【01-auth：账户与管理员】

【本轮恢复接续：实质修复与重新验证】

▶ 当前结论
• 本轮仍为阶段 01 coder，未推进其他阶段。重新阅读三份完整指定文档、已有阶段记录、实际账号／管理员／数据库／安全边界代码，以及 OpenAPI 账号相关操作和全部递归引用 schema；未将旧报告的通过声明直接当作验收。
• 进入本轮先实际运行固定后端命令，结果 `162 passed in 12.06s`。随后发现并修复管理员分页游标缺少日期校验的问题，新增 16 项离线回归，最终 `178 passed in 14.38s`。账号阶段可交独立审查；不是正式发布或完整创作工作台完成。
• 下文「前次编码记录」完整保留，162 项是历史证据，本轮最终结果以本节 178 项为准。没有重写迁移历史、依赖、契约、控制器、实施计划或已有原型。

▶ 本轮实际修改
• `backend/src/omniflow/auth_service.py`：管理员用户／邀请列表读取不透明游标时，原先只限制时间字符串长度；含孤立 Unicode 代理字符的合成游标可到达 SQLite 并产生 500，其他坏日期被当作普通排序值接受。现按服务实际签发的 UTC 微秒格式解析并精确回验日期及规范 UUID，在数据库查询前统一拒绝为 `400 VALIDATION_ERROR`，不回显游标。
• `backend/tests/test_auth.py`：新增 10 项坏游标测试，覆盖用户／邀请两个列表的代理字符、坏日期、不存在的日期、异时区、空字符；另有 6 项验证同时间戳分页无重漏、游标不能代替当前管理员权限、哈希计算期间撤销邀请／重发重置的竞争，以及重置／停用审计失败时密码、状态、授权代数、旧登录与一次性凭据全部回滚。
• `docs/progress/01-auth.md`：追加本轮证据，保留先前实现记录与未验证清单。没有修改其他源码；本地测试临时数据不作为交付。

▶ 先失败、再修复的证据
```bash
.venv/bin/python -m pytest backend/tests/test_auth.py -q -k malformed_cursor_timestamp
```
• 修复前 `10 failed, 54 deselected in 1.50s`：2 项代理字符请求实际 500，8 项坏时间请求实际 200；不是凭推测新增校验。随后修改实现，未删除测试、改变这些预期或放宽安全契约。

▶ 本轮最终测试命令与结果
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_health.py
```
• 后端：`178 passed in 14.38s`，基础阶段仍通过；ruff：`All checks passed!`；格式检查：`24 files already formatted`。
• 契约：40 路径、51 操作、64 schema、525 内部引用，22 契约示例／8 文档 JSON 示例／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称完成官方全量校验。
• 健康冒烟：显式迁移、只读检查、127.0.0.1 HTTP、重启恢复及访问日志脱敏通过；脚本结束自己创建的子进程。账号仍使用 HTTPS URL 的离线 TestClient，不等于真实浏览器 HTTPS 联调。
• 通过本地 Python `subprocess.run(..., check=True)` 连续 5 次执行下列同一命令，每次 `8 passed, 72 deselected`，全部退出码 0；首轮 1.79s，其余各 1.44s。覆盖先前 6 项竞态及本轮新增撤销／重发交错，不启动后台循环或其他 agent。
```bash
.venv/bin/python -m pytest backend/tests/test_auth.py backend/tests/test_auth_invariants.py -q \
  -k 'race or concurrent or verified_before_reset or revoked_during_password_hash'
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 空白和原站／部署文件差异检查均通过。未重新安装或构建包，本轮没有依赖变更，不把前次构建命令列为本轮实测。

▶ 未实现、未验证与接续
• 原有账号闭环、本地显式管理员创建、Cookie／CSRF、Argon2id、邀请与重置、登录撤销、管理员状态与本地开关均保留并重新测试；当前仅完善阶段 01，不新增对话、任务或媒体接口。
• 下一步先独立审查本阶段，再进入阶段 02；后续服务写事务继续复核身份。SSE 撤销关流、媒体授权绑定 `auth_epoch`、任务入队／提交复核暂停开关依然分别由阶段 02／03／04 接入验证。
• 前端与浏览器 E2E、真实 agy／Agnes、真实免费权益、宿主隔离、生产发布与生产迁移均「未完成」。未读取真实凭据、未改 Google 授权、未调用真实供应商、未开放公网；产品文本型号仍为 `gemini-3.8-flash-low`。

