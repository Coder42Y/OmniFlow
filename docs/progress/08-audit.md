【08-audit：最终代码审查与交接】

▶ 本轮结论（2026-09-09，尝试 1）
• 当前阶段编码、代码侧自查及固定回归通过，可交外层独立 reviewer；编码者自查不冒称独立复审已经通过。本会话未启动嵌套 agent，也未越阶段实现生产装配。
• 最终后端 809 passed，前端 82 passed，真实新 FastAPI＋浏览器＋假提供方 E2E 5 passed，Vite 生产构建通过。先前测试全部保留；新增后端 12 项、前端 1 项。
• 修复两项具体缺陷：普通请求在 JSON 解析前缺少总容量限制；横线定位到末轮时按钮引发高度变化、活动标记回退。没有扩展新功能、改配色或降低测试预期。
• 真实 agy／Agnes 核验、生产运行装配及正式切换「未完成」。用户已批准发布并选择「补齐真实接入后发布完整新版」；第09／10阶段继续隔离实现，不能重复询问整体发布授权，也不能把假生成或 AI 禁用基础版当作完整新版发布。

【一、依据与审查范围】

• 完整阅读 `docs/plans/自动开发实施计划-v1.md`、`docs/specs/创作工作台重构-Spec-v1.0.md`、`docs/api/FastAPI接口文档-v1.md` 及追加的 `docs/plans/完整新版发布实施计划-v1.md`。
• 优先阅读 `docs/progress/07-e2e.md` 全部两轮记录和 `06-ui-代码收尾.md`，核对实际实现；旧 user_stop 是已被后续阶段取代的暂停信息，未据此重新设计或回滚界面。
• 直接检查 app／Settings／ASGI 安全边界、账号依赖与事务、SQLite 迁移、对话和 SSE 路由、素材上传与受保护交付、持久文字和媒体执行器、受控工具、agy 协议／权益闸门及安全 HTTP；前端检查 API／SSE、App 生命周期、认证、Transcript／WaveRail、原生弹窗、响应式 CSS 和测试生命周期。
• 按需完整核对设计中的 Problem、TextArtifactCreate、TextVersionCreate schema，及实际请求模型；运行全量契约回归，而非只相信阶段报告。既有 51 操作／64 schema 比较器未删改或放宽。
• 用户已取消视觉模型／审美验收；未调用视觉模型、未读取 PNG 或做像素对比。保留既有组件、tokens、批准原型及测试自动诊断截图。

【二、实质修复及证据】

▶ AUDIT-01：普通请求缺少解析前容量上限（安全防护）
• 原字段 max_length 仅在 JSON 已经被完整读取和解析后生效。超过 1MiB 的空白＋短合法 JSON 可被正常处理；无效超长正文则在大量读取后才返回字段／语法错误。限流与字段长度不能替代单请求容量保护。
• `backend/src/omniflow/middleware.py` 增加 1MiB 总字节防护：已知超长 Content-Length 在读取前拒绝；缺失或虚报时逐块计数；不因更换 Content-Type 绕过。保留 Host／Origin 检查优先，错误为统一 413／UPLOAD_TOO_LARGE、request_id、no-store，不回显原输入。
• `/api/v1/uploads` 继续使用原先「身份／CSRF 先行、图片独立总量／文件量／解码限制」的路径，不受普通 JSON 上限误伤。实际上传并下载约 1.5MiB 的合成无压缩 PNG，字节完全一致。
• 上限大于契约最大 50000 个非 BMP 字符全部 JSON 转义后的约 600KB；新增用例验证最大文案及恰好 1MiB 的请求仍接受。未增加日用量／存储业务配额。
• `api/auth.py` 的共用错误声明补充实际 413，契约成功响应与请求 schema 未改；全量契约差异检查通过。
• 新增测试覆盖有／无／虚报长度、单块／分块、不同内容类型、输入脱敏、拒绝后不额外消费、精确容量边界、最大文案及 Origin 优先。新增用例初跑 9 failed／2 passed，明确暴露原问题；修复后与 app／契约／上传专项合计 211 passed，随后补入大图不受误限回归，全量 809 passed。
• 审查中核对了现有 Starlette 解析器源码：它已在 BaseException 下关闭上传暂存文件，因此没有把初步怀疑的上传清理问题当作真实缺陷，也没有重复改写该模块。
• 此防护不等于抗住所有并发、慢连接或前置服务器缓冲攻击；生产代理／连接限制仍需第10阶段与现场验证。

