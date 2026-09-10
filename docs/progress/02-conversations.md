【02-conversations：对话、轮次与事件】

【尝试 3：门槛偶发失败定位、停止确认加固与实际复测】

▶ 当前结论
• 阶段 02 离线实现与测试通过，可交独立复审；最终固定后端命令为 `282 passed in 49.24s`，修复后全量连续三次通过，保留原 267 项并净增 15 项。下方尝试 1／2 的结果仅作历史记录；不以它们替代本轮实测。
• 本轮完整重读指定实施计划、Spec v1.0、FastAPI 接口文档、docs/progress 已有记录及当前任务指定门槛日志 `.superpowers/autodev/logs/02-conversations-2-gate-2-1788891084813210238.log`；核对实际服务、模型、迁移、管理器、假 CLI 和事件测试，读取相关完整请求／资源／事件 schema。没有修改契约、实施计划或控制器来绕过失败。
• 实质修改包含产品管理器停止确认校验、竞争测试的确定性同步及新增故障注入回归。未推进素材、媒体任务或前端阶段；生产发布及真实供应商验证仍未完成。

▶ 门槛失败的实际原因与修复
• 指定日志实际是 `test_two_managers_cannot_execute_same_conversation` 失败、`266 passed`，不是仅有泛化的 process_or_model_error。进入本轮原代码复跑却为 `267 passed in 44.04s`，因此一次全绿不足以排除时序故障。
• 原测试看到 `FakeProvider.opened` 后即向假 CLI 写入 release。该列表只说明握手完成，管理器可能还没从 open 返回、更没发送本轮 history；release 抢先到达时被假 CLI 当成轮次，因没有 history 而退出。管理器将断线轮次置为 needs_reconciliation、阻止下一轮是正确的安全行为，不能把它改成重试／继续来迎合测试。
• 用 threading.Event 精确暂停 open 返回，保留原测试的发送顺序，实际复现 `1 failed, 1 passed, 40 deselected in 0.86s`；新增的首轮必须 completed 断言捕捉到 needs_reconciliation。不是仅猜测原因，也未给测试增加随意 sleep 或自动重试。
• 修复的是测试输入顺序：先允许 open 返回，等待第一条真实增量已入库，再发送 release。保留「竞争者不能执行、原持有者能执行后续轮次、只开一个进程」全部原预期；新增打开中／执行中／两轮之间三处互斥核对、首轮与后轮实际 completed、两轮真实 history 数量／正文及后排不可提前见断言。
• 测试参数同时覆盖自然调度和确定性延迟 open 两种情况。未修改假 CLI 去吞掉错误控制帧，未放宽断线后的待核对规则。

▶ 额外发现并修复的停止确认边界
• 管理器原先把 `session.stop()` 的一般真值当作停止确认。合成适配器返回整数 1、字符串 "false" 或含 stopped=false 的字典时，轮次会误报 canceled；发送前取消的 False／None 分支虽为 needs_reconciliation，却缺少 RECONCILIATION_REQUIRED 错误信息。
• 两处停止调用均改为只接受严格的 `True`，与原有 close 确认纪律一致；发送前的未知停止补齐稳定错误码。其余值或异常均保留待核对与已输出内容，后排不能盲目执行；不会把关闭进程成功当作该轮结果已核清。
• 新增 14 项测试：发送前／已输出后两个时点，分别注入 False、None、1、"false"、字典、超时异常及 True 正常对照。修复产品代码之前实际 `8 failed, 6 passed in 5.60s`；修复后全部通过。它们证明的是适配器边界的防守性校验，不是声称真实 agy 曾返回这些值。

▶ 本轮修改文件
• `backend/src/omniflow/run_manager.py`：严格停止确认、未知停止错误码与接口说明。
• `backend/tests/test_conversations.py`：保留原互斥测试，加强同步、确定性交错和真实结果／上下文断言；净增 1 项。
• `backend/tests/test_run_stop_confirmation.py`：新增 14 项停止确认故障注入与正常对照。
• `backend/README.md`：补充 stop 的明确确认契约及待核对边界。
• `docs/progress/02-conversations.md`：本节结果与证据，历史记录完整保留。
• 没有改依赖、数据库迁移、假 CLI 协议、原站文件、部署配置、Google 授权、控制状态；没有 commit、push、reset、clean、merge、嵌套 agent、后台循环或新供应商调用。

