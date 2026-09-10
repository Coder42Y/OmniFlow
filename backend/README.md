【独立后端：00-foundation 至 09-runtime】

▶ 当前交接（2026-09-09）
• Vue 工作台、真实新 FastAPI＋假提供方浏览器联调和全部 51 操作／64 schema 契约核对已实现；最终检查及具体未验证项见 `docs/progress/08-audit.md`。
• 用户已批准发布并选择「补齐真实接入后发布完整新版」。第09阶段已增加显式 real 装配、bwrap 全进程隔离、受限网络出口、签名证据与跨进程心跳；同一生产 CLI 已用合成可执行文件验证。操作说明见 `backend/RUNTIME.md`，证据见 `docs/progress/09-runtime.md`。第10阶段继续发布工具；真实供应商和生产切换仍未完成。
• 下文提供独立回环开发命令，不是可直接复制上线的完整生产启动方式。自动管理 HTTPS 浏览器／服务／假 worker 的可运行离线闭环：`npm --prefix frontend run test:e2e`；正常退出会回收本轮进程及临时数据。


▶ 范围与边界
• 这是新增 FastAPI 工程，不导入或修改根目录 `server.py`、`index.html`，不代理旧接口，不切换现有网站。
• 当前实现健康检查、账号／管理员、对话／消息／轮次、持久事件与 SSE 恢复，以及图片上传、不可变作品版本、明确参考确认与受保护媒体交付。现有三类持久媒体任务、独立媒体 worker、恢复与 `/admin/tasks` 脱敏故障列表；Vue 前端位于 `frontend/`。消息／任务返回 202 只表示已保存排队，不表示模型或媒体生成成功。
• 默认关闭真实提供方、收费回退、调试文档；产品文本模型固定 `gemini-3.8-flash-low`。显式 `mock` 配置允许开发者注入测试适配器，但没有自动假生成入口；可执行的假媒体提供方只在 tests 中，不能当真实供应商效果。
• 所有命令从当前隔离 worktree 根目录执行。不能把数据位置指向旧站、生产库或真实素材目录。

▶ 安装与锁定依赖
需要宿主已有 Python 3.12–3.14 和 uv；本阶段实际验证 Python 3.14.4，不自动安装其他 Python。

```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
uv sync --project backend --locked --group dev \
  --python "$(command -v python3)" --python-preference only-system
```

• `backend/uv.lock` 固定运行与测试依赖、下载校验值，安装到当前 worktree `.venv`，不修改全局 Python。
• Starlette 1.6 的 TestClient 使用 `httpx2`；AnyIO 限定 4.12.x，避免 4.15 中已弃用的 `anyio.abc.BlockingPortal` 别名触发错误。测试保持「所有警告视为错误」，不屏蔽弃用警告。
• 不加载任何 `.env`，也不读取旧项目配置。配置只来自显式 `OMNIFLOW_*` 环境变量或测试构造的 `Settings`。

▶ 显式迁移与本地启动
```bash
export OMNIFLOW_DATA_DIR="$PWD/backend/var/data"
.venv/bin/python -m omniflow.cli migrate
.venv/bin/python -m omniflow.cli check
.venv/bin/python -m omniflow.cli serve --port 8765
```

• `migrate` 仅创建独立私有目录、媒体目录和 `omniflow.sqlite3`，执行前向迁移；重复运行不会重建或清空库。
• 数据目录和媒体目录要求权限 0700，数据库要求 0600；不自动修改既有目录权限，拒绝符号链接、未识别数据库、未来版本或被改写的迁移历史。
• `serve` 仅绑定 `127.0.0.1`，不接受公网绑定参数，不启用访问日志或代理头信任。用 Ctrl+C 结束自己的前台开发进程；不要结束其他服务。入口在收到停止信号时先通知 SSE 发出 `SERVICE_RESTARTING` 并关闭，再等待连接退出；优雅关闭上限 5 秒。直接使用原始 Uvicorn 工厂入口不包含这层信号通知，推荐始终使用本 CLI。
• `live` 只表示进程存活。`ready` 检查本地存储；real 模式另外核对全部生成路径的签名条件、限流和独立执行器心跳。disabled/mock 模式保留仅本地检查，不冒称模型就绪。任何 ready 都不是一次真实生成成功的证据。
• 未迁移时服务仍可启动，`live=200`、`ready=503`；请求与应用启动都不自动迁移、不创建缺失数据库。
• 健康接口按契约在本地存储未就绪时返回 503 Problem，错误码为 `STORAGE_UNAVAILABLE`；后续业务新增写入的磁盘不足错误使用契约规定的 507。

另一个本机终端可检查：
```bash
curl --noproxy '*' http://127.0.0.1:8765/api/v1/health/live
curl --noproxy '*' http://127.0.0.1:8765/api/v1/health/ready
```