▶ AUDIT-02：End 定位到末轮后标记回退（键盘／布局功能）
• 本轮首次前端全量 80 passed／1 failed：按 End 后 aria-valuenow 读到 17，原预期为 18。没有接受 17 或删除断言。
• 初步加入明确值等待和跨两帧复核后，再次全量仍 80 passed／1 failed：短暂出现 18 后，活动标记回到第17轮。证实不只是即时断言抢跑；诊断专项偶然通过不能抵消这个失败。
• 实际原因：`TranscriptView.jump()` 无条件将 atBottom 设为 false，即使浏览器已将滚动夹到最底部；「回到最新」按钮随之插入并占据高度，迟到的 scroll／ResizeObserver 重新计算时把末轮标记退回上一轮。
• 修复仅将 jump 的底部判断改为与普通滚动相同的实际剩余距离判定；旧消息阅读锚点、显式回到最新和 WaveRail 已批准的横线几何／弹簧算法保留。未改 CSS 或审美风格。
• 原导航用例保留 Home=1、PageDown=4、End=18、波峰／预览／拖动和减少动画断言，额外跨帧核对第18轮活动标记及真实目标可见。
• 新增确定场景：「已经在底部按 End」，用 MutationObserver 计数按钮插入，跨四个真实布局帧断言插入次数为 0、高度不变、标记仍为 18、剩余距离为 0。观察器与浏览器上下文在 finally 清理。
• 修复后的导航与旧消息阅读专项 2 passed／26 skipped（仅名称筛选）；最终前端全量 82 passed，无跳过。产品组件变化后重新跑 E2E 及其前置构建，通过。

【三、全量回归与审查结果】

▶ 实际最终命令
```bash
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run test -- --run
npm --prefix frontend run test:e2e
# test:e2e 的 pretest:e2e 实际执行 npm run build，
# 对外同样保留 npm --prefix frontend run build。
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
npm --prefix frontend run format:check
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
sha256sum docs/specs/assets/codex-wave-rail.approved.html backend/uv.lock frontend/package-lock.json
```
• 后端：809 passed in 183.41s；包括原有真实回环健康／素材冒烟、账号／一次性链接竞态、跨账号、HEAD／Range／grant 撤销、SSE 恢复、串行上下文、工具隔离、进程停止确认、任务故障恢复及全部契约比较。后端代码此后未改，不为凑证据反复重跑。
• 前端最终：5 文件、82 passed，18.14s；包含 320／390px 与桌面、无整页溢出、控件可见、独立抽屉、弹窗／键盘／减少动画、媒体读取恢复、未确认动作保留原编号与退出清理。前两次真实失败及诊断过程如上保留。
• E2E 最终：5 passed，0 failed／skipped，11.38s；Vite 24 modules，763ms；最终 JS 为 `index-Bp2rnhwE.js`（139.80kB），CSS 为 `index-DvZ_Iile.css`（16.17kB）。构建确实包含本轮 Transcript 修复。
• E2E 业务闭环：邀请注册、Secure／HttpOnly Cookie、CSRF、上传→自然语言工具生图→固定版本确认→视频、关页面／重启 worker 后下载、A/B/A 上下文隔离、跨账号拒绝 GET／HEAD／Range、幂等和精修版本、下载失败只恢复原结果、提交未知不再生成、注销撤销旧流。
• Ruff、86 个 Python 文件格式、前端 Prettier、空白和列明旧站／部署文件差异检查全部通过。文档契约：40 路径／51 操作／64 schema／525 引用、22 契约示例／8 文档 JSON／1 SSE／22 类拒绝检查通过；未提供官方 OpenAPI 元 schema，不冒称官方完整结构验证。
• 有一次 `npm --prefix frontend exec -- prettier --write tests/layout.test.js` 因路径从 worktree 根解析而报找不到文件；改用已安装的 `frontend/node_modules/.bin/prettier --write frontend/tests/layout.test.js`，格式检查通过。未安装新依赖或改全局运行时。

