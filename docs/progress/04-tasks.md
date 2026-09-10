【04-tasks：持久任务与独立 worker】

▶ 当前结论
• 本轮 coder 已完成本阶段离线实现，可交独立审查。固定后端命令最终已通过 `444 passed in 103.47s`；进入阶段实测基线为 `367 passed in 74.04s`，保留既有测试，新增 77 项任务、故障和独立进程验证。随后三个回环冒烟、代码与原站边界检查均通过；worker 故障专项另连续三轮通过，不把历史成功声明直接当作证据。
• 已完整阅读指定实施计划、Spec v1.0、FastAPI 接口文档、docs/progress 全部既有记录；逐项读取本阶段全部任务操作、递归引用请求／响应 schema，并核对实际账号、对话、版本、文件／grant、迁移、CLI 和测试代码。没有改设计契约或用旧 Spec 替代新决策。
• 只修改当前 worktree 的 backend 与阶段报告，保留原有未提交文档、原型和代码；未修改控制器、实施计划、控制状态、原站 server.py／index.html 或部署配置。未读取 .env／真实凭据、未启动其他 agent 或后台循环、未操作生产库，未 commit／push／reset／clean／merge。
• 生产发布、生产迁移、真实 agy／Agnes、真实费用权益、Google 授权及宿主隔离实测均「未完成」。产品文本模型仍固定 gemini-3.8-flash-low。此次 pass 只指本阶段离线任务闭环，不等于真实媒体质量或整站验收。

▶ 已实现
• 追加迁移 6：持久 tasks、任务幂等账本、提交意图与不可逆 dispatched_at、提供方 ID／结果线索、每次领取的租约令牌、下一次调度时间及稳定输出作品／版本 ID。迁移 1–5 未改写；v5→v6 合成升级保留账号、旧文件版本和迁移历史，外键检查通过。
• 六个任务 HTTP 操作：创建、个人列表、详情、取消、恢复、管理员脱敏故障列表。三类请求 image／ai_video／local_motion 使用 kind 判别，禁止模型／owner／路径／URL／shell 等额外字段；秒数、画幅、档位、引用唯一性、成对目标和基础版本、明确参考确认、归属均由服务校验。
• HTTP 和可信内部工具使用同一创建事务。持久记录任务、媒体范围／输入与目标占用、幂等关联、run.task_ids 和 task.updated；重放先验证当前身份和归属，同语义返回原 202 响应，不触发执行。已删对话／目标／输出作品保留墓碑，不借旧键复活；明确重新生成必须新动作、新键。
• TaskMediaService 已真实接线：入队 reserve，包括无输入任务；最后提交检查通过才 activate；终态事务 close 并撤销 grant。未知提交和保存失败不提前释放输入。目标基础版本不是当前版本则 VERSION_CONFLICT，已有非终态修改任务则 ARTIFACT_BUSY；任务与作品 source_task_id 唯一关联。
• 独立 `media-worker [--once]` 命令，不由 API／SSE 启动，不使用 FastAPI BackgroundTasks。每次领取只执行一次提交、续查或保存；SQLite 短事务领取与取消原子互斥，网络等待期间独立续租，晚到结果须通过 lease token／时限屏障。多个 worker／进程不能同时提交同一任务。
• 提交意图先落盘，最后事务重新核对本地开关、用户状态／授权代数和磁盘；提供方检查读取本地可信条件快照，禁止在写锁内联网。尚未提交的账号撤销任务取消，暂停任务保留 queued；已受理任务不因本地暂停或停用而停止核对／保存。真实 grant 撤销可能影响供应商取图，不保证停用后生成必成功。
• 一旦 dispatched_at 落盘，不把超时、进程死亡、回复丢失或 ID 提交失败当作安全重试。无提供方 ID 的不确定提交进入 submission_unknown；只有明确未受理异常可 failed。未知且无线索的 recover 返回 RECONCILIATION_REQUIRED，没有重新 POST 的恢复后门，也不声称外部系统与 SQLite 严格 exactly-once。
• 已受理任务重启续查原 ID；提供方完成后进入 saving；下载错误、坏文件、低磁盘保留原结果线索并等待恢复。恢复不新建 task、不重新生成，且不抢走仍活跃的保存者租约。needs_reconciliation 有可信线索可恢复原查询／保存，无线索保持阻塞。
• 输出 ID 在入队时确定，文件发布采用既有私有仓库、不覆盖原子链接。文件已落盘而终态事务未提交时，新 worker 校验并复用稳定文件，补齐唯一版本；不重复下载或追加版本。素材维护保留未结束任务预留的稳定输出文件，不误认作孤立上传清掉。
• completed 仅在文件基本校验与落盘完成，版本、作品 current_version、task 终态、grant 关闭、artifact.ready／task.updated、助手 message.updated 同一事务成功后出现。run 先结束或被取消，已持久任务仍能完成并补齐原助手的 artifact_version_ids。
• 快照返回非终态任务，SSE task.updated 使用严格公开模型。请求参数不被实际结果覆盖：图片实际解码，视频由固定参数的本机 ffprobe 基本校验，实际大小、SHA256、宽高、时长、fps 保存在不可变版本。合成视频请求 4 秒，实际 1.25 秒、32×48、12 fps；测试明确验证差别，不伪造 requested 达标。
• 默认 DisabledMediaProvider 不联网且拒绝新增；可注入假提供方只允许 test 或显式 mock 开发配置，生产拒绝注入。提供方 gate／任务异常均白名单脱敏，管理员仅得到七项 OperationalTask 字段。没有收费回退、日额度扣减或自动清理可用作品。
• ToolAuthority 由可信服务绑定 run／conversation／owner／manager token／稳定 action_id；拒绝 discuss_only、停止后的新增任务、后排消息引用和未选中的旧库。直接文字视频权限默认拒绝，阶段 05 必须依原用户证据授予，不能信模型自报。