▶ 实际测试命令与结果
所有命令均在当前 worktree 前台有界执行，仅临时合成数据、假 CLI 及 127.0.0.1 冒烟服务。
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_health.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 固定后端门槛：修复后三次完整执行依次为 `282 passed in 49.57s`、`282 passed in 49.47s`、`282 passed in 49.24s`；后两次用有界前台 `subprocess.run(..., check=True, timeout=180)` 串行复跑，均退出码 0。先前基础、账户、对话／事件／暂停／租约等全部继续通过。
• Ruff：`All checks passed!`、`39 files already formatted`。新文件第一次检查的导入顺序／长行及改动断言的格式问题已实际修正，未屏蔽规则。
• 契约：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过；未提供官方 OpenAPI 元 schema，不宣称全量官方结构校验。
• 两份真实回环冒烟均通过；HTTP／SSE、停止信号 control、重启快照、独立文字管理器消费及默认拒绝提供方均有实际证据，自管进程已退出。没有降低 Secure Cookie，不代表浏览器 HTTPS 验收。
• 空白与列明原站／部署文件差异检查退出码 0。未重新构建包或安装依赖，不引用历史构建作为本轮实测。
• 有界 Python `subprocess.run(..., check=True, timeout=30)` 前台重复以下双管理器专项 10 次，每轮 `2 passed, 40 deselected`，耗时 0.64–0.86 秒，全部退出码 0：
```bash
.venv/bin/python -m pytest backend/tests/test_conversations.py -q -k two_managers
```
• 有界 Python `subprocess.run(..., check=True, timeout=60)` 前台重复以下专项 3 次，每轮 `25 passed`，耗时 12.14／12.20／12.00 秒，全部退出码 0；包含上一轮真实租约等待、独立 Python 竞争者和退出确认交错，不只运行新加测试：
```bash
.venv/bin/python -m pytest backend/tests/test_run_manager_safety.py \
  backend/tests/test_run_stop_confirmation.py -q
```
• 修复后还实际运行 `test_run_stop_confirmation.py` 与 `test_conversations.py` 联合专项，`56 passed in 14.93s`。所有失败前置证据已在上文记录，不把失败命令算成通过。

▶ 未实现／未验证及后续接续
• 本阶段的对话、消息、run、幂等、串行队列、一致快照及 SSE 恢复闭环仍保留并复测；管理员及其他用户不能读取私密对话，订阅不触发生成，注销／停用／重置撤销已有流的测试继续通过。
• 生产发布、生产数据迁移、真实 agy／Agnes 调用、真实费用／超额权益与宿主隔离验证均「未完成」。产品型号仍固定 `gemini-3.8-flash-low`；没有读取真实凭据或 `.env`，没有开放公网。
• 真实 CLI 的 stop／close、受控子进程退出、协议与费用闸门留到阶段 05；本轮只能证明假提供方边界。真实断线不能因这个测试修复而改为自动重放。
• 崩溃或遗留的 uncertain 进程占用仍保守阻塞，无自动按时间／PID 解锁或人工核对接口；不能直接删除占用演示恢复。迁移版本仍为 4，下阶段从 5 追加。
• 阶段 03 接版本／确认／必要素材快照；阶段 04 接任务及 message.updated 作品关联、删除占用校验；当前相关资源不存在，引用拒绝、快照媒体数组为空是真实状态，不是已实现上传／视频。
• 前端、浏览器 E2E、HTTPS／代理长连接和长时常驻压测未完成；npm 测试／构建与 E2E 命令仍分别从阶段 06／07 提供。独立复审及后续分阶段验收由外层继续，不能把本轮 pass 说成整站完成。

【尝试 2：独立审查缺陷修复与复测】

