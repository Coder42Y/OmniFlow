"""本地显式管理命令；默认不开访问日志，避免未来 grant 查询参数泄露。"""

import argparse
import getpass
import hmac
import signal
import sqlite3
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from uuid import UUID

from pydantic import ValidationError

from .artifacts import ArtifactService
from .auth_models import InviteCreate
from .auth_service import AuthService
from .config import Settings
from .db import Database, StorageError
from .http_server import serve_http
from .problems import ProblemError
from .run_manager import RunManager
from .task_worker import TaskWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OmniFlow 独立后端本地工具")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="显式初始化独立存储并执行前向迁移")
    commands.add_parser("check", help="只读检查本地组件；不检查真实提供方")
    commands.add_parser("runtime-check", help="只读核对真实装配、签名证据和执行器心跳；不生成")
    commands.add_parser("purge-artifacts", help="清理已删除且到期的素材及崩溃遗留临时文件")
    admin = commands.add_parser("create-admin", help="本地显式创建管理员；仅终端隐藏输入密码")
    admin.add_argument("--username", required=True)
    invite = commands.add_parser("issue-invite", help="本机签发一次性普通用户邀请，链接只输出本次")
    invite.add_argument(
        "--no-expiry", action="store_true", help="不设到期时间；使用一次或撤销后失效"
    )
    revoke = commands.add_parser("revoke-invite", help="本机撤销未消费的普通用户邀请")
    revoke.add_argument("invitation_id", type=UUID)
    reusable = commands.add_parser(
        "make-invite-reusable", help="本机显式允许指定邀请重复注册普通账号"
    )
    reusable.add_argument("invitation_id", type=UUID)
    manager = commands.add_parser(
        "run-manager", help="独立消费持久文字队列；真实运行需显式受保护配置"
    )
    manager.add_argument("--once", action="store_true", help="最多领取一轮后退出")
    manager.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    worker = commands.add_parser(
        "media-worker", help="独立消费持久媒体任务；真实提供方默认拒绝调用"
    )
    worker.add_argument("--once", action="store_true", help="最多执行一次提交、续查或保存后退出")
    serve = commands.add_parser("serve", help="仅监听 127.0.0.1，不开启访问日志或代理头信任")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == "serve" and not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024–65535 之间")
    stack = ExitStack()
    try:
        settings = Settings()
        database = Database(settings)
        if args.command == "migrate":
            print(f"迁移完成，当前版本 {database.migrate()}。未导入旧站数据。")
        elif args.command == "issue-invite":
            data = InviteCreate(expires_in_seconds=None) if args.no_expiry else InviteCreate()
            print(AuthService(database).issue_invite_locally(data).model_dump_json())
        elif args.command == "make-invite-reusable":
            AuthService(database).make_invite_reusable_locally(str(args.invitation_id))
            print("指定邀请已允许重复注册普通账号；未更改其他邀请及现有账号。")
        elif args.command == "revoke-invite":
            result = AuthService(database).revoke_invite_locally(str(args.invitation_id))
            print(f"邀请状态：{result.status}。没有删除用户或修改账号权限。")
        elif args.command == "purge-artifacts":
            # 低磁盘时也允许主动删除后的清理；不触发自动删可用作品。
            count = ArtifactService(database).cleanup()
            print(f"已清理 {count} 个到期删除作品；未删除可用作品，不提供备份恢复。")
        elif args.command == "check":
            database.check_ready()
            print("本地组件就绪；未验证真实提供方。")
        elif args.command == "runtime-check":
            from .media_provider import check_generation
            from .runtime import get_runtime

            database.check_ready()
            runtime = get_runtime(database)
            print("运行配置摘要：" + runtime.fingerprint)
            failed = False
            for kind in ("text", "image", "ai_video", "local_motion"):
                if kind not in runtime.config.enabled_kinds:
                    print(kind + ": disabled（未装配）")
                    continue
                try:
                    runtime.check_materials("text" if kind == "text" else "media")
                    check_generation(runtime.media, kind)
                    print(kind + ": ready（材料及心跳通过，不代表真实调用已成功）")
                except (ProblemError, ValueError, OSError) as exc:
                    print(
                        kind
                        + ": "
                        + (exc.code if isinstance(exc, ProblemError) else "PROVIDER_UNAVAILABLE")
                    )
                    failed = True
            if failed:
                return 1
        elif args.command == "media-worker":
            # 已受理任务低磁盘时仍可续查；保存步骤单独检查磁盘，不能因 ready 失败丢任务。
            with database.connect(readonly=True) as connection:
                database._history(connection, allow_empty=False)
            worker = TaskWorker(database)
            if settings.provider_mode == "real":
                from .runtime import get_runtime

                stack.enter_context(get_runtime(database).worker("media"))
            if args.once:
                worker.execute_next()
            else:

                def stop_worker(signum, frame):
                    worker.shutdown.set()

                previous = {
                    sig: signal.signal(sig, stop_worker) for sig in (signal.SIGINT, signal.SIGTERM)
                }
                try:
                    worker.serve()
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
        elif args.command == "run-manager":
            database.check_ready()
            if settings.provider_mode == "real":
                from .runtime import get_runtime

                stack.enter_context(get_runtime(database).worker("text"))
            if args.once:
                manager = RunManager(database)
                try:
                    manager.execute_next()
                finally:
                    manager.close()
            else:
                managers = [RunManager(database) for _ in range(args.workers)]

                def stop_managers(signum, frame):
                    for manager in managers:
                        manager.shutdown.set()

                previous = {
                    sig: signal.signal(sig, stop_managers)
                    for sig in (signal.SIGINT, signal.SIGTERM)
                }
                try:
                    with ThreadPoolExecutor(max_workers=args.workers) as pool:
                        futures = [pool.submit(manager.serve) for manager in managers]
                        try:
                            for future in as_completed(futures):
                                future.result()
                        finally:
                            stop_managers(None, None)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
        elif args.command == "create-admin":
            database.check_ready()
            if not sys.stdin.isatty():
                print(
                    "创建管理员须在交互终端输入密码；不接受参数、环境或管道密码。", file=sys.stderr
                )
                return 1
            with warnings.catch_warnings():
                # 无法关闭回显时直接拒绝，不能退回可见密码输入。
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("管理员密码（隐藏输入）：")
                confirmation = getpass.getpass("再次输入密码：")
            if not hmac.compare_digest(password.encode(), confirmation.encode()):
                print("两次密码不一致；未创建账号。", file=sys.stderr)
                return 1
            AuthService(database).create_admin(args.username, password)
            print("管理员已创建；未修改任何现有账号。")
        else:
            serve_http(settings, args.port)
    except ProblemError:
        if args.command == "create-admin":
            print("账号创建失败：用户名不可用或字段不符合公开账号策略。", file=sys.stderr)
        else:
            print("操作被拒绝：请核对独立存储与当前资源状态；未自动修复。", file=sys.stderr)
        return 1
    except (
        ValidationError,
        ValueError,
        OSError,
        sqlite3.Error,
        StorageError,
        EOFError,
        getpass.GetPassWarning,
    ):
        print("操作失败：请核对独立存储权限、配置及迁移版本；未自动修复。", file=sys.stderr)
        return 1
    finally:
        stack.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