▶ 实际测试与结果
所有测试使用合成账号、临时 SQLite／文件和假提供方。普通 API 测试默认禁止网络连接；固定门槛中已有三个回环冒烟仅绑定 127.0.0.1，自行回收创建的服务。没有真实供应商请求。
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
```
• 最终后端 `444 passed in 103.47s`；Ruff `All checks passed!`；格式 `60 files already formatted`。
• 契约：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不宣称完整官方结构校验。任务运行时资源、事件、权限、取消／恢复响应及操作安全子集另以实际 API 测试核对。
• 任务 API 首次专项修复后 `32 passed in 9.08s`。worker 故障专项首轮 `23 passed, 3 failed`，三个失败是新测试误用已有 send 辅助函数，把 Response 当字典；改用原有 accepted 辅助函数并保留全部业务断言，最终全量通过。
• 新增独立 Python 进程入口 media_worker_process.py，逐次进程完成 submit→poll→save；假提供方使用独立 SQLite 账本，每一次 submit 都追加，不靠 mock 内存计数或自动幂等掩盖重复请求。
• 故障注入覆盖：受理后普通超时、SystemExit、os._exit(77)；文件发布后 SystemExit、os._exit(78)；提供方 ID 写入失败；续查／下载失败、坏文件；领取后崩溃、晚到返回、旧保存者失去租约、取消竞争、真实 3 秒租约经过 3.2 秒仍由心跳续持。硬退出进程和其他测试进程均为本轮自行创建，不终止其他服务。
• 未确定提交经三个新的独立进程重启后仍只有一次供应商受理记录；发布后崩溃恢复维持相同 inode、一个 source_task_id 版本；恢复操作没有增加 submit 次数。坏文件和 ffprobe 缺失／超时／无视频流不报 completed。
• 既有版本 4 升级测试原来硬编码当前版本 5、账号 OpenAPI 测试排除 /admin/tasks；新增迁移／操作后首次全量 `2 failed, 423 passed`。将当前迁移断言对齐 MIGRATIONS 最后版本，并将 admin/tasks 纳入原有严格操作／安全／响应核对；没有删除测试、放宽安全断言或改契约。
• 新 API 首次测试发现 FastAPI 响应序列化给 requested_parameters 补出了未提交的 null，违反设计请求 schema。修复 TaskBase 的序列化白名单，既保持 Task 自身必需的 nullable 字段，也不向内嵌请求补 null。孤立代理字符测试改为发送转义 JSON，确保请求真正到达服务；没有改变 422 预期。

▶ 最终专项复测与边界核对
在当前 worktree 前台通过有界 subprocess.run(check=True, timeout=90) 连续三次执行：
```bash
.venv/bin/python -m pytest backend/tests/test_task_worker.py -q
```
• 三轮分别 `26 passed in 16.03s`、`26 passed in 15.38s`、`26 passed in 15.55s`，全为退出码 0。包含独立硬退出、文件事务恢复、领取／取消竞争、真实心跳等待、权限撤销、run／task 关联等，不是只重跑无故障正常路径。
```bash
.venv/bin/python backend/tools/smoke_artifacts.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_health.py
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 三份已有真实回环 HTTP 冒烟全部通过；自管进程均退出。它们验证素材、文字／SSE 与健康的既有闭环，不冒充新增任务的真实网络 E2E；新增任务使用完整 FastAPI TestClient 和独立 worker 进程。
• Ruff 再次 All checks passed，60 files already formatted；空白和列明原站／部署文件差异检查退出码 0。
• 本轮没有安装依赖或重新构建包，不能将前次阶段构建证据当作本轮实测；固定后端命令与本地安装继续可用。

