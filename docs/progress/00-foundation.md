【00-foundation：独立工程与测试基础】

▶ 结果
• 状态：本阶段完成。新增后端可安装、可显式迁移、可独立启动；最终固定测试命令通过 98 项测试。
• 本轮角色为 coder，尚不替代下一轮独立审查，也不表示其他阶段已经完成。
• 全程在当前 worktree 开发，只使用合成数据和本机回环测试。未启动生产服务、未调用真实 agy／Agnes、未实施生产发布或生产迁移。

▶ 阅读与基线核对
• 已完整阅读 `docs/plans/自动开发实施计划-v1.md`、`docs/specs/创作工作台重构-Spec-v1.0.md`、`docs/api/FastAPI接口文档-v1.md`。
• 已核对 `docs/api/openapi-v1.json` 中健康操作、Health／Problem 完整 schema，以及后续账号策略和能力字段；阅读了契约检查工具。
• 本阶段开始时不存在 `backend/`、`frontend/`、`docs/progress/`，没有可复用的已完成阶段记录。
• 实际核对了旧依赖、忽略规则及旧后端 E2E 代码；旧测试访问 8080 服务和真实生成路径，本轮没有运行或修改。
• 保留进入本轮前已有的 `.gitignore` 修改、全部未提交文档、原型与工具。最终 `git diff --exit-code` 检查原 `server.py`、`index.html`、依赖及部署文件无本轮改动；未执行 commit、push、reset、clean、merge。

▶ 已实现
• `backend/` 为 src-layout 可安装 Python 包，提供 FastAPI 工厂入口和 `omniflow` 本地命令。导入、工厂构造与服务启动都不会自动建库、迁移、启动 CLI 或请求提供方。
• `Settings` 只加载显式 `OMNIFLOW_*` 配置，不自动读取 `.env`。产品文本模型固定 `gemini-3.8-flash-low`；真实提供方默认禁用，免费状态未核验、收费回退关闭、无业务配额。
• 精确 Host／Origin、显式凭据 CORS、安全响应头、服务端追踪 UUID、统一脱敏 Problem。Cookie 安全属性固定，不提供 Secure／HttpOnly 降级；当前没有账号 Cookie 签发入口。
• 默认关闭调试文档，未建立源码／素材静态根和旧接口兼容代理。生产配置拒绝 mock、开发来源、交互文档，但本轮未进行生产配置或部署。
• SQLite 显式前向迁移，版本 1 仅创建应用标记及迁移历史，不提前冒写后续业务表。每连接启用外键、有限 busy timeout、FULL 同步；使用 WAL、短写事务、迁移校验值。
• 私有数据与媒体目录 0700、数据库 0600；拒绝符号链接、公共权限目录、未知数据库、未来版本、改写历史；不自动导入旧库、修改旧目录权限或降级数据库。
• 初次迁移及追加迁移失败可事务回滚；重复迁移和并发迁移不会重复应用。WAL 设置的锁竞争只进行有限本地重试，不重新执行已提交的迁移。
• `GET /api/v1/health/live` 与 `/ready` 按设计契约提供健康状态。缺迁移、低磁盘、损坏或缺失存储时 ready 返回不含路径的 503 Problem；live 仍可表示进程存活。
• 真实回环 HTTP 冒烟工具使用自己创建的临时数据库、端口和子进程，覆盖迁移、启动、重启、错误响应头以及虚构 token 不进入访问日志，完成后回收自己的进程和临时数据。

▶ 修改文件
全部为新增文件；没有改写既有产品代码或控制器。
• `backend/.gitignore`
• `backend/pyproject.toml`
• `backend/uv.lock`
• `backend/README.md`
• `backend/src/omniflow/__init__.py`
• `backend/src/omniflow/app.py`
• `backend/src/omniflow/cli.py`
• `backend/src/omniflow/config.py`
• `backend/src/omniflow/db.py`
• `backend/src/omniflow/middleware.py`
• `backend/src/omniflow/problems.py`
• `backend/src/omniflow/api/__init__.py`
• `backend/src/omniflow/api/health.py`
• `backend/tests/conftest.py`
• `backend/tests/test_app.py`
• `backend/tests/test_cli.py`
• `backend/tests/test_config.py`
• `backend/tests/test_db.py`
• `backend/tests/test_smoke.py`
• `backend/tools/smoke_health.py`
• `docs/progress/00-foundation.md`
• 另有忽略的本地 `.venv/`、`backend/var/uv-cache`、测试临时数据和构建产物，不属于源码交付。

▶ 实际环境与安装
• uv 0.12.9，已有 CPython 3.14.4；未替换全局 Python，也未下载新 Python。
• 本地锁定组合：FastAPI 0.141.1、Starlette 1.6.0、Pydantic 2.13.5、pydantic-settings 2.15.0、Uvicorn 0.52.4、AnyIO 4.12.1、httpx2 2.12.0、pytest 9.1.1。完整精确版本及下载校验值见 `backend/uv.lock`。
• 本阶段实际执行锁定、安装与最终锁定复核，最终可复现命令为：
```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
uv sync --project backend --locked --group dev \
  --python "$(command -v python3)" --python-preference only-system
```
结果：成功，锁定文件有效，当前本地环境 30 个已安装包检查完成。

▶ 实际测试与结果
以下命令均从当前 worktree 根目录执行。

1. 固定阶段门槛：
```bash
.venv/bin/python -m pytest backend/tests -q
```
最终复跑结果：`98 passed in 2.36s`，退出码 0。覆盖安全配置、环境变量、健康 schema、Problem 脱敏、Host／Origin／CORS、未开放路径、迁移重复／并发／失败回滚／未知库／损坏／低磁盘、CLI 及真实回环 HTTP 启停与重启。