【前次编码记录（保留历史证据）】

▶ 结果与边界
• 状态：本阶段实现与离线测试通过。新增 17 个账号／管理员操作；固定后端测试最终为 `162 passed in 12.24s`，包括此前基础阶段测试和 64 项新增账号测试。
• 本轮为 coder，不替代独立审查。仅在当前隔离 worktree 修改 `backend/` 与阶段说明，依赖安装在本地 `.venv`；没有修改原站源码、部署配置、自动开发控制器或实施计划。
• 生产发布、生产迁移、真实 agy／Agnes 联调、真实免费权益与宿主工具隔离验证均「未完成」。未读取真实凭据、未调用真实供应商、未改 Google 授权、未配置收费回退、未开放公网。
• 账号闭环使用离线 TestClient 的 HTTPS URL 和临时 SQLite；真实回环 HTTP 冒烟仅验证健康服务，不冒充浏览器 HTTPS 账号验收。

▶ 阅读与实际基线
• 已完整阅读 `docs/plans/自动开发实施计划-v1.md`、`docs/specs/创作工作台重构-Spec-v1.0.md`、`docs/api/FastAPI接口文档-v1.md`。
• 已完整读取 OpenAPI 中账号／管理员操作及其引用的请求、响应、Problem、分页、鉴权 scheme；同时读取管理员任务故障 schema，但没有提前虚构任务实现。
• 已完整读取 `docs/progress/00-foundation.md`，并实际核对基础配置、数据库迁移、ASGI 边界、错误处理、CLI、依赖与测试。此前并无账号表、Cookie 签发或 CSRF 令牌实现，未把报告中的安全常量当作已完成账号功能。
• 保留进入本轮前已有的未提交文档、原型、代码和 `.gitignore` 修改。未执行 commit、push、reset、clean、merge，也未启动其他 agent 或后台循环。

▶ 已实现
• 追加迁移版本 2：users、invites、login_sessions、csrf_contexts、password_reset_tokens、auth_rate_limits、audit_events、generation_policy。版本 1 的定义和校验值未改写；v1→v2 升级实测保留合成数据与完整旧迁移记录。
• 用户名去两端 ASCII 空格并仅将 ASCII 大写转小写，校验 3–32 位字母／数字／下划线；拒绝 Unicode 折叠成 ASCII 的混淆输入。密码不 trim、不转大小写、不截断，按 `/auth/policy` 实际 Settings 校验长度。
• Argon2id 使用 19 MiB、2 次、并行度 1，随机盐；最多 4 个并行密码计算。不存在账号也执行等成本的随机 dummy hash 校验；没有默认账号或默认密码。
• Cookie 固定 `__Host-`、HttpOnly、Secure、SameSite=Lax、Path=/、无 Domain。高熵 Cookie／邀请／重置仅保存用途隔离的 SHA-256 校验值；CSRF 从 Cookie 秘密派生，数据库不存明文 Cookie／CSRF／完整链接。
• 匿名 CSRF 引导、精确 Origin、令牌匹配、上下文有效期与重复 Cookie／头拒绝。登录／注册事务消费原上下文并轮换登录与 CSRF；匿名上下文不能访问私有接口。
• 邀请可重复校验但只可注册消费一次；创建账号、消费邀请、创建登录和审计为同一短写事务。用户名冲突／审计故障回滚，不吞掉邀请。邀请过期、已用、撤销、不存在统一 TOKEN_INVALID。
• 登录错误账号／密码统一 INVALID_CREDENTIALS；仅密码正确时报告 ACCOUNT_DISABLED。密码验证在写锁外完成，写事务重新核对密码摘要、账号代数和状态，防止重置期间的旧密码登录重新建立有效会话。
• 注销撤销当前登录；停用或成功重置撤销全部旧登录并增加 `users.auth_epoch`，重新启用不复活旧登录。密码重置一次性消费、不自动登录；重发使同用户旧重置凭据失效，邀请／重置用途不能混用。
• 本地显式 `create-admin --username ...` 命令：只允许交互终端两次隐藏输入密码，拒绝管道／密码参数，不能隐藏输入时直接失败；不自动迁移、不提升既有用户、不覆盖密码。
• 管理员账号元数据与邀请分页、邀请签发／撤销、用户启用／停用、人工核验后重置签发、本地生成开关读取／局部修改。管理员角色在依赖及写事务复核；不能停用最后一名启用的管理员，并发互斥实测通过。
• 链接凭据放网页 fragment，仅签发响应显示一次，列表不返回历史链接。重置核验方式持久保存；自由 note 接受但不保存／日志记录，避免误填秘密。核验方式记录不等于真的完成了人工核验。
• 生成开关持久化，拒绝任意 model、key、额度、收费回退或非布尔开关；服务提供 `require_generation_allowed` 供后续入队／提交事务复用。恢复本地开关不改变 `free_access_verified=False`，产品文本型号仍固定 `gemini-3.8-flash-low`。
• SQLite 原子防爆破计数跨进程／重启生效：默认 300 秒窗口、表单每实际来源 60 次、登录每规范化用户名 10 次；429 带 Retry-After。忽略伪造 X-Forwarded-For；不实现每日创作业务额度。
• Problem 统一脱敏，字段列表仅使用固定字段白名单与固定文案，不返回原始 input／ctx／异常消息／未知字段名。所有响应保持 no-store、追踪 ID 和 no-referrer。当前启动命令继续禁用访问日志与代理头信任。