▶ 合成视频夹具
仅为离线解析／下载测试在 backend/tests/fixtures/synthetic.mp4 生成蓝色画面，无网络或真实媒体生成。实际执行：
```bash
ffmpeg -nostdin -v error -f lavfi -i color=c=blue:s=32x48:r=12 \
  -t 1.25 -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  -n backend/tests/fixtures/synthetic.mp4
```
使用宿主已有 ffmpeg／ffprobe，未安装／修改全局运行时。该夹具不是 AI 输出，也不是本地运镜适配器已实现的证据；实际本地运镜执行适配器属于阶段 05。

▶ 本轮修改文件
新增：
• backend/src/omniflow/task_models.py
• backend/src/omniflow/task_schema.py
• backend/src/omniflow/tasks.py
• backend/src/omniflow/task_worker.py
• backend/src/omniflow/media_provider.py
• backend/src/omniflow/media_validation.py
• backend/src/omniflow/api/tasks.py
• backend/tests/test_tasks.py
• backend/tests/test_task_worker.py
• backend/tests/test_task_invariants.py
• backend/tests/media_double.py
• backend/tests/media_worker_process.py
• backend/tests/fixtures/synthetic.mp4
• docs/progress/04-tasks.md
更新：
• backend/src/omniflow/db.py、config.py、app.py、cli.py
• backend/src/omniflow/conversation_models.py、conversations.py
• backend/src/omniflow/artifacts.py、api/artifacts.py
• backend/tests/test_auth.py、test_media_delivery.py
• backend/README.md
本地测试缓存／临时库不是源码交付。没有新增 Python 依赖或改锁定文件。

▶ 未实现／未验证与后续接续
• 下一迁移只能从 7 追加，不改 1–6。启动／独立 worker／配置与测试说明已更新 backend/README.md。
• 阶段 05 接真实官方 agy 常驻协议、受控网关、Agnes 与 FFmpeg 适配器。当前 MediaProvider 是可注入边界而非真实传输；必须实现有界请求、费用／免费证据时效、目标 URL／重定向／容量检查、按 task_id 签发受限参考图 grant。check 只读取本地快照，不能在数据库写锁内拉取真实权益。
• 无线索 submission_unknown 不自动重放，没有人工注入线索／强制解锁接口；需可信核对后再设计管理操作。不能直接把任务改回 queued 演示恢复。正常数据库／文件保留的进程重启已验证，不提供备份或灾损恢复。
• 自然语言授权证据、真实 CLI 工具协议、Google 权限、免费超额开关、真实媒体效果、完整解码与播放、长时负载／磁盘耗尽、宿主沙箱均未验证。ffprobe 固定参数和协议限制不是操作系统沙箱。
• 前端、浏览器 HTTPS／代理／Mac／iOS、浏览器 E2E 尚未完成；npm 测试／构建及 test:e2e 入口分别从阶段 06／07 提供。本阶段 TestClient＋独立 worker 子进程不冒充浏览器 E2E。
• 真实 FastAPI 全量 OpenAPI schema／错误声明严格差异仍留阶段 07，包括 TaskCreate 的内联判别联合表示等；本阶段已验证全部六个任务操作、安全声明、成功响应引用与真实资源 schema，不以文档检查替代运行时测试。
• 生产发布、生产迁移、真实供应商验证均「未完成」。没有配置 systemd／定时清理服务、没有开放公网；已有显式维护命令需后续运行安排与部署审查，24 小时清理是策略目标而非线上保证。

---
In brief
• What's happening：创作工作已能排队、停止未开始的工作，并在重启后继续查结果和保存。
• Reason：系统记住每一步，结果不确定时不会擅自再做一遍。
• Impact：旧网站不变；444 项检查通过，真实生成和正式上线尚未完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