▶ 当前代码侧安全结论
• AC-01／08／11：身份与管理员权限分离；修改事务重新校验授权；停用／重置／注销撤销旧登录，SSE 和文件分块读取复核身份，签名取图不向普通响应泄露。测试成功不宣称服务器所有者无法访问磁盘。
• AC-02／04／06：应用会话和 CLI 映射分离，单会话串行、排队后文不提前可见；默认型号仍是 `gemini-3.8-flash-low`，工具范围受可信 run 和显式版本引用约束，确认不是模型自报。结构化正文与任务事实分离。
• AC-03／05／09：已受理任务独立持久处理，关闭浏览器不取消；提交未知不盲目 POST，下载重试接回同一结果，版本和任务关联唯一。离线闭环通过不等于真实媒体质量／权益已验收，也不宣称跨供应商严格 exactly-once。
• AC-07：默认拒绝未知免费／超额／隔离证据；本地暂停不能清除供应商持久限流，低磁盘不删旧作品。真实可信证据采集器和生产入口仍待第09阶段。
• AC-10／12：响应式、键盘和实际 API／契约回归通过；Mac／iOS 真机、真实代理链仍未验证。未把测试名中的 UI 或截图产物当作视觉复审门槛。

▶ 日志与完整性
• `backend/var/08-bounds-before.log`：请求容量新增测试修复前失败。
• `backend/var/08-targeted.log`：后端专项 211 passed。
• `backend/var/08-backend.log`：后端最终全量 809 passed。
• `backend/var/08-frontend.log`、`08-frontend-final.log`：两次前端真实失败。
• `backend/var/08-navigation-diagnosis.log`、`08-navigation-fixed.log`：诊断和修复专项。
• `backend/var/08-frontend-delivery.log`、`08-e2e-delivery.log`：最终前端与构建／E2E 成功。日志目录本地忽略，不含真实账号或供应商凭据。
• 批准原型 SHA256：`be8f15d9510bfb6a2bd937409c142fa7473876c17f901a7e495228ece1189448`，未改动。
• 本轮没有重新安装依赖或修改锁文件；当前摘要：backend/uv.lock 为 `9ae464f9d5027c4331dd305c9a41295916cfb695c817b8d350bdac9c10778396`，frontend/package-lock.json 为 `056032b9f039423e22a355da2acfc19e9b142869bec7ba8f454361b9d796d80f`。
• 13:55 检查 e2e_server／fake_agy／Vitest 等测试进程，仅匹配查询命令自身，没有本轮残留服务；backend/var 无 e2e-* 临时目录。正常测试自行关闭浏览器、API 及 worker。未终止其他会话或进程。

【四、启动与停止交接】

▶ 无真实调用的完整可运行验证
```bash
npm --prefix frontend run test:e2e
```
• 从当前 worktree 根运行，使用现有 `.venv`、Node、Chrome、openssl、FFmpeg。自动构建 frontend/dist，在 backend/var/e2e-* 创建合成库／媒体／短期测试证书，实际绑定 127.0.0.1 随机端口；退出自动关闭自己的进程并清理临时数据。
• 此入口位于 tests，仅供离线验收，禁止作为生产入口或真实供应商成功证据。

