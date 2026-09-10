【05-cli：官方 CLI 与媒体适配器】

【2026-09-09 恢复尝试 2：429 主异常保留与关闭故障回归】

▶ 当前结论
• 指定两处缺陷已实际复现并修复，本阶段离线实现与测试通过，可交独立复审。固定门槛最终 `675 passed in 142.77s`，保留原 625 项并新增 50 项；包含新增故障链路的 231 项联合专项又前台有界连续两次通过。下方原 625 项成功结论仅为历史证据，不能代表覆盖本次异常窗口；本轮 pass 不等于生产发布或真实供应商验收。
• 完整阅读指定实施计划、Spec v1.0、API 文档、已有 00–05 阶段记录及本任务指定完整审查报告 `.superpowers/autodev/logs/05-cli-1-reviewer-1788918513547182031.final.md`；核对 SafeHTTP／PinnedHTTPSExchange／Agnes／持久限流／任务 worker 与实际测试，递归读取完整 Task／Problem 及请求 schema。未使用其他会话或真实凭据。

▶ 已实现
• SafeHTTP 与 PinnedHTTPSExchange 共用状态／头部处理：可靠取得 429 后，读取或校验头部失败统一保留 HTTPRateLimited，不返回普通头部错误；正常 429 保留等待提示，重复关键头（新增 Retry-After）或头部读取失败丢弃不可信提示，交既有 ProviderLimits 保守等待 300 秒。其他状态继续拒绝重复关键头，没有放宽重定向／压缩／容量／完整性检查。
• 统一资源关闭：已取得响应时总是分别尝试关闭响应与连接；有主异常时只压住次要关闭错误并原样重抛主异常，正常完成后的关闭错误仍向上抛出，不能假称成功。TLS 握手失败也先尝试关闭原始 socket 与连接，保留握手主异常。捕获 BaseException 仅为完成资源清理，不吞掉原始退出／取消。
• 实际 API→TaskWorker→AgnesProvider→SafeHTTP→PinnedHTTPSExchange 注入合成 socket／TLS／HTTP，覆盖图片与视频、响应／连接关闭故障、两者同时失败、重复 Content-Type／Retry-After、头部读取失败及组合。断言持久限流 1 行、新入队 503、既有排队仍 queued、未写 dispatched_at、POST 总数仍 1；未知原任务不能 recover 重发。两个原始视频重现另外调用既有独立 Python worker 故障入口，核对新进程和新权益证据也不能绕过暂停。
• 其他回归覆盖非 429 四种状态与五类重复关键头仍拒绝，正常响应的关闭错误不吞掉，正文读取超时／退出与关闭错误同时发生时保留原异常，响应安全校验错误不被关闭盖住，TLS 失败清理，以及直接注入 SafeHTTP 的 429 同样保留。

▶ 先失败再修复的证据
```bash
.venv/bin/python -m pytest backend/tests/test_safe_http_failures.py -q
```
• 产品修复前两项正式测试实际 `2 failed in 0.86s`：均观察到 `(限流行数, 新任务状态码, 原排队状态, POST 次数) = (0, 202, submission_unknown, 2)`，与指定审查一致。这是本轮写入仓库的正式回归，不冒称原内存插件原样重跑。
• 修复后与既有暂停／媒体／恢复联合专项实际 `85 passed in 13.54s`；补齐 50 项后新文件专项 `50 passed in 4.54s`。随后又加入原始两项的独立进程复查，最终全量和重复专项均通过。没有删旧测试、修改契约、安全预期、状态机或迁移历史。

▶ 最终实际测试命令与结果
全部在当前 worktree 前台有界执行，仅合成账号／临时库／假提供方与本机回环冒烟。
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 后端全量 `675 passed in 142.77s (0:02:22)`，退出码 0；保留全部先前基础、账号、对话、素材、任务、CLI／工具／媒体测试。全量实际包含既有三个自管 127.0.0.1 HTTP 冒烟、假 CLI 子进程、崩溃恢复与合成图片的真实本地运镜，没有将它们说成真实供应商验证。
• Ruff `All checks passed!`，格式 `82 files already formatted`。新测试实际格式化，没有关闭规则或屏蔽警告。契约：40 路径／51 操作／64 schema／525 内部引用、22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不声称完整官方结构校验；实际服务全量契约严格差异仍由阶段 07 完成。
• 空白和列明原站／部署文件差异检查均退出码 0。没有安装依赖、重新构建包或另行独立运行三个冒烟脚本，不把历史命令当本轮实测。
• 通过前台 `subprocess.run(check=True, timeout=120)` 有界串行两次执行下列联合专项，分别 `231 passed in 38.26s`、`231 passed in 38.32s`，均退出码 0；不是后台循环或嵌套 agent，测试只回收自己创建的子进程：
```bash
.venv/bin/python -m pytest backend/tests/test_safe_http_failures.py \
  backend/tests/test_cli_limits_revision.py backend/tests/test_cli_confirmation.py \
  backend/tests/test_cli_revision.py backend/tests/test_agy_adapter.py \
  backend/tests/test_tool_gateway.py backend/tests/test_media_adapters.py \
  backend/tests/test_adapter_recovery.py -q
```

▶ 本轮修改文件
• `backend/src/omniflow/safe_http.py`。
• 新增 `backend/tests/test_safe_http_failures.py`。
• `backend/README.md`、`docs/progress/05-cli.md`（本节；历史记录保留）。
• 无依赖／锁文件、数据库迁移、真实 launcher、授权、公开接口或其他产品源码变更；未修改原站、部署配置、systemd 示例、实施计划、控制器、控制状态或批准原型。