▶ 配置与安全默认值
• `OMNIFLOW_DATA_DIR` 默认是启动目录下 `backend/var/data`；推荐始终显式设为当前 worktree 中的绝对路径。缓存与测试文件在 `backend/var/`，不进入版本控制。
• `OMNIFLOW_PUBLIC_ORIGIN` 默认 `https://localhost:8443`；Host 默认仅 `localhost`、`127.0.0.1`。列表配置用 JSON，例如 `OMNIFLOW_DEVELOPMENT_ORIGINS='["http://localhost:5173"]'`。仅非生产模式允许本机 HTTP 开发来源；禁用通配来源、通配 Host 和携带凭据的来源 URL。
• 所有修改请求校验精确 Origin；账号／管理员修改还要求匹配的 Cookie 上下文与 `X-CSRF-Token`。注册／登录轮换并消费原上下文，旧令牌不能重放。匿名 CSRF 不提供登录授权。
• Cookie 固定 `__Host-`、Secure、HttpOnly、SameSite=Lax、Path=/，不设置 Domain、不提供安全降级选项。默认启动命令是回环 HTTP，仅用于健康检查；不要指望浏览器通过该 HTTP 端口完成登录。账号已以 HTTPS URL 的 TestClient 及阶段07自管 HTTPS 浏览器联调验证；后者仅本轮浏览器信任合成证书，不等于生产证书／转发已配置。
• `OMNIFLOW_DOCS_ENABLED=true` 仅允许非生产模式开放 `/docs`、`/openapi.json`；默认没有源码／素材静态根，也不开放旧接口。
• `OMNIFLOW_ENVIRONMENT=production` 进一步拒绝 mock、开发来源与调试文档，但该设置「不等于部署完成或已获发布授权」。真实 HTTPS 转发、代理与宿主隔离仍需发布前审查，本轮不配置。
• `OMNIFLOW_MIN_FREE_DISK_BYTES` 默认 512 MiB，是磁盘保护，不是业务配额；不足时不删除任何作品。
• 密码默认 6–128 字符（2026-09-09按用户明确要求调整）；邀请／登录 7 天、重置 30 分钟，实际值由 `/auth/policy` 返回。用户名仅去两端 ASCII 空格、ASCII 小写化，再校验 3–32 位字母／数字／下划线；密码不规范化、不截断。没有默认管理员或密码。
• 普通请求体在 JSON 解析前限制为 1 MiB，按实际接收字节计数，不信任缺失／虚报 Content-Length 或 Content-Type；超限返回 413／UPLOAD_TOO_LARGE，不回显输入。契约最大 50000 字符文案即使全部为转义后的非 BMP 字符仍可保存；图片上传继续使用独立的图片容量限制和先鉴权流程，不受此 JSON 上限影响。这是单次容量防护，不是用量配额，也不等于已防住所有并发／慢连接攻击。
• 统一响应生成 `X-Request-ID`，忽略客户端同名值；错误采用 `application/problem+json`。可选字段错误仅使用固定字段白名单和固定描述，不回显 input、ctx、原始错误文案、未知字段名或堆栈。
• 不将 Git worktree、文件权限检查或配置校验描述为宿主安全沙箱；不承诺备份或灾损恢复。

▶ 本地显式管理员与账号闭环
迁移后在自己的交互终端执行（本轮测试只创建临时合成账号）：
```bash
.venv/bin/python -m omniflow.cli create-admin --username local_owner
```
• 两次隐藏输入密码；不接受密码参数、管道或环境变量，不自动迁移、不提升既有账号角色。再次使用同一用户名失败，不重设密码。
• 浏览器／测试客户端：GET `/api/v1/auth/csrf` → 带 Origin、Cookie、X-CSRF-Token POST `/auth/login` → 保存返回的新 csrf_token → POST `/admin/invitations`。邀请 URL 仅签发时返回，凭据在 fragment；前端必须读完立即清除地址栏。
• 另一浏览器取得自己的匿名 CSRF 后，可校验邀请并注册；校验不消费，注册事务原子创建账号、消费邀请及建立登录。退出只撤销当前网站登录，不操作 Google 授权。
• 人工核验后管理员 POST `/admin/users/{user_id}/password-resets`，`verification_method` 为 `trusted_existing_contact` 或 `in_person`；重发使旧重置链接失效。接口不能代替真实人工核验。自由 note 接受但不保存／日志记录，避免误填密码或完整链接；核验方式持久保存。
• POST `/auth/password-resets/complete` 后要求重新登录；全部旧登录撤销。停用账号同样撤销登录；重启／重新启用不能恢复旧会话。不能停用最后一名启用的管理员。
• Argon2id 参数：19 MiB、2 次、单并行度，最多 4 个并行哈希；随机盐。高熵邀请／重置／会话／匿名上下文只保存用途隔离的 SHA-256 校验值，不保存明文链接、Cookie 或 CSRF token。
• 防爆破采用 SQLite 原子短窗口计数：默认 300 秒，表单每来源 60 次、登录每规范化用户名 10 次；超限 429＋Retry-After，跨进程／重启有效。不信任 X-Forwarded-For；真实反向代理部署需另行审查，不能为区分来源而直接信任客户端头。
• 匿名 CSRF 默认 3600 秒；配置 `OMNIFLOW_CSRF_TTL_SECONDS`。限流配置分别为 `OMNIFLOW_AUTH_RATE_WINDOW_SECONDS`、`OMNIFLOW_AUTH_RATE_IP_ATTEMPTS`、`OMNIFLOW_AUTH_RATE_USERNAME_ATTEMPTS`；它们不是每日创作用量限制。
• 管理员只可读账号／邀请元数据和本地开关，不提供私密聊天、密码、历史链接或作品浏览入口。开关持久化且仅局部更新；恢复本地开关不表示真实提供方免费状态已核验。