▶ 独立本地健康／管理入口
```bash
export OMNIFLOW_DATA_DIR="$PWD/backend/var/local-audit-data"
.venv/bin/python -m omniflow.cli migrate
.venv/bin/python -m omniflow.cli check
.venv/bin/python -m omniflow.cli serve --port 8765
# 在同一独立数据配置下，可在另一个本地终端运行：
.venv/bin/python -m omniflow.cli run-manager --workers 4
.venv/bin/python -m omniflow.cli media-worker
```
• 以上为交接启动方法，本轮实际库／进程验证由测试管理，没有将此示例目录初始化为生产库。仅绑定 127.0.0.1；HTTP 入口用于健康检查，不降低 Secure Cookie 演示登录。Ctrl+C 仅结束自己的前台进程。
• 独立前端预览：`npm --prefix frontend run dev -- --port 5179` 或构建后 `npm --prefix frontend run preview -- --port 5180`。默认没有业务 API 代理；未装配同源 HTTPS API 时接口不可用是正常事实，不应伪造回复。
• 管理员仅本地交互终端使用 `create-admin --username local_owner`，密码隐藏输入两次，不进入命令参数／日志；正式账号初始化由后续现场流程执行。本轮只用合成账号。
• 迁移保持版本 8，未改已有迁移；删除素材仍需显式 `purge-artifacts`，当前没有安装生产定时服务。无备份／灾损恢复承诺；不把任务续查当作备份。

【五、修改文件与未完成闸门】

▶ 本轮修改／新增
• `backend/src/omniflow/middleware.py`：解析前请求容量保护。
• `backend/src/omniflow/api/auth.py`：共用 413 错误声明。
• `backend/tests/test_request_bounds.py`：新增 11 项容量／安全边界回归。
• `backend/tests/test_artifacts.py`：新增大图上传不受 JSON 上限误伤回归。
• `frontend/src/components/TranscriptView.vue`：末轮导航底部状态修复。
• `frontend/tests/layout.test.js`：增强原键盘定位断言，新增多帧末轮稳定性检查。
• `backend/README.md`、`frontend/README.md`：更新已完成阶段、启动与发布边界，纠正过期的「前端／全契约未实现」说明。
• `docs/progress/08-audit.md`：本报告。此前未提交代码／文档和原型均保留；未修改根 server.py／index.html、部署配置、实施计划、控制器或控制状态。无 commit／push／reset／clean／merge。

▶ 具体后续接续（不阻塞本轮纯离线验收）
1. 外层独立 reviewer 重新审查本轮容量中间件、上传豁免、契约错误声明及 Transcript 底部判断／跨帧测试，并执行固定回归。不能只引用本报告作为独立验收。
2. 第09阶段按已批准追加计划装配真正的 API／文字管理器／媒体 worker、官方可信启动器、最小授权与隔离边界、共享就绪状态和可信证据核验；当前 Settings 仍只有 disabled/mock，AgyProvider 默认 launcher=None。不得通过随手勾选布尔值或导入测试提供方上线。
3. 第10阶段准备同源 dist 安全交付、用户 systemd 文本和发布／回退工具、可信代理边界与最小真实验收工具；本轮未创建／安装生产服务或切换域名、隧道、Vercel。
4. 真实 agy／Agnes 协议和授权、免费权益及超额关闭、真实宿主隔离／退出、参考图编辑保真、真实媒体品质、长时常驻均未现场核验。真实媒体验收须保留 requested／actual、任务 ID、模型来源和提交次数；供应商限制即停、不自动收费回退。
5. 正式 HTTPS／Cookie／CSRF／代理 SSE／HEAD／Range、Mac／iOS 真机播放、正式管理员隐藏初始化、旧功能入口／旧匿名历史处理及生产前向迁移和保留数据的回退仍待发布流程。已获整体发布授权，不再询问同一 A/B；具体账号持有者输入仅在现场确有缺项时提出。

---
▶ In brief
• What's happening：最终检查发现的两处问题已修好，整套离线检查通过，交独立复核。
• Reason：挡住过大的请求，并避免跳到最后一段时页面把位置推回去。
• Impact：旧网站不变；真实生成和正式上线仍未完成，按已批准后续步骤推进。
• Require Input：不需要 input，继续按已批准流程交接。