▶ 修改文件
新增：
• `backend/src/omniflow/auth_schema.py`
• `backend/src/omniflow/auth_models.py`
• `backend/src/omniflow/auth_security.py`
• `backend/src/omniflow/auth_service.py`
• `backend/src/omniflow/api/auth.py`
• `backend/tests/test_auth.py`
• `backend/tests/test_auth_invariants.py`
• `docs/progress/01-auth.md`

更新：
• `backend/src/omniflow/config.py`、`db.py`、`app.py`、`cli.py`、`problems.py`
• `backend/pyproject.toml`、`backend/uv.lock`、`backend/README.md`
• `backend/tests/test_app.py`、`backend/tests/test_cli.py`、`backend/tests/test_db.py`

本地 `.venv`、`backend/var/` 测试数据、缓存和构建归档不属于源码交付。

▶ 实际安装与测试证据
所有命令从本 worktree 根目录执行，仅使用合成账号和临时数据。

1. 锁定安装复核：
```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
uv sync --project backend --locked --group dev \
  --python "$(command -v python3)" --python-preference only-system
```
• 成功，36 个锁定包解析完成、34 个已安装包检查完成；新增 argon2-cffi 25.1.0、argon2-cffi-bindings 26.1.0、cffi 2.1.1、pycparser 3.0。未替换全局 Python；当前实测 Python 3.14.4。

2. 固定阶段门槛，最终复跑：
```bash
.venv/bin/python -m pytest backend/tests -q
```
• `162 passed in 12.24s`，退出码 0。包含跨账号权限、匿名／登录 CSRF、Cookie 安全与轮换、密码错误与停用不泄露状态、一次性消费、密码重置／注销／停用撤销、分页、字段与日志脱敏、防爆破、开关持久化、本地管理员命令、v1 升级和事务失败回滚。
• 契约检查不是唯一证据：实际 FastAPI 响应通过设计 schema 验证，账号／管理员 operationId、安全 AND／OR 声明、请求字段及成功响应引用对齐设计子集。

3. 竞态专项，实际通过 Python subprocess 串行重复以下命令 10 次：
```bash
.venv/bin/python -m pytest backend/tests/test_auth.py \
  backend/tests/test_auth_invariants.py -q \
  -k 'race or concurrent or verified_before_reset'
```
• 每轮 6 项通过，10 轮均退出码 0：同邀请码并发注册、同用户名不同邀请、重置消费竞争、同匿名上下文并发登录、旧密码验证与重置交错、两管理员并发自停用。测试使用 Barrier／Event 安排关键交错，不只靠随机时序。
• 该专项执行时另有 57 项未选择；随后新增隐藏输入拒绝测试，最终全量为 162 项，不将 deselected 项算成通过。

4. 代码检查：
```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
```
• `All checks passed!`，`24 files already formatted`。没有屏蔽警告，pytest 仍将警告视为错误。

5. 文档契约回归：
```bash
.venv/bin/python docs/api/test_contract.py
```
• 40 个路径、51 个操作、64 个 schema、525 个内部引用；22 个契约示例、8 个文档 JSON 示例、1 个 SSE 帧、22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称完整官方结构校验已完成。

6. 实际回环健康冒烟和依赖检查：
```bash
.venv/bin/python backend/tools/smoke_health.py
UV_CACHE_DIR="$PWD/backend/var/uv-cache" uv pip check --python .venv/bin/python
```
• 显式迁移、只读检查、127.0.0.1 HTTP、重启恢复和访问日志脱敏通过，脚本自行回收子进程；已安装 34 个包兼容。