▶ 未完成／未验证与接续
• 生产发布、生产迁移、真实供应商验证均「未完成」。没有调用真实 agy／Agnes、读取 .env／真实凭据、改 Google 授权、启用收费回退或开放公网；产品文本模型仍为 gemini-3.8-flash-low。
• 本轮只修复阶段 05 离线传输与事实保存边界；真实网络／权益证据采集、宿主隔离、停止逃逸、长时常驻、媒体质量及真机播放仍由默认拒绝闸门保护。本轮没有启动前端阶段；npm test／build 与 test:e2e 仍分别从阶段 06／07 提供。
• 尚未可靠取得状态的握手／连接／HTTP 解析失败，仍不能凭猜测写入供应商限流；提交已在途也不能撤回。SQLite 写盘成功是事实跨重启保留的前提，不承诺数据库损坏恢复或跨系统原子事务；未知任务仍不自动重发。
• 后续先独立复审阶段 05，再按既定阶段进入前端；下一迁移仍只能从 9 追加，不改写 1–8。已有未提交文档／代码／原型全部保留；未 commit／push／reset／clean／merge，没有切换或发布旧站。

---
▶ In brief
• What's happening：两处特殊故障已修复，675 项检查通过，等待独立复审。
• Reason：现在后发生的错误不会盖住对方的暂停通知。
• Impact：旧网站不变；真实生成与正式发布仍未完成。
• Require Input：不需要 input，我继续按已批准流程交接。

【2026-09-09 恢复尝试 1：供应商持久暂停与 MCP 错误隔离】

▶ 当前结论
• 本轮已完成两项指定审查缺陷的实质修复与离线回归，可交独立复审。最终固定门槛实际 `625 passed in 138.02s`：保留既有 589 项，新增 36 项。不是生产发布、真实供应商验收或整站完成。
• 完整阅读指定实施计划、Spec v1.0、API 文档、全部阶段／巡检记录，以及本任务指定审查报告 `.superpowers/autodev/logs/05-cli-3-reviewer-1788902090318538206.final.md`；核对实际 Agnes／SafeHTTP／EvidenceGate／任务入队与 worker／MCP／迁移及测试代码，读取 Task／Problem／GenerationPolicy 与递归请求完整 schema。未读其他会话、真实凭据或 .env；不以旧报告通过声明替代实测。

▶ 供应商暂停修复
• 追加迁移 8 `provider_limits`，仅存 Agnes 限流观察时间与等待截止，不存原始响应、请求、URL、凭据或客户内容。旧迁移 1–7 未改写；原 v6→v7 测试明确限定升级目标为原七项迁移，保留全部旧断言，另增 v7→当前版本的账号／完整历史／外键／重复迁移回归。
• SafeHTTP 在读取错误正文前识别 HTTP 429，抛只携带等待提示的类型化异常并关闭响应。Agnes 在独立短事务持久保存限流事实；错误 HTML、坏 MIME、压缩头、声明超大容量或后续正文读取失败不能掩盖已经收到的限流状态。可信注入传输直接返回 429 的边界也同样保存。
• Retry-After 支持整数秒与 HTTP 日期；缺失／无效提示保守等待 300 秒，零秒至少等一秒；合法长等待时间不截短，多次／并发／晚到观察只延长截止。没有后台自动清除或普通管理员解锁入口。
• 入队和实际提交前通过 AgnesProvider.check 读取持久事实；重建 API 服务对象、适配器或独立 worker 进程不能清除。已经领取但尚未记录 dispatched_at 的任务发现限流会回到 queued，显示 PROVIDER_LIMIT_REACHED，不发送请求。普通管理员开关不能覆盖供应商暂停。
• 冷却结束仍要求有效且晚于最近限流事实的免费／超额／限额／隔离证据；EvidenceGate 返回本次已经验证的同一不可变证据，不重新读取一份未验证快照。旧快照、未知／过期／非免费证据均不放行。当前单一 Agnes 授权限额范围未核明时保守同时暂停图片与 AI 视频，不影响文字或本地运镜，不以更换模型／收费通道绕过。
• 返回 429 的 POST 仍保留 submission_unknown，无查询 ID 不可 recover，更不能重新 POST；已持有供应商 ID 的原任务继续查询／下载同一结果，查询自身返回 429 也保存暂停事实但不取消原任务。幂等重放仍返回原受理资源，不创建第二任务。冷却与新证据都满足后只执行原排队任务一次，不重做未知任务。

▶ MCP 错误隔离修复
• read_task 先严格校验字段、字符串类型、36 位 UUID 格式，再查询归属和当前轮次；整数／布尔／null／列表／对象／坏字符串不再进入会抛 AttributeError 的 UUID 转换路径。合法但不可见的编号仍 RESOURCE_NOT_FOUND，停止后仍拒绝查询，没有放宽权限。
• RPC 同样拒绝非对象包。stdio 完整有界坏 JSON、坏 UTF-8、重复字段、非对象、过深嵌套返回脱敏错误后继续处理下一帧；参数嵌套最多 32 层，不依赖 Python 版本的递归上限。超长或不完整帧仍拒绝关闭，原容量安全测试保留。未将全部内部异常一律吞掉或假装成功。

▶ 先失败再修复与过程证据
```bash
.venv/bin/python -m pytest backend/tests/test_cli_limits_revision.py -q
```
• 首批正式回归在产品修复前实际 `5 failed, 3 passed in 2.16s`：新任务仍 202；整数／布尔／列表／对象编号使网关退出，后续 tools/list 无响应。不是原审查内存插件的原样重跑。
• 首次修复后媒体／网关／恢复联合专项 `80 passed in 13.85s`。补测曾 `1 failed, 32 passed`：新测试将取消任务误写为 202，实际 API 与设计 OpenAPI 都明确为 200；核对契约后修正测试并同时断言 canceled，没有改变原行为或放宽安全预期。
• 第一轮全量 `623 passed in 137.59s`；随后加入过深 JSON 与原始注入 429 边界。深度测试原先假设 2000 层一定触发解析异常，实际 Python 可完成解码，于是返回一般参数错误，全量 `1 failed, 624 passed in 137.61s`；没有改成任选错误码通过，而是实现确定的 32 层解析边界，保留解析错误及下一合法帧必须成功的断言。
• 新文件导入顺序、循环闭包绑定及格式问题均实际修复。最终新增专项与原网关联合 `61 passed in 11.85s`；没有删测试、降低安全条件、改契约或屏蔽警告。