2. 代码检查：
```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
```
结果：`All checks passed!`，`17 files already formatted`。

3. 既有设计契约回归：
```bash
.venv/bin/python docs/api/test_contract.py
```
结果：40 个路径、51 个操作、64 个 schema、525 个内部引用；22 个契约示例、8 个文档 JSON 示例、1 个 SSE 帧、22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，未声称通过官方完整结构校验。

4. 独立重复运行实际 HTTP 冒烟：
```bash
.venv/bin/python backend/tools/smoke_health.py
```
结果：显式迁移、只读检查、127.0.0.1 HTTP、重启恢复与访问日志脱敏通过；脚本已结束自己创建的服务子进程。固定 pytest 中也包含该脚本的测试。

5. 并发迁移专项重复验证：通过本地 Python 的 `subprocess.run` 循环 20 次执行：
```bash
.venv/bin/python -m pytest backend/tests/test_db.py::test_concurrent_migrations_apply_once -q
```
结果：20 轮全部退出码 0；每轮包含 4 线程执行 8 次迁移，只产生一条版本记录。

6. 包构建与依赖检查：
```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
uv build --project backend --out-dir "$PWD/backend/var/dist" \
  --python "$(command -v python3)" --python-preference only-system
UV_CACHE_DIR="$PWD/backend/var/uv-cache" uv pip check --python .venv/bin/python
```
结果：sdist 与 wheel 均构建成功，安装依赖兼容。产物为 `backend/var/dist/omniflow_backend-0.1.0.tar.gz` 和 `.whl`。构建器提示缓存位于源码树内；随后用标准库 tarfile／zipfile 实际检查两种归档，sdist 21 项、wheel 13 项，均不含 `var/`、`.venv/` 或 `.env`。

7. 原站文件不变检查：
```bash
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
结果：退出码 0。最终 `git diff --check` 也通过。没有启动或切换原站服务。

▶ 测试中发现并修复的问题
• 首次依赖解析得到的 Starlette TestClient 对旧 httpx 发出弃用警告；改用其支持的 httpx2。AnyIO 4.15 的别名弃用与 Starlette 1.6 不兼容，限定到已实测 4.12.x。没有屏蔽警告或降低测试要求。
• 测试中一条直接创建的 SQLite 连接未显式关闭，Python 3.14 在测试退出时报告 ResourceWarning；修复为显式 closing，不将「测试项全绿但退出失败」计为通过。
• 并发迁移测试发现 `PRAGMA journal_mode=WAL` 的锁升级并非总遵从 busy timeout；添加仅针对该幂等设置的有限 BUSY／LOCKED 重试。没有删除并发测试，修复后连续 20 轮通过。
• 环境变量中的布尔字符串与 Pydantic Literal 直接匹配不兼容；补充先解析再验证安全常量的逻辑与正反用例，仍拒绝开启收费回退等不安全值。
• 构建归档检查的第一条临时命令把构建目录中的隐藏标记文件也当成 tar 包，命令失败；改为明确选择 `.whl` 和 `.tar.gz` 后检查通过，未修改或删除构建验收要求。

▶ 尚未实现与尚未验证
• 阶段 01 的账号、Argon2id、邀请／重置、登录撤销、CSRF 令牌、登录限流、管理员操作均未实现。当前 Origin 校验不能替代完整 CSRF 防护，安全配置常量不等于账号功能已经完成。
• 阶段 02–05 的对话隔离、SSE、素材版本、任务队列、worker、真实 CLI／媒体适配器均未实现；本阶段没有伪造这些功能的成功接口。
• 阶段 06 前端与阶段 07 E2E 尚未开始，当前没有 npm 前端测试／构建命令。本阶段只对实际两个健康操作做契约子集核对，不将全部文档操作标为已实现。
• 生产发布、生产库迁移、真实 agy／Agnes 联调、免费超额权益、Google 授权与宿主工具隔离验证均「未完成」。未请求真实媒体、未更改授权、未配置收费回退、未新增公网监听。
• HTTPS 与代理转发、浏览器账号联调、SSE 长连接、Mac／iOS 播放、长时常驻稳定性尚未验证。仅验证 Python 3.14.4 当前宿主，没有覆盖全部受支持 Python 小版本或操作系统。
• 不实施备份，不承诺灾损恢复。私有目录检查不是操作系统沙箱，也不声称可抵御具有宿主文件写权限的并发恶意替换。

▶ 下一阶段接续
• 启动与安装说明已保存 `backend/README.md`；固定测试命令仍为 `.venv/bin/python -m pytest backend/tests -q`。
• 阶段 01 从新增迁移版本 2 开始，不改已执行的版本 1；复用 Settings、Database、StrictModel、ProblemError 与安全响应边界。
• 新增业务修改操作必须实现会话／匿名 CSRF 上下文、令牌与账号权限；不能只依赖当前全局 Origin 校验。
• 账号公开策略接口需读取实际 Settings，不硬编码另一套默认。管理员只通过本地显式命令创建，不提供默认密码。
• 字段级错误可在下一阶段增加，但必须对字段名和错误文案做白名单脱敏；不可返回 FastAPI 原始 input／ctx／异常文本。
• 真正的提供方与费用闸门保留到所属阶段，不能为演示把 `free_access_verified` 直接开成可由普通环境变量伪造的信任值。

---
In brief
• What's happening：新版基础已能独立启动，98 项测试通过。
• Reason：先把保存、启动和安全边界做好，再添加账号与创作功能。
• Impact：旧网站不变；真实生成和生产发布仍未完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