▶ 当前结论
• 本轮为阶段 02 coder，已完成两项 P1 缺陷的实质修复，可交独立复审。最终固定后端命令 `267 passed in 44.07s`，其中此前 256 项全部保留，新增 11 项安全回归。没有推进阶段 03–08，不将测试全绿当作生产上线或真实供应商验收。
• 已完整重读实施计划、Spec v1.0、FastAPI 接口文档、已有阶段／巡检记录及本任务指定审查报告 `.superpowers/autodev/logs/02-conversations-1-reviewer-1788889359497562003.final.md`，核对实际管理器、API、取消、迁移、假 CLI、相关完整 schema 与既有测试。未读取其他私有会话、真实凭据或 `.env`。
• 下方「尝试 1 编码记录」完整保留为历史证据。旧记录关于租约和回收安全的结论被独立审查推翻，本轮以持久进程占用与明确退出确认补足，不能再仅凭原 256 项测试宣称这两个交错已经安全。

▶ 修复内容
• 暂停重查：在 `provider.open()`／绑定返回后、实际 `send()` 前的最后一次短事务复核本地文字开关及既有身份／轮次条件；已领取但未发送时回到 queued，而非 failed、canceled 或 completed。恢复复用同一 run、助手消息 ID 和 seq，不能新建重复助手或把后排消息提前传给 CLI。
• 暂停时保存对应 message.updated、run.updated 和 conversation.updated；已发送的轮次继续读取结果，不用终止已受理工作冒充修复。取消已退回排队的轮次，同时将空助手消息结束为 interrupted，不残留「仍排队」显示。
• 持久进程占用：追加迁移 4 的 `cli_process_holds`，对话主键唯一，记录持有者及 opening／open／uncertain。在调用 open 之前事务落盘，不把租约过期视为退出证明；即使管理器暂停调度、崩溃或打开后丢失句柄，其他管理器也不能重开该对话的 ID／目录。
• 退出确认：`Session.close()` 改为明确返回 bool，必须确认本会话及受控子进程已退出才返回严格的 True。异常、超时、False、None 均保留原句柄和持久占用；局部 drop、全局 close 和其他管理器的清理都不能绕过。原持有者后续可再次尝试确认退出，但已经进入 needs_reconciliation 的轮次仍不会盲目重放。
• 不确定回收会将该对话首个排队轮次显示为 needs_reconciliation／RECONCILIATION_REQUIRED，后排阻塞，其他对话继续执行。空闲租约自然过期但原进程仍存在时，仅阻止接手并保留排队；原管理器确认退出后可正常恢复。管理器崩溃留下的占用没有按时间／PID 自动解锁后门。
• open 普通异常即使没有返回 Session，也保留 uncertain 占用。只有 open 阶段抛出的 ProviderUnavailable 明确保证未留下进程／未提交时，才释放该预留；send 后的同名异常仍是提交不确定。
• 迁移 1–3 未改写。v3 旧数据中有 CLI ID 或管理器记录的对话保守建立 uncertain 占用，因为旧版没有可信退出证明；不会假装旧进程都已停。合成升级验证保留全部原对话及迁移历史。当前无人工核对／强制解锁接口，这些遗留会话需后续核对；从未启动的新对话不受影响。
• 所有等待 CLI 打开、发送、关闭的步骤均在写事务之外，不以长时间持有数据库写锁伪造外部原子性；最后一次提交前检查与外部 send 仍不是跨系统原子事务，不宣称外部 exactly-once。

▶ 本轮修改文件
• 新增 `backend/src/omniflow/cli_process_schema.py`：仅追加迁移 4 定义与旧版不确定占用初始化。
• 修改 `backend/src/omniflow/db.py`：注册迁移 4。
• 修改 `backend/src/omniflow/run_manager.py`：发送前暂停／原轮重领、持久占位、领取互斥、退出确认及失败保留。
• 修改 `backend/src/omniflow/conversations.py`：取消暂停轮次时同步空助手消息状态与事件。
• 修改 `backend/tests/cli_double.py`：假 CLI wait 确认退出并关闭句柄后明确返回 True；没有放宽原有测试预期。
• 新增 `backend/tests/test_run_manager_safety.py`：11 项故障／交错／迁移回归。
• 修改 `backend/README.md`：退出确认契约、阻塞行为、迁移 4 与阶段 05 接续要求。
• 修改本文件 `docs/progress/02-conversations.md`：追加证据，保留此前记录。
• 未安装新依赖、改锁定文件、重新构建包或修改原站、部署配置、实施计划、控制器与控制状态；没有 commit、push、reset、clean、merge、嵌套 agent 或后台循环。