▶ 对话、持久轮次与订阅
• POST `/api/v1/conversations` 创建独立应用上下文，不启动 CLI。POST `/conversations/{id}/messages` 保存用户消息和 queued run；两者都要求 `Idempotency-Key`。同键同语义重放原受理结果，同 `client_message_id` 换键仍不重复排队；当前身份和归属优先校验。
• GET 对话／消息／run 列表支持稳定分页；PATCH 仅改标题；DELETE 要求无未结束 run。删除清掉网站可查询正文、事件和幂等响应正文，保留归属／关系墓碑，旧键返回 410，不删除独立作品。不承诺 SQLite 已释放页面、磁盘残留或供应商副本被安全擦除。
• 单对话串行执行；入队为用户和助手预留相邻序号，只有助手开始执行时才建立其消息。上下文仅取本轮用户消息及更早消息，不包含后排排队内容。不同对话由独立调度线程领取。
• GET `/conversations/{id}/snapshot` 在同一读事务取得最近 50 条消息、非终态 run 和事件水位。以 `after_event_id` 订阅 `/events`，或重连带优先级更高的 `Last-Event-ID`；无游标只看新事件。逐条交付前和空闲心跳均复核登录／归属，退出、停用、重置或登录到期关闭旧流。
• 订阅、读取、刷新和断线不启动或取消生成。轮次停止请求与持久媒体任务不是同一操作；`require_tool_run` 与统一任务服务在同一事务校验状态／权限，已受理媒体不随 run 停止；官方协议与受控工具已提供离线适配，真实宿主接线仍默认拒绝。
• 图片附件、选中作品与确认事实现按 owner／对话／不可变版本校验，未经明确选择不读取其他对话素材。快照返回最近消息引用的可用 `artifact_versions`，已删除引用缺失但不丢弃其他作品；`tasks` 返回非终态媒体任务；任务在 run 结束后完成也会补齐助手消息作品关联并发送 message.updated。对话删除现同时检查未结束的任务媒体占用范围。