▶ 最终固定门槛
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 后端 `625 passed in 138.02s`，Ruff `All checks passed!`、格式 `81 files already formatted`；全部命令退出码 0。全量包含既有三个自管 127.0.0.1 HTTP 冒烟、真实假 CLI 子进程、崩溃恢复及本地合成图片运镜，不是只运行新测试。
• 文档契约：40 路径／51 操作／64 schema／525 引用、22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称官方完整结构校验；实际全量契约严格差异仍属于阶段 07。原站与列明部署文件无差异。
• 新增独立 Python worker 进程从同一合成库读取暂停，故意提供新有效权益证据与会记录任何意外调用的假传输，仍保留 queued／PROVIDER_LIMIT_REACHED，未产生第二次提交。未知任务状态不变；子进程已退出，没有真实网络请求。

▶ 有界重复专项
通过前台 subprocess.run(check=True, timeout=120) 串行两次执行：
```bash
.venv/bin/python -m pytest backend/tests/test_cli_limits_revision.py \
  backend/tests/test_cli_confirmation.py backend/tests/test_cli_revision.py \
  backend/tests/test_agy_adapter.py backend/tests/test_tool_gateway.py \
  backend/tests/test_media_adapters.py backend/tests/test_adapter_recovery.py -q
```
• 分别 `181 passed in 33.70s`、`181 passed in 33.26s`，均退出码 0；不启动后台循环或嵌套 agent。包含原有完整确认绑定、正文／动作分离、真实本地运镜故障恢复及本轮新测试，各测试自行回收其子进程。

▶ 本轮修改文件
• 新增：`backend/src/omniflow/provider_limit_schema.py`、`backend/src/omniflow/provider_limits.py`。
• 修改：`backend/src/omniflow/db.py`、`provider_gate.py`、`agnes_adapter.py`、`safe_http.py`、`tool_gateway.py`。
• 新增测试：`backend/tests/test_cli_limits_revision.py`、`backend/tests/provider_limit_process.py`。
• 修改测试：`backend/tests/test_adapter_recovery.py`（只限定既有 v6→v7 测试目标，不改其历史保留预期）。
• 文档：`backend/README.md`、`docs/progress/05-cli.md`。合成测试数据和读取用 schema 提取文件在忽略的 backend/var，不属于源码交付。
• 没有依赖变更、安装或重新构建；没有修改任务／run 状态机、公开接口契约、旧迁移、批准原型、原站／部署配置、systemd 示例、实施计划、控制器或控制状态，保留全部先前未提交文档与代码。

▶ 未完成／未验证与接续
• 生产发布、生产迁移、真实供应商验证均「未完成」。未运行真实 agy／Agnes、未读真实凭据／.env、未改 Google 授权、未开收费回退、未开放公网；产品文本模型仍固定 gemini-3.8-flash-low。
• 真实证据采集、launcher／授权挂接、宿主隔离与停止逃逸、真实常驻、真实限额范围与存储出口、媒体质量及真机播放仍保留默认拒绝闸门；本轮实现并验证的是离线协议与状态闭环，不能冒称真实权限／费用已经核验。
• 供应商事实落盘后的新检查会暂停；已经越过最终检查且在途的请求不能被事后召回，外部请求与 SQLite 仍不是原子事务。冷却结束也不保证供应商已恢复；需要新可信证据。当前无备份，不承诺数据库丢失或损坏后恢复限流／作品事实。
• 下一步独立复审阶段 05，之后按阶段 06 接前端；npm test／build 和 test:e2e 分别从阶段 06／07 提供，本轮不越阶段。下一迁移从 9 追加，不能改写 1–8。

---
▶ In brief
• What's happening：两处问题已修复，625 项检查通过，等待独立复审。
• Reason：对方要求暂停时会记住，输错一次也不会打断后续操作。
• Impact：旧网站不变；真实生成与正式发布仍未完成。
• Require Input：不需要 input，我继续按已批准阶段接续。


【尝试 3：确认事实接入可执行视频路径】

▶ 当前结论
• 本轮完成指定 P1 的实质修复与完整离线回归，可交独立复审。固定后端命令实际 `589 passed in 132.02s`，保留全部既有 567 项，新增 22 项；不是生产发布或真实供应商验收。
• 已完整阅读实施计划、Spec v1.0、FastAPI 接口文档、全部已有阶段记录及指定审查报告 `.superpowers/autodev/logs/05-cli-2-reviewer-1788901019784022452.final.md`；核对实际 RunManager／AgySession／ToolGateway／TaskService／素材确认及 worker、假进程、测试和相关完整递归 schema。未读取其他会话日志或真实凭据。
• 审查指出的遗漏成立：公开 Message 没有确认标识，AgySession 只发送当前正文／权限／附件／选中版本；旧网关却要求模型自行提交不可见 ID。原测试直接替模型填 ID，不能证明完整对话路径。本轮不改公开 Message，也不把用户确认降级为模型声明。

▶ 实现
• `tool_gateway.py`：keyframe 工具省略 reference_confirmation_id 时，只从该活动 run 原始可信 input_json 补齐；没有本轮证据仍拒绝。若模型显式提供，必须与原记录严格一致，null、随机值、别的确认（即使同图同人）均拒绝。不查「最近确认」、旧轮或后排，不修改传入参数对象，不新增确认记录。
• `tools/list` 的内部视频字段说明明确告知「可省略，由服务端绑定」。保留显式同标识调用的兼容性；补齐后的规范请求参与原有 run＋动作标识幂等，相同动作的省略／显式形式只产生同一任务。
• `tasks.py`：创建工具任务的同一写事务再次核对 reference_confirmation_id 与原 run 输入一致，且确认属于该用户、对话及原选中版本；原有有效图片、允许版本范围、账号、租约、停止、费用及数量检查不变。先读取证据后发生删除也不能新建任务。
• 公开 POST /tasks 的 keyframe 请求仍必须显式提供确认，缺少字段仍 422。没有改接口契约、迁移历史、真实启动入口或媒体适配协议；产品模型仍固定 gemini-3.8-flash-low。