▶ 先失败、再修复的证据
```bash
.venv/bin/python -m pytest backend/tests/test_run_manager_safety.py -q
```
• 修改产品实现之前，首批 6 项得到 `5 failed, 1 passed in 5.19s`。真实管理员 API 在 open 返回前关闭开关，假 CLI 仍记录一轮；真实 3 秒租约到期后另一管理器仍执行；close 的异常／False／None 三种无确认结果也均允许重复恢复。已受理工作继续收尾的对照项通过。
• 修复后首批 `6 passed in 5.24s`，第一次全量 `262 passed in 41.81s`。随后补齐取消暂停空助手、独立 Python 竞争者、打开丢失句柄、退出确认阻塞交错、复用会话发送前重查、旧版升级隔离等覆盖，最终 267 项。没有删测试、降低安全条件或改设计契约。
• 新增文件首次 lint 出现 import 顺序问题，实际调整并格式化后通过，没有屏蔽规则。

▶ 最终实际测试命令与结果
以下从当前 worktree 根目录执行，全部使用临时合成数据／假 CLI。
```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_health.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• Ruff：`All checks passed!`，`38 files already formatted`。
• 后端固定门槛：`267 passed in 44.07s`，退出码 0；基础、账号、既有对话／事件／隔离测试继续通过。
• 契约：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称完整官方结构校验。
• 对话冒烟：127.0.0.1 HTTP／SSE、信号 control 关闭、重启快照、独立管理器消费与默认拒绝真实提供方通过；自管进程已退出。
• 健康冒烟：显式迁移、只读检查、127.0.0.1 HTTP、重启恢复与日志脱敏通过；自管进程已退出。没有降低 Secure Cookie，仍不是浏览器 HTTPS 联调。
• 空白与列明原站／部署文件差异检查退出码 0。
• 另以本地 Python `subprocess.run(..., check=True, timeout=60)` 有界前台重复以下专项 3 次，每轮均 `11 passed`，耗时 7.05／6.86／6.97 秒：
```bash
.venv/bin/python -m pytest backend/tests/test_run_manager_safety.py -q
```
• 专项实际使用仍存活的独立假 CLI PID／目录／ID、合法 3 秒租约加 3.2 秒真实等待、另一个独立 Python 管理器及 Event 安排退出交错；不仅比较内存布尔值。测试 finally 回收自身子进程，没有终止其他进程或 tmux 会话。

▶ 未实现／未验证与后续接续
• 本阶段离线闭环可交复审；生产发布、生产迁移、真实 agy／Agnes、免费超额权益、Google 授权、真实宿主隔离均「未完成」。产品型号仍固定 `gemini-3.8-flash-low`，没有调用真实供应商或开放公网。
• 本轮不能证明真实 CLI 的 close／子进程组退出和权益隔离；阶段 05 必须按新的 Session 契约提供有界退出确认。不得把 os 进程锁释放、管理器死亡或租约超时当作孤儿 CLI 已退出。
• 未提供人工核对／强制解锁或 CLI 孤儿自动终止能力；管理器崩溃或旧版遗留占用可能持续阻塞该对话，必须有退出证明及明确核对流程后再设计恢复，不能直接删除占用或把未知轮次改回 queued。普通确认关闭后的重启恢复已通过测试。
• 迁移版本现在为 4，下一阶段从版本 5 追加，不改 1–4。阶段 03／04 的素材、任务、快照必要版本、任务关联与占用删除校验仍按下面历史报告接续，没有在本轮提前冒实现。
• 前端、浏览器 E2E、真实 HTTPS／代理长连接、多平台及长时常驻验证仍未完成；npm 测试／构建与 E2E 命令仍按阶段 06／07 提供。

【尝试 1 编码记录（保留历史证据）】

▶ 本轮结论与边界
• 本轮为阶段 02 coder，完成对话／消息／文字 run／幂等／串行调度／一致快照／SSE 恢复的离线闭环，可交独立审查；不是所有创作阶段完成。
• 最终固定命令：`256 passed in 37.30s`，包含进入本轮时实际复测通过的 178 项既有测试及本轮新增 78 项。没有删除测试、降低安全预期或改写设计契约来通过检查。
• 已完整阅读实施计划、Spec v1.0、FastAPI 接口文档、已有三份阶段／巡检记录；实际核对账号、安全边界、迁移与配置代码，以及本阶段路由、请求／响应、事件与引用 schema。未把旧成功声明直接当作证据。
• 仅修改当前 worktree 的 `backend/` 及本报告，保留原有未提交文档、原型和代码。没有修改原站／部署文件、实施计划、控制器或控制状态；没有 commit、push、reset、clean、merge、嵌套 agent 或后台循环。
• 生产发布、生产数据迁移、真实 agy／Agnes、免费权益、真实宿主隔离与真实供应商验收均「未完成」。未读取真实凭据、未调用真实提供方、未改 Google 授权、未开放公网。产品模型仍为 `gemini-3.8-flash-low`。

▶ 实际实现
• 追加迁移版本 3：conversations、messages、runs、events、idempotency_records；既有迁移版本 1／2 保持不变。新增显式只读事务 `Database.snapshot()`。实测 v2→v3 保留账号字段与旧迁移记录，外键核对通过。
• 12 个实际 HTTP 操作：创建／列出／读取／改名／删除对话，创建／分页消息，分页／读取／取消 run，一致快照与 SSE。所有私有接口校验当前网站登录及逐资源归属，修改接口保留精确 Origin／CSRF；管理员也不能读取别人的私密聊天。
• 对话创建只保存新应用上下文，不启动 CLI。用户消息与 queued run 在同一事务保存；附件和图片确认尚未由阶段 03 创建，当前引用一律 404，不假装授权成功或悄悄丢弃。
• 幂等范围为当前 owner＋POST＋规范化路径＋键；同语义重放原受理响应及原状态码，附 `Idempotency-Replayed: true`。`client_message_id` 唯一，即使换 HTTP 键也只产生一轮；相同标识不同内容 409。身份／归属在重放之前复核；事务失败不消耗成功幂等键。
• 用户／助手预留相邻消息序号，助手只在实际领取执行后建立；后排用户消息不会排到上一轮助手之前。构建上下文限定本轮用户消息及更早历史，不能提前包含后排消息。
• 会话与 run 按创建时间／ID 稳定倒序分页，消息返回最近一页且页内 seq 升序，游标绑定用户、资源、对话和排序。严格校验游标时间、UUID、数值范围及异常 Unicode，不返回原始输入。
• 对话删除要求没有 queued／running／stopping／needs_reconciliation run。删除清除在线正文、输入 JSON、幂等响应正文与事件，保留归属、状态和关系墓碑；旧键不能复活，返回 410。重复删除仍 204，其他用户统一 404。没有删除独立作品，不承诺 SQLite 释放页面或供应商副本被安全擦除。
• `run-manager` 为独立前台命令，可用 `--once` 或 1–8 个工作线程；不随 HTTP／SSE 启动，不使用 FastAPI BackgroundTasks。各执行者以 SQLite 租约和活动 run 唯一索引互斥，跨管理器不能重复领取，同对话必须收到明确 result 才能开始下一轮。
• 管理器在执行时续租并复核 run／用户授权代数；停用、重置后旧排队授权不复活。暂停开关在入队与领取均检查，低磁盘暂停新增而不删旧内容。队列默认 1000 个非终态 run，属于并发保护而非每日额度。
• CLI 映射、进程和目录不进入公开响应。注入提供方必须在新对话收到空恢复 ID，在旧对话收到已绑定的明确 ID；映射唯一约束拒绝跨对话复用 ID。相邻同对话轮次复用常驻进程；空闲可回收，线程切换其他对话前先回收旧空闲进程，避免闲置进程与过期租约并存。
• 管理器只接收类型化文字增量与明确 result。未知日志／工具输出拒绝进入正文或事件；每条 delta 有递增 `chunk_index`，完整 `message.updated` 与 run 终态事务提交。提供方原始异常不写入公开资源。
• queued 取消直接 canceled；running 先 stopping，管理器只停止自己的会话，保存部分正文。已确认停止可 canceled／interrupted；无法确认则 needs_reconciliation。`require_tool_run` 提供内部同事务权限门槛：拒绝停止后新增动作、过期执行者、越权范围及 discuss_only 媒体动作。测试中的已受理合成动作不被取消抹掉，真正媒体任务仍属阶段 04。
• 租约过期、CLI 意外断线、未知输出或提交之后发生的不确定错误，保存已有内容并标为 needs_reconciliation，阻止该对话后续轮次，不自动重放。旧执行者的晚到增量／result 不能越过租约屏障。默认提供方完全拒绝调用，实际领取后如实 failed／PROVIDER_UNAVAILABLE，不返回假 Gemini 回复。
• 快照在同一读事务读取最近 50 条消息、较早消息游标、非终态 run 与事件水位；本阶段没有媒体任务或作品版本，相关数组真实为空。并发写入快照期间的事件可从返回水位继续重放，不漏读。
• SSE 只读持久事件。header 优先于 query；不带游标只看之后变化；高位／坏游标 400，过期游标 410。逐条交付和空闲心跳复核当前身份与归属；注销／停用／重置／到期、删除与服务停止均用无持久 ID 的 control 关闭。
• `serve` 的 Uvicorn 信号入口在等待现有连接结束之前通知 SSE，解决仅在 lifespan 收尾置位会等不到无限流结束的问题。默认只绑定 127.0.0.1，访问日志和代理头信任仍禁用。真实回环测试已收到 SERVICE_RESTARTING，且自管服务器及时退出。
• 事件保留默认最近 10000 条，轮次收尾及内部显式 `prune_events` 裁剪派生日志，不删除消息。单轮输出默认上限 128000 字符、最长 600 秒，检查间隔默认 0.2 秒、租约 15 秒，均是可配置单次安全／排队参数。

▶ 修改文件
新增：
• `backend/src/omniflow/conversation_schema.py`
• `backend/src/omniflow/conversation_models.py`
• `backend/src/omniflow/conversations.py`
• `backend/src/omniflow/run_manager.py`
• `backend/src/omniflow/http_server.py`
• `backend/src/omniflow/api/conversations.py`
• `backend/tests/test_conversations.py`
• `backend/tests/test_conversation_events.py`
• `backend/tests/test_conversation_invariants.py`
• `backend/tests/cli_double.py`
• `backend/tests/fake_cli.py`
• `backend/tools/smoke_conversations.py`
• `docs/progress/02-conversations.md`

更新：
• `backend/src/omniflow/app.py`、`cli.py`、`config.py`、`db.py`、`problems.py`
• `backend/tests/test_cli.py`
• `backend/README.md`

本轮没有安装新依赖、修改锁定文件或重新构建包；不把前次构建证据当作本轮实测。测试临时库、子进程合成目录和冒烟日志不属于源码交付。

▶ 实际测试命令与结果
进入本轮的基线复测：
```bash
.venv/bin/python -m pytest backend/tests -q
```
• `178 passed in 14.17s`。

本轮最终全量及代码检查：
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
```
• `256 passed in 37.30s`；`All checks passed!`；`36 files already formatted`。
• 覆盖真实 API 请求／响应 schema、跨用户／管理员越权、CSRF、幂等并发与墓碑、消息唯一性、队列背压、暂停、低磁盘边界、迁移保留、事务故障回滚、串行与跨会话调度、独立假 CLI 进程／目录／ID／历史、明确恢复、部分取消、停止未知、授权撤销、SSE 重放／重连／游标边界、快照并发与真实回环关闭。
• 子进程假 CLI 代码仅在测试目录，使用 `python -I`、合成私有 cwd 和精简环境，无网络请求。三段不同对话实际检查不同 PID／ID／目录和输入历史，不是只问模型是否记得。
• 无限流测试直接驱动完整 ASGI 请求并逐帧消费，避免 TestClient 对无限 SSE 整段缓冲导致假通过；另有真实 TCP 回环冒烟佐证。