独立文字调度进程（以下未配置 real 时不启动真实 CLI）：
```bash
.venv/bin/python -m omniflow.cli run-manager --workers 4
# 或最多领取一轮，便于本地检查
.venv/bin/python -m omniflow.cli run-manager --once
```
• 不随 HTTP 启动，不使用 FastAPI BackgroundTasks。当前内置提供方默认拒绝调用，领取后如实写入 `failed / PROVIDER_UNAVAILABLE`；没有配置假回复的公开运行入口。子进程假 CLI 仅位于 `backend/tests/`，由测试显式注入，不能作为 Gemini 实测证据。
• SQLite 租约与活动轮次唯一索引防止并发重复领取；执行中续租和检查停止。CLI 内部 ID 只在服务端绑定，新对话无旧 ID，旧对话只按明确映射恢复。另有 `cli_process_holds` 持久进程占用，在打开 CLI 前写入，不随租约到期、管理器退出或丢失句柄而释放。空闲租约过期只允许原持有者确认回收，不授权另一个管理器同时恢复相同 ID／目录。
• `Session.stop()` 只有严格的 `True` 才代表该轮已确认停止；`False`、`None`、整数、字符串、字典或异常都不能充当确认。未确认时保留部分文字，统一记录 `needs_reconciliation / RECONCILIATION_REQUIRED` 并阻塞后排，不把后续关闭进程成功当作该轮已安全停止的证明。
• `Session.close()` 必须有界、可重复，且只有确认本会话及受控子进程已退出才返回严格的 `True`。异常／超时／False／None 保留句柄与持久占用，禁止重开；后续排队的首轮显示 `needs_reconciliation`，其他对话仍可处理。正常确认退出后可恢复明确的旧 CLI ID；已经标记待核对的轮次仍不会自动重放。
• 提供方打开前已持久占位；若打开后未返回句柄便异常，占用仍保留。只有 `ProviderUnavailable` 在 open 阶段明确保证未留下进程时才可安全释放。管理器崩溃遗留的占用不会按时间或 PID 自动清掉；缺少退出证明时宁可保持阻塞。当前尚无人工核对／强制解锁接口，阶段 05 必须补足真实官方适配器退出证明，不可直接删占用来演示恢复。
• 本地暂停在入队、领取及 `send()` 前的最后一次短事务复核；已领取但未发送的轮次回到 queued，保留原 run／助手消息 ID 与序号，开关恢复后只发送一次。取消这种暂停轮次同时结束空助手消息。已经发送的轮次不因本地开关变化而停止收尾；数据库检查和外部发送不是跨系统原子事务，不宣称外部 exactly-once。
• 租约过期、提供方意外断线或提交之后的不确定错误进入 `needs_reconciliation`，保存已交付内容且暂停该对话后续轮次，不自动重放整轮。当前没有人工核对恢复接口，不要直接改库把未知轮次改回 queued。
• 默认队列保护 1000 个非终态 run、单轮最长 600 秒／输出 128000 字符、租约 15 秒、检查间隔 0.2 秒；对应 `OMNIFLOW_RUN_QUEUE_CAPACITY`、`RUN_MAX_SECONDS`、`RUN_OUTPUT_MAX_CHARS`、`RUN_LEASE_SECONDS`、`RUN_POLL_SECONDS`（后四项同样带 `OMNIFLOW_` 前缀）。这是单次安全和并发保护，不是每日业务额度。
• `OMNIFLOW_EVENT_POLL_SECONDS` 默认 0.2 秒，`OMNIFLOW_EVENT_RETENTION_COUNT` 默认 10000。轮次执行收尾调用内部事件裁剪，维护入口 `ConversationService.prune_events` 也可显式调用；只裁剪派生日志，不删用户消息。游标过期 410 后重新取快照。
• 阶段 05 已用官方 NDJSON 协议替身验证独立进程、目录、ID、逐轮输入、停止和明确恢复。真实 agy／Google 授权挂接、宿主隔离、远端费用核验与长时常驻仍未实测；管道实现不是操作系统沙箱。

▶ 素材、不可变版本与媒体交付
• `POST /api/v1/uploads` 接受 multipart `file` 与可选 `conversation_id`；`POST /artifacts` 仅保存 text 文案，`POST /artifacts/{id}/text-versions` 要求当前 `base_version_id`。这些操作及明确参考确认均需要 Cookie／CSRF／Idempotency-Key。上传的幂等摘要基于文件字节与对话字段，文件名／multipart boundary 不影响重放。
• 仅接受可解码 JPEG、PNG、WebP，拒绝动画、多帧、SVG、HTML、坏图和视频。默认单图 20 MiB／2500 万解码像素，配置 `OMNIFLOW_MAX_UPLOAD_BYTES`、`OMNIFLOW_MAX_IMAGE_PIXELS`；实际值由受保护 `/capabilities` 返回，不是每日额度。能力接口仍如实标记真实生成不可用，参考图编辑质量未验收。
• 原始图片字节保留，因此不承诺去除 EXIF 等自带信息。用户文件名完全不作为路径、作品标题或下载文件名；临时文件及不可变版本位于私有 media 目录，0600 权限，UUID 文件名，无公开静态目录。版本 SHA256、像素、大小均来自已校验实际内容；直接保存的文案／上传图 execution_engine 为 null。
• 数据库版本元数据禁止更新／删除；修改追加版本，旧版本仍可下载。任务输出的 source_task_id 唯一，后续 worker 重取结果不能新增第二版本。写入使用私有临时区、fsync、不覆盖的原子链接与短事务关联；崩溃孤立文件不可在线访问，维护核对清理，不承诺文件与数据库跨系统严格 exactly-once。
• `POST /conversations/{id}/reference-confirmations` 为用户明确确认，绑定精确图片版本；不向普通模型工具开放。新图片不改变旧确认或旧视频输入。网页作品库可列用户自有作品，但内部执行应使用 `authorized_versions(..., through_seq=本轮消息序号)`，只取得截至该轮的明确引用／确认，不能将后排消息引用或全库提前注入模型。
• 用户 `GET/HEAD /artifacts/{id}/versions/{vid}/content` 每次鉴权和检查归属；GET 支持单区间 Range，HEAD 忽略 Range 且无响应体。多区间／越界为 416；无权访问先 404，不返回文件大小。download=true 使用固定安全文件名；no-store、nosniff。分块读取再次校验授权，已交付的副本无法撤回。
• 供应商仅能凭短期 `media-grants/{id}/content?token=…` 读取被授权的具体任务输入图片，可重复 GET／HEAD。token 仅保存用途隔离摘要；默认 TTL 300 秒、原任务窗口最多 1800 秒，配置 `OMNIFLOW_MEDIA_GRANT_TTL_SECONDS`、`OMNIFLOW_MEDIA_GRANT_WINDOW_SECONDS`。签名地址仅交指定提供方，不进入普通 API、事件、幂等账本或日志。到期／撤销／任务结束／账号停用或重置统一 404，不因重新启用而复活。
• 任务媒体范围已接入阶段 04 的真实持久任务表。可信任务创建事务调用 `TaskMediaService.reserve` 登记所有输入／目标与账号授权代数；即将提交且所有安全闸门通过后调用 activate／issue；真实终态事务调用 close 撤销 grant 并释放占用。排队未激活不能签发，重复激活不能延长原窗口，未知提交或 saving 不能提前释放。没有网站／MCP 任意签发接口。
• DELETE 立即隐藏作品／版本并返回稳定清理回执；有未结束任务输入／目标占用时 409，不偷偷撤去任务输入。重复删除不重算时间，幂等重放已删作品为 410，删除对话不连带删作品。默认物理清理目标 24 小时，配置 `OMNIFLOW_ARTIFACT_CLEANUP_SECONDS`。