▶ 新增 22 项回归
• 真实 API 上传合成图→用户确认→发送带确认的消息→原官方协议假进程→实际 AgySession／RunManager／ToolGateway→持久视频任务→关闭文字进程→重建媒体 worker 收尾→刷新作品关联→GET／HEAD／Range 下载。分别覆盖有／无 selected_version_id；捕获实际 stdin，确认模型没有收到／猜中确认标识，工具参数也没有旁路注入 ID。
• 假提供方账本只记录一次提交，输入是精确确认版本；只有一条确认记录与一份任务。合成视频实际 1.25 秒仍如实返回，不冒充请求的 4 秒或真实 AI 生成；其他用户下载仍 404。
• 内部 MCP 省略确认成功、重建网关幂等重放、显式同确认语义重放、停止后重放拒绝；原参数没有被补值污染。
• 模型改选随机／同版本另一确认／另一版本／另一对话／另一用户／null 六类拒绝；消息 API 另一用户／对话／版本／已删除确认四类拒绝，均未启动 CLI。
• 未绑定确认、后排确认、旧轮确认三类完整 CLI 拒绝；常驻两轮与明确恢复两轮分别绑定自身精确版本，后排图片不会进入前轮 stdin。
• 消息受理后删除、网关读取之后且任务事务之前删除两种交错拒绝；事务前注入另一确认也拒绝；公开任务字段要求不放宽。

▶ 先失败、再修复及测试证据
```bash
.venv/bin/python -m pytest backend/tests/test_cli_confirmation.py -q
```
• 产品修复前，两项完整路径测试实际 `2 failed in 1.04s`：有／无选中版本的 run 均 failed 而非 completed，与指定审查问题一致。未直接重放其内存插件，不将本轮测试说成原插件。
• 产品修复后任务已正常 completed，新测试对假提供方 inputs 误当作对象列表，实际为版本 ID 列表，得到 `2 failed in 1.16s`；核对 worker 的真实输入后更正测试取值，保留精确版本断言，不修改提供方或业务预期。随后相关联合专项 `46 passed in 10.43s`。
• 完整新增专项 `22 passed in 7.75s`；首轮新文件导入／未用项／长行已实际修复，Ruff 全后端检查通过。没有删旧测试、改安全预期或屏蔽警告。
```bash
.venv/bin/python -m pytest backend/tests -q
```
• 最终固定后端门槛 `589 passed in 132.02s`。包含既有三个回环 HTTP 冒烟、独立进程崩溃恢复、结构化正文分离及真实合成本地运镜测试；不复用上轮声明代替实测。

▶ 最终静态检查及有界重复专项
```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 全部退出码 0：Ruff `All checks passed!`，格式 `77 files already formatted`；契约 40 路径／51 操作／64 schema／525 引用、22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称完整官方结构验证。列明原站与部署文件无差异。
• 通过前台 `subprocess.run(check=True, timeout=100)` 串行两次执行以下联合专项，没有后台循环或嵌套 agent：
```bash
.venv/bin/python -m pytest backend/tests/test_cli_confirmation.py \
  backend/tests/test_cli_revision.py backend/tests/test_agy_adapter.py \
  backend/tests/test_tool_gateway.py backend/tests/test_media_adapters.py \
  backend/tests/test_adapter_recovery.py -q