以下专项通过本地 `subprocess.run(..., check=True)` 有界串行重复 5 次，不是后台循环：
```bash
.venv/bin/python -m pytest backend/tests/test_conversations.py \
  backend/tests/test_conversation_events.py backend/tests/test_conversation_invariants.py -q \
  -k 'race or multi_manager or two_managers or revocation or one_read_transaction or signal_shutdown or switching_worker'
```
• 五轮均 `11 passed, 67 deselected`，耗时依次 5.65／5.44／5.42／5.42／5.46 秒，全部退出码 0。

独立真实回环冒烟：
```bash
.venv/bin/python backend/tools/smoke_health.py
.venv/bin/python backend/tools/smoke_conversations.py
```
• 两者通过；后者实际验证 HTTP／SSE、信号关闭 control、服务器重启后快照、独立 run-manager 消费以及 PROVIDER_UNAVAILABLE 真实失败状态。只使用临时合成账号／库，绑定 127.0.0.1；结束自己创建的服务器与 CLI 测试子进程。
• 合成 Cookie 手工发送到回环 HTTP，仅验证服务器边界与流式传输；没有降低 Cookie Secure，也不冒充真实浏览器 HTTPS／反向代理验收。

契约和文件边界：
```bash
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 契约：40 路径、51 操作、64 schema、525 引用；22 契约示例、8 文档 JSON、1 SSE 帧及 22 类拒绝用例通过。没有提供官方 OpenAPI 元 schema，不宣称完成官方全量校验。
• 空白与原站／部署文件差异检查通过，没有切换旧站。

▶ 实现过程中发现与修复
• 初次静态检查出现新增代码长行、导入顺序和冗余异常写法，实际格式化／定点修复后通过，未屏蔽规则。
• 阶段 02 原有固定 178 项测试一直保持通过。新增业务／SSE／故障注入测试逐批通过，最终共 256 项；没有出现需要删除测试或改契约的业务失败。
• 代码核查发现仅在 lifespan 末尾关闭流不足以处理真实服务停止，新增 LocalServer 信号入口并实际以自管子进程收到停止信号验证。`test_cli.py` 相应从检查 uvicorn.run 参数改为检查实际 Config／Server，保留精确回环、禁用日志／代理／Server 头的原安全断言，新增提前关闭流断言。
• 代码核查补强了提交开始之后的 ProviderUnavailable 不可误判为安全失败、忙于另一会话前回收旧空闲进程、多调度线程失败时及时通知其余线程、删除清除在线正文，以及未知轮次不能盲目重放等边界，并增加相应回归。

▶ 未实现／未验证与下一阶段接续
• 阶段 03：接入真实版本归属、图片确认、显式引用范围与快照必要版本；当前引用拒绝不是已实现上传。扩展快照中的严格类型与幂等记录以覆盖无 conversation_id 的独立上传／作品动作，不能直接把对话作用域重放函数当作全部资源授权。
• 阶段 04：新增持久媒体任务、任务事件、run.task_ids、助手 artifact_version_ids 关联及任务结束后的 message.updated；在同一删除事务增加非终态媒体任务占用检查。现在没有真实媒体任务，不能把合成动作表测试算作媒体取消／恢复完成。
• 阶段 05：将真实官方 agy 常驻协议、受控工具动作、费用／权限闸门接到 Provider／Session 接口。适配器必须提供有界 open／send／receive／stop／close，并实测正确的进程范围与终止确认；本轮的进程独立和停止证明仅针对假 CLI，不是宿主沙箱或真实提供方保证。
• 内部工具在保存动作的同一写事务调用 `require_tool_run`，再叠加具体用户意图、引用、任务种类开关与提供方费用校验；模型不能自行选择 owner、conversation、租约令牌或 confirmation。
• needs_reconciliation 当前可查询但没有自动重试／人工核对恢复接口；后续明确核对以前不可改回 queued 或盲目发送整轮。服务正常重启可以恢复已保存事实，不等于备份或供应商 exactly-once。
• 当前只验证本阶段实际操作／安全／请求与成功响应引用子集及事件实例；跨阶段快照类型、全部 OpenAPI 字段和错误声明的严格差异仍留到阶段 07。当前请求运行时拒绝可选引用显式 null，完整 schema 对齐也需在全量比较中核对。
• 前端、浏览器 E2E、HTTPS 转发与代理缓冲、Vercel／隧道长连接、Mac／iOS、长时间常驻压测均未完成；npm 测试／构建与 E2E 命令分别从阶段 06／07 提供。
• 真实供应商、免费权益、宿主隔离、生产发布和生产迁移均未完成。本轮可继续纯离线阶段，不因真实供应商闸门而伪造验证或越权发布。

---
In brief
• What's happening：对话现在能保存、排队、停止并重新接上，256 项检查通过。
• Reason：每段聊天分开处理，重复发送不会重复执行，没弄清结果时不会擅自再做一次。
• Impact：旧网站未变；真实生成和正式上线还没完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