仅对自己的独立数据目录执行显式维护：
```bash
.venv/bin/python -m omniflow.cli purge-artifacts
```
• 清理到期删除文件，保留资源与幂等墓碑；清理超过一小时且确认无版本关联的自建孤立文件，flock 防误删仍活跃的上传。不删可用作品，不提供备份或恢复，也不自动删除宿主其他文件。当前未安装定时服务，阶段 04／部署审查须安排调用；清理目标不是已经运行的生产定时承诺。

▶ 持久媒体任务与独立 worker
• `POST /api/v1/tasks` 接收 image／ai_video／local_motion，要求当前账号、CSRF、幂等键；严格校验确认、引用与目标基础版本。queued 可取消，submitting 起拒绝取消；recover 只续查原任务或下载原结果，没有线索的未知提交返回 409，绝不新建生成。
• disabled 默认拒绝新增任务；real 通过 `backend/RUNTIME.md` 的受保护配置装配 Agnes／FFmpeg，缺证据或独立 worker 时继续返回 503。本地开关、账号状态／授权代数、磁盘及提供方条件在入队和实际提交前复查；已受理任务在停用／暂停后仍续查和保存，参考图 grant 的撤销可能使真实任务失败。
```bash
.venv/bin/python -m omniflow.cli media-worker --once
# 或独立前台持续消费；可用 Ctrl+C 停止自己的 worker
.venv/bin/python -m omniflow.cli media-worker
```
• API 不启动 worker。每次领取生成新的租约令牌，网络等待期间独立续租，最终写入再次校验；多个独立进程可竞争同一 SQLite 队列。信号停止后完成当前有界动作再退出，不取消已受理工作。阶段 05 提供方必须实现有界调用。
• 提交意图及不可逆 dispatched_at 先落盘，再调用 submit。只有明确未提交的过期领取可重领；提交之后没有任务 ID 的故障进入 submission_unknown，不因重启／recover／幂等重放再次 POST。外部受理和本地事务不是严格 exactly-once。
• running 重启续查同一 ID；saving 失败重取同一 result_key。输出作品／版本 ID 在入队时确定；文件发布后事务崩溃会复核稳定文件并补齐唯一版本，维护程序不会删除未结束任务预留的稳定输出文件。未确定状态持续占用输入，不能为了删除素材而伪造失败。
• requested_parameters 原样保存已校验的请求；实际 SHA256、大小、宽高、时长、fps 来自文件。图片实际解码，视频用已有 ffprobe 的固定参数、pipe 输入、仅 pipe 协议与 15 秒时限基本校验；无 ffprobe 或坏文件不会 completed。ffprobe 不是宿主沙箱；完整解码、真实质量及播放验收仍未完成。
• 默认任务容量 1000（并发保护）、租约 15 秒、调度间隔 1 秒、故障续查间隔 30 秒、单输出上限 100 MiB；分别配置 `OMNIFLOW_TASK_QUEUE_CAPACITY`、`OMNIFLOW_TASK_LEASE_SECONDS`、`OMNIFLOW_TASK_POLL_SECONDS`、`OMNIFLOW_TASK_RETRY_SECONDS`、`OMNIFLOW_MAX_GENERATED_BYTES`。图片仍受单图解码／上传大小安全上限约束。不扣每日额度、不收费回退、不因低磁盘删除可用作品。
• 内部 `TaskService.create_tool` 使用可信 ToolAuthority：绑定 owner／conversation／run／租约／稳定 action_id，不能由模型选择；拒绝 discuss_only、停止后的新增动作、全库和后排引用。direct_text_video 默认拒绝，阶段 05 网关必须根据原用户明确要求建立证据，不能信模型声明。该内部入口不是公开 MCP 服务。
• `MediaProvider` 保留默认 DisabledMediaProvider，不联网；check 只能读取本地可信条件快照，不能在写锁内联网。阶段 05 已新增 AgnesProvider、FFmpegProvider、MediaRouter 及有界 HTTP/管道适配，使用下节显式离线注入验证，真实 agy／Agnes 不调用。

