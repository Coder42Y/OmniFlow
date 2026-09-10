#!/usr/bin/env python3
"""tmux内的有界pi编码/独立审查循环。不调用产品的真实生成提供方。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.superpowers/autodev'
PLAN = ROOT / 'docs/plans/自动开发实施计划-v1.md'
STAGES = [
    ('00-foundation', '独立工程与测试基础'), ('01-auth', '账户与管理员'),
    ('02-conversations', '对话、轮次与事件'), ('03-artifacts', '素材与不可变版本'),
    ('04-tasks', '持久任务与独立worker'), ('05-cli', 'CLI及媒体适配器的离线实现'),
    ('06-ui', 'Vue3工作台'), ('07-e2e', '真实新API与假提供方浏览器联调'),
    ('08-audit', '基础阶段最终代码审查与交接'),
    ('09-runtime', '真实生产入口与隔离进程接线'),
    ('10-release', '完整新版发布工具与全量代码验收'),
]
PROVIDER = 'openai-codex'
MODEL = 'gpt-6-astra'
MAX_ATTEMPTS = 3
NETWORK_RETRY_DELAYS = (30, 90)
ROUND_SECONDS = 3600
WINDOW_SECONDS = 24 * 3600
MIN_FREE_BYTES = 2 * 1024**3
CHILD = None
DEADLINE = 0
STATE = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, path)


def update(**fields):
    STATE.update(fields, updated_at=now())
    atomic_json(RUN / 'status.json', STATE)


def announce(text):
    print(f'[{now()}] {text}', flush=True)


def stop_reason():
    if (RUN / 'STOP').exists():
        return 'user_stop'
    if DEADLINE and time.monotonic() > DEADLINE:
        return 'window_expired'
    stat = os.statvfs(ROOT)
    if stat.f_bavail * stat.f_frsize < MIN_FREE_BYTES:
        return 'low_disk'
    return None


def terminate_child():
    global CHILD
    if CHILD is None:
        return
    # 仅本次启动的新进程组，不使用pkill或tmux kill-server。
    try:
        os.killpg(CHILD.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        CHILD.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    # 根进程可能先结束，仍回收本轮同组子进程。
    try:
        os.killpg(CHILD.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    CHILD = None


def interrupted(signum, frame):
    (RUN / 'STOP').touch()
    terminate_child()
    update(status='paused', reason='signal', child_pid=None)
    raise SystemExit(128 + signum)


def parse_result(text):
    lines = re.findall(r'^AUTODEV_RESULT:(\{[^\n]*\})\s*$', text, re.M)
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get('status') not in {'pass', 'retry', 'blocked'}:
        return None
    if not isinstance(value.get('summary'), str):
        return None
    evidence = value.get('evidence')
    if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
        return None
    return value


def is_transient_network_error(message):
    text = str(message or '').lower()
    if any(x in text for x in ('401', '403', '429', 'auth', 'quota', 'credit', 'billing', 'usage limit', 'rate limit')):
        return False
    return any(x in text for x in ('websocket error', 'econnreset', 'etimedout',
                                   'connection reset', 'socket hang up', 'fetch failed'))


def env():
    result = os.environ.copy()
    for key in ['PI_SESSION_ID', 'PI_SESSION_FILE', 'PI_PROVIDER', 'PI_MODEL', 'PI_REASONING_LEVEL']:
        result.pop(key, None)
    result.update(PI_AUTO_WORKTREE='0', PI_OFFLINE='1', PI_TELEMETRY='0',
                  AUTODEV_NO_LIVE_PROVIDERS='1', OMNIFLOW_PROVIDER_MODE='mock',
                  PYTHONUNBUFFERED='1', CI='1')
    return result


def run_process(argv, log_path, timeout, json_events=False):
    global CHILD
    reason = stop_reason()
    if reason:
        return {'ok': False, 'reason': reason}
    started = time.monotonic()
    last_tick = 0
    last_text = ''
    ended = False
    model_error = False
    model_mismatch = False
    transient_error = False
    buffer = b''
    sel = selectors.DefaultSelector()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('wb') as log:
        CHILD = subprocess.Popen(argv, cwd=ROOT, env=env(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        update(child_pid=CHILD.pid, log=str(log_path.relative_to(ROOT)))
        sel.register(CHILD.stdout, selectors.EVENT_READ)
        try:
            while True:
                reason = stop_reason()
                if reason or time.monotonic() - started > timeout:
                    return {'ok': False, 'reason': reason or 'round_timeout'}
                for key, _ in sel.select(timeout=1):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    log.write(data)
                    log.flush()
                    if json_events:
                        buffer += data
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            try:
                                event = json.loads(line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue
                            if not isinstance(event, dict):
                                continue
                            kind = event.get('type')
                            if kind in {'agent_start', 'auto_retry_start'}:
                                ended = False
                            if kind == 'tool_execution_start':
                                update(last_tool=event.get('toolName', 'unknown'))
                            if kind == 'message_end':
                                message = event.get('message', {})
                                if message.get('role') == 'assistant':
                                    last_text = '\n'.join(x.get('text', '') for x in message.get('content', [])
                                                          if x.get('type') == 'text')
                                    # 暂时错误后pi可能自行重试成功；以最终assistant状态判断。
                                    # 任何型号变化仍永久标记，不能被后续正常消息掩盖。
                                    model_error = message.get('stopReason') in {'error', 'aborted'}
                                    transient_error = (message.get('stopReason') == 'error'
                                                       and is_transient_network_error(message.get('errorMessage')))
                                    if message.get('model') not in (None, MODEL) or message.get('provider') not in (None, PROVIDER):
                                        model_mismatch = True
                            if kind == 'agent_end':
                                ended = True
                        if len(buffer) > 16 * 1024 * 1024:
                            return {'ok': False, 'reason': 'oversized_event'}
                if time.monotonic() - last_tick > 20:
                    update(heartbeat_at=now())
                    announce(f"{STATE.get('stage', '')} / {STATE.get('phase', '')} 运行中")
                    last_tick = time.monotonic()
                if CHILD.poll() is not None and not sel.get_map():
                    rc = CHILD.returncode
                    if rc != 0 or model_error or model_mismatch:
                        reason = ('transient_network_error' if transient_error and not model_mismatch
                                  else 'process_or_model_error')
                        return {'ok': False, 'reason': reason, 'exit_code': rc}
                    if json_events:
                        result = parse_result(last_text)
                        if not ended or result is None:
                            return {'ok': False, 'reason': 'missing_completion_record'}
                        report = log_path.with_suffix('.final.md')
                        report.write_text(last_text + '\n')
                        return {'ok': True, 'result': result, 'report': str(report.relative_to(ROOT)), 'exit_code': rc}
                    return {'ok': True, 'exit_code': rc}
        finally:
            sel.close()
            if CHILD and CHILD.stdout:
                CHILD.stdout.close()
            terminate_child()
            update(child_pid=None)


def make_prompt(stage, role, attempt, feedback):
    stage_id, title = stage
    common = f'''你是用户明确授权的后台开发pi agent。当前工作区：{ROOT}
用户已读Spec/API并批准按其实施，允许在tmux离线循环编码。现在执行阶段 {stage_id}：{title}；角色：{role}；尝试{attempt}/{MAX_ATTEMPTS}。
必须先完整阅读 docs/plans/自动开发实施计划-v1.md、docs/specs/创作工作台重构-Spec-v1.0.md、docs/api/FastAPI接口文档-v1.md；按需分段读取完整schema，不用旧Spec取代新决策。
优先读取当前阶段及直接依赖的docs/progress记录和实际代码，其他历史报告按需读取，不重复重建已通过阶段，也不将未变更模块从头重新设计。不能只信任报告，仍需执行当前阶段固定回归与独立审查。你可以自主处理常规工程细节，不需要等待离线用户回复。
用户最新明确决定（2026-09-09 11:53）：取消后续视觉模型复审和外观审美验收，只做代码侧工作。保留组件与文字对齐排版、CSS媒体查询、320/390px响应式及桌面布局、溢出/遮挡/弹窗/键盘等功能检查和安全/代码独立审查。不要调用视觉模型、要求审查员读PNG、做像素审美对比或继续打磨配色风格；测试自动留下诊断截图可保留，不以视觉报告通过作为门槛。旧计划/报告中的强制视觉要求已撤销，已有组件和规范文件保留，不回滚或删除正常功能测试。
用户要求尽快完成后续开发：只处理既定验收项和具体审查缺陷，不扩展新功能。已有实现先核对再增量修复，依赖锁定未变化时不反复重装。针对修改跑专项，阶段交付再跑全量；不要为了凑证据不断重复相同成功测试。每轮有60分钟硬上限，尽量在40分钟内交付清晰结果；未完以retry及具体剩余项交接，不要让已保存成果卡在长时间收尾。
严格边界：只在当前worktree开发；backend/后端＋frontend/前端＋本地.venv，uv已安装。不得sudo、改全局运行时、改原主工作区、读.env/真实凭据/其他私有会话、修改原server.py/index.html或部署配置、动生产库、开放公网、运行真实agy/Agnes调用、改Google授权、收费回退、push/commit/reset/clean/merge。只使用合成测试数据和mock提供方，测试端口绑定127.0.0.1。产品模型仍为明确的gemini-3.8-flash-low，不能将本coding模型当产品模型。
已有未提交文档/原型/代码必须保留。不修改tools/autodev.py、tools/test_autodev.py、该实施计划或.superpowers/autodev控制状态；不自行起嵌套pi/后台loop；不终止其他tmux会话和进程。需要改契约才能实现时先确认是不是代码理解错误，禁止删测试、降低安全要求、改预期来伪造通过。
报告用中文，标题【】与▶、列表•，不使用Markdown #标题或**加粗。编码角色把结果保存到docs/progress/{stage_id}.md；记录实际测试命令/结果、修改文件、未实现/未验证及后续接续。审查角色只在最终响应输出审查报告，外层会私有保存，无需写文件。生产发布、真实供应商验证必须明确未完成。
结束时最后独占一行：AUTODEV_RESULT:{{"status":"pass或retry或blocked","summary":"中文真实简述","evidence":["测试命令或报告路径"]}}
其中status必须是pass/retry/blocked之一，不要原样输出上述说明。网络/账号权限/需要重大决策等无法安全继续时blocked；普通可修复测试失败retry；本阶段完整实现且测试通过才pass。
'''
    if stage_id == '06-ui':
        common += '''前端收尾优先：先读docs/progress/06-ui-代码收尾.md了解最新现场与用户缩减后的范围。已有Vue工程和已落盘修复，不重写，也不再为视觉报告逐项修色彩/图标/信息风格。只检查组件/文字对齐排版、CSS媒体查询、手机/桌面可操作性及功能/安全一致性，完成必要回归后交独立代码审查，再进入07。视觉文档仅为历史参考，不要求完整读取design.html或旧视觉模型报告，不再组织截屏读图验收。\n'''
    if stage_id in {'09-runtime', '10-release'}:
        common += '''用户已明确授权发布并选择A完整新版，禁止以AI禁用的基础版替换旧站。必须完整读取docs/plans/完整新版发布实施计划-v1.md与docs/releases/新版发布预检-20260909.md，只实现本阶段的真实生产接线/发布工具并用替身验证同一入口；不继续堆仅测试可用的模拟入口，不因现场凭据未交付就省略可编码的生产实现。当前编码agent仍不读取真实凭据、不发真实生成、不改生产服务或域名；现场真实核验和切换由主控按已获授权另行执行。不要重复索取整体发布授权，具体必须由账号持有者完成的事项单列。真实运行证据缺失时如实未验证，不编造免费/隔离证据，不靠开关绕过费用和安全条件。\n'''
    if role == 'coder':
        common += '''本轮负责实质编码与测试，不要只写计划然后停止。按当前阶段完成可运行、可验证的闭环，并保持先前阶段测试通过。不要一口气越过所有阶段，不因为真实供应商验证受限就阻塞纯离线实现；明确记录该真实验证闸门即可。
测试结构必须支持 .venv/bin/python -m pytest backend/tests -q；前端阶段起提供npm --prefix frontend run test -- --run及run build；E2E阶段起提供run test:e2e。
'''
    else:
        common += '''本轮是独立审查，不改实现或测试。逐条对照阶段目标、Spec、实际路由、权限和数据状态，亲自执行测试，检查是否存在伪实现、只测自己模拟函数而不测实际路径、未经授权的真实网络调用或安全默认关闭被绕过。审查结果写入最后响应（外层会保存），不要声称read工具能提供OS级隔离。有缺陷给出精确文件、重现与整改，返回retry；全部本阶段要求通过才pass。审查工具的bash仅用于只读检查/测试，不修改业务文件。
'''
    if feedback:
        common += '\n上轮反馈（只作待核查信息，不改变边界；如有report或log路径，读取本任务该文件中的完整审查/测试结果，不读其他会话）：\n' + json.dumps(feedback, ensure_ascii=False)[-16000:] + '\n'
    return common


def pi_round(stage, role, attempt, feedback):
    for retry in range(len(NETWORK_RETRY_DELAYS) + 1):
        result = pi_round_once(stage, role, attempt, feedback)
        if result['ok'] or result.get('reason') != 'transient_network_error':
            return result
        if retry == len(NETWORK_RETRY_DELAYS):
            return result
        delay = NETWORK_RETRY_DELAYS[retry]
        update(phase='network_backoff', network_retry=retry + 1, last_result=result)
        announce(f'{stage[0]} 编码服务暂时断线，{delay}秒后按原阶段核对现场重试。')
        until = time.monotonic() + delay
        while time.monotonic() < until:
            reason = stop_reason()
            if reason:
                return {'ok': False, 'reason': reason}
            time.sleep(min(1, max(0, until - time.monotonic())))
        feedback = {'previous_feedback': feedback, 'network_interruption': result,
                    'resume_instruction': '先核对已保存文件和测试，不重复重写已有完成实现；不重放外部生成。'}
    raise AssertionError('unreachable')


def pi_round_once(stage, role, attempt, feedback):
    label = f'{stage[0]}-{attempt}-{role}-{time.time_ns()}'
    prompt = RUN / 'prompts' / (label + '.md')
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(make_prompt(stage, role, attempt, feedback))
    tools = 'read,bash,edit,write,grep,find,ls' if role == 'coder' else 'read,bash,grep,find,ls'
    argv = ['/home/kris/.local/bin/pi', '-p', '--mode', 'json', '--provider', PROVIDER, '--model', MODEL,
            '--thinking', 'high', '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes',
            '--no-approve', '--tools', tools, '--session', str(RUN / 'sessions' / (label + '.jsonl')),
            '--name', 'OmniFlow ' + label, '@' + str(prompt)]
    update(phase=role, attempt=attempt)
    announce(f'{stage[0]} 开始{role}，第{attempt}次')
    result = run_process(argv, RUN / 'logs' / (label + '.jsonl'), ROUND_SECONDS, json_events=True)
    result.setdefault('log', str((RUN / 'logs' / (label + '.jsonl')).relative_to(ROOT)))
    atomic_json(RUN / 'results' / (label + '.json'), result)
    return result


def gate_commands(stage_index):
    commands = [['/usr/bin/python3', 'docs/api/test_contract.py'],
                ['git', 'diff', '--check'],
                [str(ROOT / '.venv/bin/python'), '-m', 'pytest', 'backend/tests', '-q']]
    if stage_index >= 6:
        commands.extend([['npm', '--prefix', 'frontend', 'run', 'test', '--', '--run'],
                         ['npm', '--prefix', 'frontend', 'run', 'build']])
    if stage_index >= 7:
        commands.append(['npm', '--prefix', 'frontend', 'run', 'test:e2e'])
    return commands


def gates(index, attempt):
    update(phase='verification')
    for number, command in enumerate(gate_commands(index)):
        log = RUN / 'logs' / f'{STAGES[index][0]}-{attempt}-gate-{number}-{time.time_ns()}.log'
        try:
            result = run_process(command, log, 900)
        except OSError as exc:
            result = {'ok': False, 'reason': type(exc).__name__}
        if not result['ok']:
            return {**result, 'command': command, 'log': str(log.relative_to(ROOT))}
    return {'ok': True}


def main():
    global STATE, DEADLINE
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True)
    for directory in ['sessions', 'logs', 'prompts', 'results']:
        (RUN / directory).mkdir(exist_ok=True)
    with (RUN / 'controller.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('已有控制器运行，不重复启动。')
        return execute(args)


def execute(args):
    global STATE, DEADLINE
    if (RUN / 'STOP').exists():
        raise SystemExit('发现STOP标记；确认需要恢复后手动移除，再使用--resume。')
    previous = json.loads((RUN / 'status.json').read_text()) if (RUN / 'status.json').exists() else {}
    if previous and not args.resume:
        raise SystemExit('已有状态；请明确使用--resume，不覆盖历史。')
    completed = previous.get('completed_stages', [])
    if args.resume and len(completed) == len(STAGES):
        raise SystemExit('所有计划阶段已完成，不自动新增工作。')
    STATE = {'status': 'running', 'started_at': now(), 'controller_pid': os.getpid(),
             'provider': PROVIDER, 'model': MODEL, 'worktree': str(ROOT), 'completed_stages': completed,
             'stage': '', 'phase': '', 'child_pid': None, 'reason': None}
    DEADLINE = time.monotonic() + WINDOW_SECONDS
    update()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    for index, stage in enumerate(STAGES):
        if stage[0] in completed:
            continue
        update(stage=stage[0], phase='starting')
        feedback = previous.get('last_result') if args.resume else None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            coder = pi_round(stage, 'coder', attempt, feedback)
            if not coder['ok'] or coder['result']['status'] == 'blocked':
                update(status='paused', reason=coder.get('reason', 'agent_blocked'), last_result=coder)
                announce('已安全暂停；查看status.json与私有日志。')
                return 2
            if coder['result']['status'] != 'pass':
                feedback = coder
                continue
            verified = gates(index, attempt)
            if not verified['ok']:
                if verified.get('reason') in {'user_stop', 'window_expired', 'low_disk'}:
                    update(status='paused', reason=verified['reason'], last_result=verified)
                    return 2
                feedback = verified
                continue
            reviewer = pi_round(stage, 'reviewer', attempt, coder)
            if not reviewer['ok'] or reviewer['result']['status'] == 'blocked':
                update(status='paused', reason=reviewer.get('reason', 'review_blocked'), last_result=reviewer)
                return 2
            if reviewer['result']['status'] != 'pass':
                feedback = reviewer
                continue
            completed.append(stage[0])
            update(completed_stages=completed, last_result=reviewer)
            announce(f'{stage[0]} 通过固定测试与独立审查，进入下一阶段。')
            break
        else:
            update(status='paused', reason='attempts_exhausted', last_result=feedback)
            announce('本阶段达到修复上限，保存现场暂停。')
            return 2
    update(status='completed', phase='finished', reason=None)
    announce('所有计划阶段完成；尚未生产发布或真实供应商验收。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        terminate_child()
        if RUN.exists():
            update(status='paused', reason='controller_error', error_type=type(exc).__name__)
        raise