```
• 分别 `145 passed in 27.45s`、`145 passed in 27.21s`，均退出码 0；每次重新执行自身假进程、硬退出恢复及实际合成运镜，测试自行回收子进程。没有安装依赖或重新构建包，不借用旧构建证据。

▶ 本轮修改文件
• `backend/src/omniflow/tool_gateway.py`
• `backend/src/omniflow/tasks.py`
• 新增 `backend/tests/test_cli_confirmation.py`
• `backend/README.md`
• `docs/progress/05-cli.md`（本节；下方历史证据保留）
• 未修改 AgySession／RunManager、假 CLI、依赖／锁文件、迁移、公开契约、批准原型、原站／部署配置、systemd 示例、实施计划、控制器或控制状态；原有未提交文档／代码保留。

▶ 未完成／未验证与接续
• 生产发布、生产迁移、真实供应商验证均「未完成」。未运行真实 agy／Agnes、未读 .env／真实凭据、未改 Google 授权、未启用收费回退、未开放公网；本地测试仅合成账号／图像／视频及假提供方。
• 真实 launcher、授权挂接、费用证据采集、宿主隔离／停止逃逸、长时常驻、媒体质量和真实播放验证仍是独立默认拒绝闸门。本轮不因这些闸门阻塞纯离线修复，也不冒称已经核验。
• 自然语言仍采用既有保守明确语法；确认仍通过用户接口；没有新增模型自行确认、完整自然语言质量验收或图片像素输入能力。
• 下一步独立复审阶段 05，之后阶段 06 前端；npm test／build、test:e2e 分别从阶段 06／07 提供，本轮不跨阶段。全量实际 OpenAPI 严格差异与浏览器联调仍留阶段 07；下一迁移仍从 8 追加。

【尝试 2：独立审查缺陷修复与实际复测】

▶ 当前结论
• 本轮仅完成阶段 05 的两项 P1 修复与离线回归，可交独立复审。固定后端命令实际为 `567 passed in 123.89s`，原有 544 项保留，新增 23 项；不是生产发布或真实供应商验收。
• 已完整重读实施计划、Spec v1.0、FastAPI 接口文档、全部已有阶段进度，以及本任务指定审查报告 `.superpowers/autodev/logs/05-cli-1-reviewer-1788899651480771015.final.md`。核对实际适配器、网关、管理器、worker、存储、验证、测试及相关完整递归 schema；重新完整阅读此前保存的官方 headless 公开文档缓存，未读其他私有会话或真实凭据。
• 下方首次编码报告原样保留。其「工具参数不进入正文」「本地稳定文件可恢复」结论并不覆盖审查发现的两个窗口，不能继续以旧 544 项通过作为这两项行为的证据。

▶ 结构化协议与公开正文修复
• 新增 `agy_schema.StructuredReply`，以严格封闭的 `{text, actions}` 分离正文与动作；动作名称限现有五工具，最多八项，参数仍经 ToolGateway 的请求模型与归属／意图／租约事务校验。模型的结构化声明不是执行授权。
• 启动计划显式带 `--json-schema`；init 与 result 必须返回相同 schema。response 只检查是否为 structured_output 同一对象的序列化，不能从自由文本或混杂说明截取动作。
• 所有原始 agent_response 增量有界缓存，不直接发布。完整终态、轮次、schema、正文／动作结构与累计统计全部通过后，才把专用 text 交付为 TextDelta，动作另交网关。结构化模式目前在终态一次交付正文，不宣称逐字预览；断线或取消前尚未验证的 JSON 不保存为用户可读片段。
• 假进程现遵守官方的 response=json.dumps(structured_output)，并分片发送同一 JSON；不再靠另一段自然语言 response 隐藏差异。真实 RunManager→ToolGateway→TaskService 闭环仍产生一份任务，同时实际消息 API、快照消息与 ASGI SSE 消息事件只含正文。
• 公开 Task.requested_parameters 按契约本来就含任务参数，本轮没有删除此字段或篡改事件契约。回归断言针对误公开的助手正文，而非把任务资源也改成无参数。
• 新增旧 actions-only／缺字段／schema 不符／对象不符／重复 JSON 字段／坏正文／坏动作／过量动作／坏统计的逐字符分片拒绝，以及真实假进程在增量后断线的 API／SSE 空正文验证。

▶ 本地视频发布后、receipt 前恢复修复
• worker 在领取前核对已 dispatched、无提供方 ID／receipt 且租约已过期的 local-ffmpeg 任务，只读取该任务入队时预留的稳定输出路径；不扫描临时文件猜结果，也不重新执行运镜。
• 复用私有普通文件、无符号链接与容量校验；新增本地输出校验函数，核对可识别视频的实际时长、目标画幅与 24fps。FFmpeg 发布前与缺 receipt 恢复共用此函数，不把远端供应商的 requested 冒充 actual。
• 文件读取及 ffprobe 在写事务之外；随后重新核对状态、租约令牌／期限、提交事实及版本 ID，同事务补 receipt、接回 running 和事件，再走原 poll→save 唯一版本链路。活跃提交者或校验期间续租不接管；两个并发扫描者只有一个接回成功。
• 独立 Python 故障入口在实际 FFmpeg 合成运镜 publish 成功后执行 os._exit(78)，确认 receipt 尚不存在。三次独立重启后任务 completed，原文件 inode 不变，仅一次运镜记录与一个 source_task_id 版本。另覆盖 SystemExit 同一窗口、无文件、损坏文件、时长／画幅不符、符号链接、公共文件权限及无 dispatched 事实拒绝。
• 恢复仅使用已完成本地文件，不需要重新获得生成权益，也不会启用真实供应商。坏结果仍待核对，不删除输入或将未知提交改回 queued 冒做一遍；未改迁移 1–7。

▶ 先失败再修复证据
```bash
.venv/bin/python -m pytest backend/tests/test_cli_revision.py -q \
  -k 'only_public_text or restores_same_file_once'
```
• 产品修复前 `2 failed, 7 deselected in 1.12s`：符合新正文／动作分离的官方序列化结果在旧实现进入 needs_reconciliation；本地文件确实落盘但仍 submission_unknown，不能完成恢复。这是本轮测试的实际失败，不把它冒称原审查内存测试已原样重跑。
• 产品修复后联合适配专项 `84 passed in 13.59s`；随后补齐上述安全／并发回归，新文件专项 `23 passed in 7.47s`。未删除既有测试、改契约、屏蔽警告或放宽权限／幂等预期。

▶ 本轮实际测试
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 后端 `567 passed in 123.89s`；Ruff `All checks passed!`；格式 `76 files already formatted`。固定门槛包含既有三个自管回环 HTTP 冒烟，未降低 Secure Cookie，未开放公网。
• 文档检查：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称全量官方结构验证；实际服务全契约严格差异仍属阶段 07。
• 空白与列明原站／部署文件差异检查均退出码 0。首次开发中的新增代码长行／未用导入已实际修正，最终检查通过。没有依赖变更、重新安装或构建，不借用上轮构建结果当本轮实测。

▶ 最终有界专项复测
通过前台 Python subprocess.run(check=True, timeout=90) 串行重复以下命令三次，没有启动后台循环或其他 agent：
```bash
.venv/bin/python -m pytest backend/tests/test_cli_revision.py \
  backend/tests/test_agy_adapter.py backend/tests/test_tool_gateway.py \
  backend/tests/test_media_adapters.py backend/tests/test_adapter_recovery.py -q