▶ 阶段 05 适配基础（第09阶段接线更新如下）
• `agy_adapter.AgyProvider(root, gate=..., launcher=...)` 实现官方 `event=user` 输入、init／step_update／result 输出；启动计划固定 gemini-3.8-flash-low、双向 stream-json、--sandbox，新对话不带恢复参数，旧对话仅 --conversation 精确 UUID。常驻与明确恢复只发当前用户消息，累计 usage 仅保留最新快照，不按轮累加。
• AgyProvider 单独构造仍默认 launcher=None；real 运行入口现由 `runtime.py` 装配可信 AgyLauncher，以只读签名绑定镜像、独立 HOME/cwd、单文件授权挂载、bwrap 全进程和网络隔离启动。只走官方 structured_output→可信 ToolGateway，CLI 内拒绝全部 MCP／文件／命令／网页工具，不注册虚假的外部 MCP。真实官方登录材料和已安装版本兼容性仍需现场验证；详见 `backend/RUNTIME.md`。
• 官方结构化路径显式传 `--json-schema`，要求封闭的 `{text, actions}`；init 和 result 必须确认相同 schema，response 必须是 structured_output 的同一对象序列化。全部 `agent_response` 原始增量只做有界缓存，不直接交付；终态与统计完整校验后仅将专用 text 发为 message.delta，再由统一网关校验动作。结构化模式当前在终态一次交付正文，不承诺逐字预览；中断前未校验的 JSON 不作为「部分文字」保存。工具参数不会混入消息正文／快照消息／SSE 消息事件，任务 requested_parameters 仍按公开契约保留。
• `process_transport.ProcessTransport` 支持有界 NDJSON 读写、重复字段拒绝、单帧 256KiB、仅自己的新进程组。`stop` 必须观察官方 INTERRUPTED/CANCELED 并确认关闭，单纯杀掉进程不冒称成功取消；`ERROR` 不是成功，未知状态与断线保持待核对。工具日志/思考/路径不当作助手文字，更不能再执行一次 tool_info。
• `tool_gateway.ToolGateway` 实现内部 MCP initialize／tools/list／tools/call 与专用 stdio 流；RunScope 必须由可信宿主绑定，不提供匿名 HTTP 或模型自报身份入口。普通工具只含 submit_image／submit_video／submit_local_motion／read_task／list_artifacts。若采用 structured_output，RunManager 的 tool_gateway 注入可消费封闭 actions 数组；动作只能有 name/arguments，稳定幂等键由 run＋动作序号生成，不能从混杂正文提取 JSON 执行。
• 查询工具严格校验 UUID 字符串、字段及当前归属／活动轮次；错误编号返回脱敏 VALIDATION_ERROR，不退出 stdio 网关。完整有界坏 JSON 帧返回解析错误后继续处理下一帧，参数嵌套最多 32 层；超长／不完整帧仍拒绝并关闭，不能以容错绕过帧容量限制。回归位于 `backend/tests/test_cli_limits_revision.py`。
• 明确自然语言当前采取保守白名单语法，例如「请生成一张图片：蓝色方块」「请生成2张图片：不同布局」「请直接文生视频：蓝色海洋」「请用已确认图片生成视频：轻轻移动」「请选择本地运镜：缓慢推进」。其他表达不猜测执行，要求澄清或通过已有严格任务 API 明确操作；不是完整自然语言理解质量验收。单轮执行数来自用户明确张数（1–8），默认一份，不是每日业务配额。
• 图像确认仍由用户接口完成。keyframe 内部工具可省略 `reference_confirmation_id`：网关从本轮原消息的可信输入补齐，CLI 不必猜测不可见标识；显式提供时必须精确一致，null／其他确认均拒绝。任务创建事务再次核对同一原消息、用户／对话／选中版本及图片有效性，不能借旧轮或后排确认。公开 `POST /tasks` 仍必须显式带确认，不改公共契约；模型不能自造 confirmed 或借 text 模式绕过确认。只读工具也逐次核对账号、当前 run 与租约，可在 discuss_only 查询，但不能看到其他对话／旧库或后排消息才授权的图片。完整假 CLI→可信绑定→媒体 worker→刷新下载回归见 `backend/tests/test_cli_confirmation.py`。
• EvidenceGate 的最长 300 秒规则未变。real 使用 RSA/SHA256 签名验证器，绑定实际配置与核验记录摘要；应用仅持公钥，没有签发／自动刷新接口。费用、超额与授权记录必须来自账号持有者的只读核验，公开价格页和本地 useG1Credits 字段不能替代。未找到官方费用只读查询协议，未编造自动采集器；具体流程见 `backend/RUNTIME.md`。
• Agnes API 的 HTTP 429 在读取错误正文之前识别，迁移 8 的 `provider_limits` 持久保存观察时间和 Retry-After 截止，重启 API／worker／适配器或恢复管理员开关均不清除。支持秒数与 HTTP 日期，缺失／无效提示保守等 300 秒；多次观察只延长不缩短。冷却后仍要求有效且晚于该限流事实的权益证据，不把等待结束或旧放行快照当作免费／限额恢复证明。当前单一 Agnes 授权限额范围未核明时，保守同时暂停图片和 AI 视频，不影响文字或本地运镜；不得通过换模型／授权绕过。只有受信任核验器未来提供新证据后才能恢复，不新增 HTTP／环境变量解锁后门。
• 已可靠取得 429 后，响应头读取／校验失败或资源关闭异常不能覆盖限流事实；重复 Retry-After 等关键头视为歧义，丢弃等待提示并采用保守等待，不读取错误正文。响应与连接均尝试关闭，已有主异常优先；正常路径的关闭失败仍抛出，非 429 的响应头、重定向、压缩、容量与截断校验没有放宽。实际 PinnedHTTPSExchange→SafeHTTP→Agnes→持久任务的故障回归见 `backend/tests/test_safe_http_failures.py`，只用合成 socket／TLS／HTTP，不代表真实网络已验证。
• 新入队与 worker 提交前都读取持久暂停，排队任务保留 queued／PROVIDER_LIMIT_REACHED。429 后原 POST 仍是 submission_unknown，不声称一定未受理，不重发；已持有供应商 ID 的任务继续续查／下载同一结果，查询返回 429 也记录新增暂停但不取消原任务。这些检查不提供外部事务原子性：已经越过最终检查且在途的请求不能被事后召回。
• Agnes 图片 POST /v1/images/generations 使用 size＋ratio、extra_body.response_format=url，参考图按用户原顺序放 extra_body.image；同步结果先保存 adapter_receipts。视频 POST /v1/videos 固定 Flash／720P／n=1，seconds 为字符串，GET /agnesapi 同时使用 video_id 和 model_name；不能拿响应 id/task_id 替代 video_id。图片结果线索落盘而 ID 返回前崩溃时，新 worker 只接回原结果，不重新 POST。
• 参考 grant 即将提交才签发，完整地址只给媒体提供方。SafeHTTP 精确 HTTPS 主机白名单、全 DNS 地址公共性校验、连接固定地址、原主机 TLS 校验、拒绝全部重定向/压缩与超限/截断响应；存储下载不携带 Agnes Authorization，不继承代理。PinnedHTTPSExchange 代码已提供但没有执行真实网络测试；离线 exchange 记录固定地址、请求体和请求数。
• `FFmpegProvider` 只读任务占用的真实图片，解码后重新编码 PNG，经 pipe 传给 FFmpeg；四种枚举滤镜、固定 H.264/24fps、有限时长和文件容量，无 shell/任意 URL/滤镜参数。解析宿主已安装 ffmpeg 的绝对位置后使用精简环境，不修改全局运行时。实际元数据由 ffprobe 校验，稳定文件和最终版本仍复用 worker，不标成 AI 视频。
• 本地运镜发布稳定文件后、adapter_receipts 提交前中断：worker 在过期且已 dispatched 的同一任务中检查预留输出文件，校验私有普通文件及任务时长／画幅／24fps，在写事务外检查后再次核对租约，再原子接回 receipt 与 running 状态。无文件、损坏、参数不符或活跃提交者不接回；不重新执行运镜／下载，不绕过唯一输出版本。真实独立 Python 进程在 publish 后 os._exit(78) 并三次重建进程的测试位于 `backend/tests/test_cli_revision.py`。
• `backend/examples/systemd/*.service.example` 仅为三份待审查文本，没有安装或启用。包含不存在的 REVIEW_REQUIRED 路径/用户、显式审批条件、最小目录写入、组级停止和仅回环网络；没有自动启用目标。真实运行身份、Google 授权、提供方出口及服务长时行为必须逐项审查后另行批准，不能直接复制启用。
• 运行离线适配闭环：`.venv/bin/python -m pytest backend/tests/test_agy_adapter.py backend/tests/test_tool_gateway.py backend/tests/test_media_adapters.py -q`。使用官方协议假 Python 子进程、合成账号/图片和注入 HTTP 回复；FFmpeg 运镜实际运行本地合成图，不发送任何真实模型请求。