7. 包构建与归档检查：
```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
uv build --project backend --out-dir "$PWD/backend/var/dist" \
  --python "$(command -v python3)" --python-preference only-system
```
• sdist 和 wheel 构建成功。构建器仍提示缓存位于源码树内；随后使用标准库 tarfile／zipfile 检查，sdist 28 项、wheel 18 项，均包含账号实现且不含 var、.venv 或 .env。

8. 原站与空白检查：
```bash
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 均退出码 0；未修改原站或部署文件。

▶ 测试失败与修复说明
• 首次基础回归出现 9 项失败：旧测试把「当前迁移版本为 1／只有两个健康路径」硬编码为永久事实，和本阶段追加版本 2、账号操作冲突。没有修改迁移版本 1 或删除测试；将当前／未来版本断言泛化到 MIGRATIONS，篡改历史用例仍实际制造不兼容记录，健康子集逐项要求保持不变，新增账号路由精确集合与契约操作检查。
• 并发迁移断言进一步检查全部版本、名称和校验值，而非只检查行数；另加真实 v1→v2 升级保留记录测试。因此这些调整是阶段扩展，不是放宽归属、安全、事务或错误要求。
• 编码审阅中处理了 Unicode Kelvin 符号可能被 lower 折叠成合法用户名、游标 UUID 项非字符串可能导致内部异常、邀请 TTL 布尔／字符串隐式转换及溢出、终端密码隐藏失败回退等边界，均补测试。没有通过改契约或隐藏异常来伪造通过。
• 初写文件的格式／长行检查失败已通过实际格式化和定点调整修复，最终 lint 与 format 均通过。

▶ 尚未实现与尚未验证
• 阶段 02 对话、run、SSE 尚未实现；目前验证了撤销后的新请求及可复用 session 校验，不声称现有长连接关闭已经验收。
• 阶段 03 尚无媒体 grant／文件资源。已提供授权代数失效基础，但实际 GET／HEAD／Range／媒体 grant 撤销须在阶段 03 实现和测试，不能把没有媒体表说成已完成交付撤销。
• 阶段 04 尚无真实任务、worker 或管理员任务故障列表。当前实现持久开关与事务检查服务，不虚构正在提交的任务被暂停；后续必须在入队及实际提交复核，已受理任务继续核对和保存。
• 前端和浏览器 E2E 尚未开始；HTTPS 浏览器 Cookie、代理来源、fragment 清除、人工核验页面等尚未验证。不能通过降低 Secure 来补演示。
• 此阶段只核对实际账号／健康契约子集；完整 51 操作、全部请求响应 schema 与错误状态声明的全量差异检查仍属阶段 07。当前账号路由共用较宽的 Problem 错误声明，不将其视为全部契约严格等价。
• 真实供应商权限、免费超额状态、CLI 宿主隔离、长时常驻、多平台／真机兼容与生产发布均未完成。未做备份，不承诺误删或磁盘损坏恢复。

▶ 下一阶段接续
• 安装、显式迁移、管理员命令与配置已更新到 `backend/README.md`。固定后端测试命令保持不变；前端测试／构建与 E2E 命令分别从阶段 06／07 提供。
• 后续迁移从版本 3 追加，不改版本 1／2。复用 `api/auth.py` 的 session_context／admin_context，在业务写事务再调用 `auth_security.require_session`／require_admin；不要仅使用请求开始时缓存的 User。
• SSE 必须周期性／交付前重新验证登录并在撤销时关闭。注销、停用和重置不会操作 Google 登录。
• 媒体 grant 须绑定并校验 users.auth_epoch 与用户启用状态，避免重启／重新启用复活旧授权。
• 新入队及 worker 提交调用 `require_generation_allowed`，另行叠加 run 状态、真实免费权益、提供方限额与磁盘保护；不要把本地开关当成真实供应商授权。
• 管理员 task 列表使用设计 OperationalTask 字段白名单，不能提供聊天、prompt、主机路径、下载链接或提供方完整错误。

---
In brief
• What's happening：新版账号与管理员流程已重新验证，178 项检查通过。
• Reason：现在能分清谁能进入、谁能管理，旧通行凭据失效后不能继续使用。
• Impact：旧网站不变；创作功能、真实服务验证和正式发布还没完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