```
• 结果依次 `123 passed in 20.41s`、`123 passed in 19.65s`、`123 passed in 19.79s`，均退出码 0。包含原有 100 项适配专项及本轮 23 项，不只运行新增正常路径；每次均重新运行自己的硬退出／重启子进程、真实合成运镜与 SSE 交付测试。

▶ 本轮修改文件
• 新增 `backend/src/omniflow/agy_schema.py`。
• 修改 `backend/src/omniflow/agy_adapter.py`、`ffmpeg_adapter.py`、`media_validation.py`、`task_worker.py`。
• 修改 `backend/tests/fake_agy.py`；新增 `backend/tests/test_cli_revision.py`、`backend/tests/local_recovery_process.py`。
• 更新 `backend/README.md` 与本报告。没有修改原站、部署示例、控制器、实施计划、控制状态、接口契约、迁移历史、依赖或锁文件；原有未提交代码／文档／原型保留。

▶ 未实现／未验证与接续
• 生产发布、生产迁移、真实供应商验证均「未完成」。产品模型仍固定 gemini-3.8-flash-low；未运行真实 agy／Agnes、未读取 .env／真实凭据、未改 Google 授权、未开启收费回退。
• 真正 launcher、授权挂接、真实权限隔离与费用证据采集仍默认拒绝；真实常驻、停止／逃逸、供应商质量、视频完整播放及宿主隔离尚未验证。本地视频检查是基本有效性校验，不承诺灾损恢复或完整媒体质量。
• 不迁移或清洗已有助手历史正文；本轮修复防止后续原始结构化数据进入正文，既有数据若曾通过旧适配器生成，需另行核对，不自动改写用户历史。
• 下一步先独立复审阶段 05，再进入阶段 06；前端 npm test／build 与 E2E 入口分别从阶段 06／07 提供，本轮没有跨阶段新增前端。下一迁移仍从 8 追加。

【首次编码记录（保留历史证据）】

▶ 本轮结论
• 本阶段离线实现与测试通过，可交独立审查。最终固定后端命令为 544 passed in 116.36s；进入本轮实测基线为 444 passed in 103.91s，保留全部既有测试，新增 100 项。新增专项另外前台有界复测三轮，均 100 项通过。
• 完整阅读实施计划、Spec v1.0、FastAPI 接口文档、docs/progress 全部已有阶段记录；实际核对 RunManager、任务服务／worker、素材授权、配置、迁移、测试及任务／能力完整递归 schema，不以旧成功声明替代实测。
• 本轮仅修改当前 worktree 的 backend/ 与本阶段进度文件。保留已有未提交代码、文档及原型；未改原站 server.py／index.html、部署配置、自动开发控制器、实施计划、控制状态或 Google 授权。没有 commit／push／reset／clean／merge、sudo、嵌套 agent 或后台循环。
• 生产发布、生产迁移、真实供应商验证均「未完成」。未运行真实 agy 或调用 Agnes API；只获取公开文档页面、运行合成协议进程、注入 HTTP 回复和本地合成图 FFmpeg。未读取 .env、真实凭据或其他私有会话；测试服务只绑定 127.0.0.1。

▶ 官方协议依据与发现的差异
本轮实际读取的公开文档：
• https://antigravity.google/docs/cli/headless
• https://antigravity.google/docs/cli/permissions
• https://antigravity.google/docs/cli/settings
• https://agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash
• https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash
• https://agnes-ai.cn/zh-Hans/docs/agnes-video-25
公开页面文本临时保存在 backend/var/public-docs/，属于忽略的研究缓存，不含授权。最后一页仅核对 Flash 文档明确继承的响应／查询协议，未选择非 Flash 模型。
• agy 文档当前页面标示 CLI v1.1.25，历史项目记录为 v1.1.22；没有运行真实 CLI 核对当前安装版本，不宣称两者已真实兼容。
• 官方流输入为 event=user／message.content；输出为 init、step_update、result，不支持 control_request/control_response。只支持文本块，不发送伪造图片块；当前消息附带的是版本标识，不冒称 Gemini 已读取图片像素。
• result.status 必须明确检查；退出码 0 不等于业务成功。num_turns／usage／duration_seconds 是会话累计快照，不能逐轮相加；重复 result 不得完成下一轮。
• 权限优先级 Deny > Ask > Allow；因此只精确允许必要 MCP，不加会覆盖它们的 mcp(*) deny/ask。工作区默认读写放行与 --sandbox 均不能代替完整宿主隔离证明。
• Agnes 图片参数表提及顶层 image，但「重要说明」及全部图生图示例使用 extra_body.image；实现采用后者，且 response_format 放 extra_body。没有在失败后换字段重发 POST，也没有静默切模型。
• Agnes 图片为同步 data[0].url，不虚构外部异步图片任务 ID；视频的 video_id 才用于 /agnesapi 查询，不能用 id/task_id 替代。seconds 发送字符串，所有视频查询都带固定 Flash model_name。
• 公开价格和 useG1Credits 字段不是账号权益／账单证据，本轮未将它们转为真实放行条件。

▶ 已实现：官方常驻适配与安全闸门
• AgyProvider／AgySession：固定 gemini-3.8-flash-low、双向 stream-json、--sandbox；每个 owner／应用对话独立 HOME 与 cwd。新对话不带恢复参数；旧对话只接受已绑定明确 UUID 的 --conversation，禁止全局 --continue。CLI ID／路径／进程信息不进入公开消息。
• 常驻／明确恢复仅发送当前用户消息，不重复发送整段旧历史，不把后排消息带入。本轮 result 完成前拒绝下一次 send，重复／跨会话 result、错误型号／权限 init、未知帧、过长输出及断线均不能误报成功。
• TextDelta 仅来自 agent_response；tool_info.output、工具参数、思考、路径和原始异常不进入正文，也不将观测到的工具日志再次执行。SUCCESS 才结束本轮；ERROR 显示失败，其他不能明确核清的状态保守待核对。
• ProcessTransport 使用无 shell 的参数数组、有界非阻塞读写、单帧 256KiB、重复 JSON 字段拒绝与独立进程组。只处理自己创建的进程；close 必须确认退出，组内仍有子进程不释放原持久占用。stop 需要官方 INTERRUPTED/CANCELED 与关闭确认，单纯终止进程不伪装成功取消。
• RunManager 接入 typed structured_output 动作和稳定错误码；执行前检查本地证据，媒体任务仍通过统一服务创建。停止文字不取消已经受理的媒体任务；现有租约、打开占用和未知轮次不重放测试保持通过。
• EvidenceGate 只读取可信宿主注入的短期不可变证据：精确模型、免费、超额关闭、限额、可用与隔离，证据最长 300 秒，未知／过期／未来／型号不符／字段非严格布尔均默认拒绝。常驻每轮及媒体提交前再次核对，不提供环境变量、HTTP 或管理员开关来伪造放行；没有收费回退或业务日额度。

▶ 已实现：受控 MCP 与创作动作
• ToolGateway 提供专用 stdio 的 MCP JSON-RPC initialize、tools/list、tools/call；没有匿名 HTTP 通用工具端点。RunScope 由可信宿主绑定 owner／conversation／run／manager token，模型不能指定这些字段或任意 URL、路径、shell、模型、凭据、confirmed 和动作幂等键。
• 工具仅 submit_image、submit_video、submit_local_motion、read_task、list_artifacts。查询也校验活动轮次、租约、账号状态和授权代数；discuss_only 允许受限读取但禁止生成。
• 网关从原用户消息建立保守明确意图证据，不凭关键词或模型声明执行。支持明确单张／1–8 张图片、明确直接文生视频、已确认图片视频及本地运镜；默认一份，数量由用户原动作限制，并在创建事务内原子核对。多方案逐任务入队，不由模型不断换动作 ID 无限扩增；这不是每日用量配额。
• 当前安全语法示例：「请生成一张图片：蓝色方块」「请生成2张图片：不同布局」「请直接文生视频：蓝色海洋」「请用已确认图片生成视频：轻轻移动」「请选择本地运镜：缓慢推进」。其他表达／咨询／疑问／条件或否定句不猜测执行，需要澄清或已有严格任务 API 明确操作；这不是完整自然语言理解质量验收。
• keyframe 必须使用该轮原消息绑定的精确 reference_confirmation_id；模型不能自动确认，也不能把 keyframe 用户请求改成 text 绕过确认。同用户旧库只有显式版本可见，其他用户、其他对话和后排才授权的版本均拒绝。
• 内部动作幂等键由 run＋稳定 RPC 标识或 structured_output 动作序号计算，重放先校验当前权限；同动作不同语义冲突，不创建第二任务。MCP 内嵌文本采用稳定 JSON 序列化，使首次与重放响应一致。
• RunManager＋官方协议假进程＋structured_output＋真实 TaskService＋假媒体 worker 的闭环实际通过：文字结束后媒体继续完成、助手作品关联补齐、只有一次提交。正文中的 JSON 不执行。

▶ 已实现：Agnes、HTTPS 与 FFmpeg
• AgnesProvider 映射固定 Flash 图片／视频协议；图片参考顺序保留用户“第一张／第二张”的语义，不使用数据库 UUID 排序。只为已激活任务的具体输入签发短期 grant；原始 URL 仅发给媒体提供方，不进入公开事件、任务响应、幂等账本或日志。
• 追加迁移 7 adapter_receipts，旧迁移 1–6 不变。同步图片结果先存可续查线索；若线索落盘后返回 ID 丢失，worker 重新找到该线索，仅接回原结果，不重新 POST。没有线索的未知提交继续阻塞，不能借此自动重做。
• 视频保存真正 video_id；重建适配器后仍查询原 ID，完成后下载 metadata.url。下载失败保持 saving 并重取原结果；requested_parameters 与实际解码规格分开。合成视频请求 4 秒、实际 1.25 秒／32×48 的差异继续如实保留。
• SafeHTTP 精确 HTTPS 主机白名单、全部 DNS 地址校验（拒绝私网、回环、混合地址、组播及特殊转换范围）、固定连接 IP、原 hostname TLS 校验、不使用代理、不自动重试 POST。拒绝全部重定向、压缩响应、超限和截断响应；每次关闭响应。存储 GET 不携带 Agnes Authorization。
• PinnedHTTPSExchange 已实现并用注入 socket／TLS／HTTP 对象验证连接地址、主机校验及关闭纪律；未进行真实 TLS／供应商网络验证。真实存储域名白名单默认空，必须独立核对后注入。
• FFmpegProvider 实现 dolly_in、pan_left、pan_right、dynamic_float；只从任务占用读取真实图片，安全解码后重编码 RGB PNG，经 pipe 传入固定参数与滤镜。固定 H.264／24fps，明确 local-ffmpeg，无任意命令、URL、路径或滤镜字符串透传，有 90 秒时限与文件容量限制。
• 本轮四种运镜均使用合成图片实际执行宿主已有 FFmpeg；输出 1280×720、24fps、1 秒，重启适配器后续查／保存／GET／Range 通过。输出使用稳定版本文件，worker 校验后才发布 completed，不冒充 AI 视频。
• FFmpeg 执行文件在可信宿主环境先解析为绝对位置，再使用精简子进程环境；没有安装或修改全局运行时。其固定命令与 ffprobe 基本检查不是 OS 沙箱，也不代替真实视觉／播放质量验收。

▶ 待审查 systemd 文本
• backend/examples/systemd/omniflow-api.service.example
• backend/examples/systemd/omniflow-runs.service.example
• backend/examples/systemd/omniflow-media.service.example
三份都是未安装、未启用的示例：不存在的 REVIEW_REQUIRED 路径／用户、显式审批 Condition、只允许独立数据目录写入、组级停止、仅回环网络、真实提供方 disabled，无 [Install] 自动启用目标。仅离线检查文本安全条件，没有启动 systemd 服务或修改现有部署。

▶ 实际测试命令与结果
从当前 worktree 根目录前台执行，全部使用临时合成数据：
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_health.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_artifacts.py
```
• 最终后端：544 passed in 116.36s，退出码 0。
• Ruff：All checks passed；格式：73 files already formatted。
• 契约：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称全量官方结构校验；运行时全契约严格差异仍留阶段 07。
• 三份回环 HTTP 冒烟分别验证健康／迁移／重启、对话／SSE／信号关闭、素材／HEAD／Range／grant／墓碑／清理，全部通过且自行回收子进程。手工合成 Cookie 的回环测试不是浏览器 HTTPS 验收，未降低 Secure Cookie。
• 通过前台有界 subprocess.run(check=True, timeout=90) 串行重复三次：
```bash
.venv/bin/python -m pytest backend/tests/test_agy_adapter.py \
  backend/tests/test_tool_gateway.py backend/tests/test_media_adapters.py \
  backend/tests/test_adapter_recovery.py -q
```
结果依次 100 passed in 13.12s、100 passed in 12.75s、100 passed in 12.66s。覆盖实际假进程、多轮／A-B-A 映射与恢复、未知停止／重复 result／断线、工具数量竞争、同步结果线索恢复、HTTP 安全与四种实际合成运镜。