▶ 测试与构建
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_health.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_artifacts.py
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
uv build --project backend --out-dir "$PWD/backend/var/dist" \
  --python "$(command -v python3)" --python-preference only-system
```

• 固定 pytest 命令只发现新增测试；临时数据在 `backend/var/pytest`，pytest 会清理它自己的该临时目录，切勿存放真实数据。
• 单元与 API 测试默认阻止网络连接；三份实际 HTTP 冒烟脚本均明确创建回环服务，只访问自己启动的端口。对话／媒体脚本以合成 Cookie 手工请求回环 HTTP，未降低 Secure Cookie，不是浏览器 HTTPS 联调。媒体脚本验证上传／重传、真实 HEAD／Range、短期取图、重启恢复、占用／墓碑及显式清理，并检查服务日志无签名 token。
• 冒烟脚本采用精简环境、合成临时数据库、自选本机端口，验证迁移、启动、重启、响应头与日志不含虚构 token，finally 中回收自己的子进程，结束后删除自己的临时数据。
• 旧 `tests/test_backend_e2e.py` 会请求旧 8080 服务与真实生成路径，本阶段不运行；不是将其失败改成通过，也未修改旧测试。
• `backend/tests/test_runtime_contract.py` 已严格核对全部 51 操作、64 schema、请求响应与安全声明；实际资源和持久事件亦通过设计 schema 校验。第08阶段新增容量保护的 413 错误声明，不改变设计成功响应或放宽设计 schema；不以文档校验冒充业务实现。

▶ 后续接续
• 当前迁移版本 8；后续只能追加版本 9 起，不能改写版本 1–8。版本 8 新增持久供应商限流事实，合成 v7 升级保留旧账号及完整迁移历史；未改旧迁移。版本 7 新增同步媒体结果线索 adapter_receipts。版本 6 新增任务、提交意图、租约与幂等账本，v5 升级实测保留账号、文件版本及历史。版本 5 新增素材与授权生命周期，并已实测 v4 升级保留账号／历史。已实测 v2／v3 升级保留账号、对话和历史。版本 4 对旧版有 CLI ID 或管理器记录的对话建立 uncertain 占用：旧版没有可信退出证明，升级不能假定旧进程已停。这些旧对话需核对，未曾启动的新对话不受影响；本轮只迁移临时合成库，不操作生产数据。
• SQLite 每连接启用外键、FULL 同步和有限 busy timeout，业务写入使用短 `transaction()`，一致读取使用 `snapshot()`；等待 CLI 打开、发送和退出均不占用写事务。
• 后续私有接口复用 `api/auth.py` 的 session_context／admin_context；业务写事务须用 auth_security.require_session 再次校验，不能仅相信请求开始时的身份。SSE 每次交付前／心跳须复核，撤销后关闭。
• 素材 grant 已通过任务媒体范围绑定 users.auth_epoch，逐次核对用户启用状态与代数；停用／重置和重新启用后的 GET／HEAD 拒绝均有离线测试。任务／worker 生命周期已接线并用合成提供方验证，真实供应商取图仍待阶段 05 独立验证，不把离线闭环当作真实生成验收。
• 新入队与 worker 实际提交须在自己的事务内调用 require_generation_allowed，再叠加提供方费用、限额、磁盘及 run 状态检查；已受理任务不得因本地暂停停止核对。任务表与管理员脱敏故障概况已实现，管理员仍不能读取他人的普通任务详情。
• 任何网络等待、CLI 执行或媒体生成都不能占用写事务。当前已有独立文字管理器和媒体 worker，没有 BackgroundTasks 或真实供应商调用。
• 阶段记录见 `docs/progress/00-foundation.md` 至 `docs/progress/09-runtime.md`。前端与端到端固定命令见 `frontend/README.md`；接续第10阶段发布工具，真实供应商和生产切换由主控现场流程核验。

---
In brief
• What's happening：新版创作工作可以排队、停止未开始的工作，并在重启后继续查结果和保存。
• Reason：先记住正在做什么，结果不确定时不会擅自再做一遍。
• Impact：旧网站不变；真实生成、生产发布和灾损恢复均未完成。
• Require Input：不需要 input，我继续按已批准阶段推进。