```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" uv pip check --python .venv/bin/python
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
uv build --project backend --out-dir "$PWD/backend/var/dist" \
  --python "$(command -v python3)" --python-preference only-system
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 已安装 36 包兼容；未新增依赖、修改锁文件或全局环境。
• sdist／wheel 构建成功。构建器提示缓存目录位于源码树，随后实际检查归档：sdist 81 项、wheel 45 项，包含新适配器，不含 var／.venv／.env。构建产物位于 backend/var/dist/，不是部署。
• 空白及列明原站／部署文件差异检查均退出码 0。

▶ 失败与修复证据
• 首次新媒体／网关专项：5 failed、54 passed。MCP 内嵌 JSON 首次与重放键序不同，修复实现为稳定序列化；FFmpeg 精简 PATH 未包含宿主已有二进制，实际诊断 FileNotFoundError 后修复为预先解析绝对位置。未降低任何成功／恢复／安全预期。
• 临时诊断首次在 pytest 配置前导入业务模块触发 warnings-as-errors；改为 pytest_configure 插件诊断，没有屏蔽警告或修改测试配置。
• 首次全量：1 failed、529 passed。唯一失败是旧 v5 升级测试把当前版本固定为 6；新增迁移 7 后改为 MIGRATIONS 最后版本，保留全部旧账号／版本／文件／历史不变断言，并新增独立 v6→v7 保留全部历史校验值测试。没有修改已执行迁移或设计契约。
• 新文件初次静态检查的导入顺序／未用导入／长行已实际修复，最终 lint 与格式检查通过。没有删测试、改业务预期或修改自动开发工具来伪造通过。

▶ 修改文件
新增源码：
• backend/src/omniflow/adapter_schema.py
• backend/src/omniflow/agy_adapter.py
• backend/src/omniflow/agnes_adapter.py
• backend/src/omniflow/ffmpeg_adapter.py
• backend/src/omniflow/process_transport.py
• backend/src/omniflow/provider_gate.py
• backend/src/omniflow/safe_http.py
• backend/src/omniflow/tool_gateway.py
修改源码：
• backend/src/omniflow/db.py
• backend/src/omniflow/conversations.py
• backend/src/omniflow/tasks.py
• backend/src/omniflow/task_worker.py
• backend/src/omniflow/run_manager.py
新增测试：
• backend/tests/fake_agy.py
• backend/tests/test_agy_adapter.py
• backend/tests/test_tool_gateway.py
• backend/tests/test_media_adapters.py
• backend/tests/test_adapter_recovery.py
其他修改／新增：
• backend/tests/test_task_invariants.py（当前迁移版本断言扩展）
• backend/examples/systemd/ 下三份 .service.example（见上）
• backend/README.md（安装／离线注入／安全闸门与接续说明）
• docs/progress/05-cli.md（本报告）
本地 var 测试缓存、公开文档缓存、构建产物不属于源码交付。原有未提交文档／原型／代码保留。

▶ 未实现／未验证及下一阶段接续
• 「未完成」生产发布、生产迁移和真实供应商验证。真实 agy 常驻长时行为、真实停止与受控子进程逃逸、宿主秘密／Google 授权安全挂载、远端超额开关／账单权益、实际存储出口和供应商能力／质量均未核验。
• 真实 agy launcher／Google 授权挂接／全局 MCP 注册与真实证据采集器故意未接到运行入口。AgyProvider 默认 launcher=None，Agnes 默认无 HTTP／密钥／证据，下载白名单为空；Settings 不允许 production 注入 mock。已有可注入协议实现与离线闭环不能被表述成真实提供方已上线。
• LaunchPlan 的权限对象只是可信启动器必须落实的计划，本轮没有把它写到真实 HOME。专用 MCP stdio 宿主需绑定 RunScope；真实运行前必须审核启动器确实只加载这一网关与独立受控上下文，不能仅靠提示词或测试证据放行。
• 自然语言确认仍走明确用户 API；保守语法之外的表达不能静默执行。没有做 Gemini 图片像素输入、完整自然语言覆盖、图生图保真、视频参考输入或不透明回退。Flash 不支持的视频输入继续拒绝。
• 无证据的 submission_unknown／CLI uncertain 占用仍没有强制解锁后门；不能改库、删占用或重排来演示恢复。有同步结果 receipt 的恢复只续查原结果，不重新生成。
• 迁移下一版从 8 追加，不能改写 1–7。前端阶段 06 继续复用现有真实 API／SSE／任务状态；假提供方只在 tests 或明确 mock 开发注入中使用。npm test／build 从阶段 06 提供，test:e2e 从阶段 07 提供，本轮不越阶段创建前端。
• 浏览器 HTTPS／代理／长连接、Mac／iOS 真机播放、长时负载与完整媒体解码仍未验证；无备份，不承诺误删或磁盘损坏恢复。systemd 只提供待审查示例，未安装定时清理或应用服务。

---
In brief
• What's happening：对话到图片、视频处理的离线连接已完成，544 项检查通过。
• Reason：每段聊天分开，重复动作只做一次，没弄清结果时不擅自再做。
• Impact：旧网站不变；真实生成、真实费用核验和正式发布仍未完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
