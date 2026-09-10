【OmniFlow Vue3 创作工作台】

▶ 运行与测试
在当前隔离 worktree 根目录执行：
```bash
npm --prefix frontend ci --ignore-scripts --cache "$PWD/frontend/.npm-cache"
npm --prefix frontend run dev -- --port 5179
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run test:e2e
npm --prefix frontend run preview -- --port 5180
```
• 使用现有 Node 24；依赖精确锁定在 package-lock.json。没有修改全局 Node／Python。
• dev／preview 只绑定 127.0.0.1。Vite 禁止加载 .env，文件可见范围限定 frontend/，不读取旧站凭据、不自动代理旧接口。
• 所有运行时请求使用同源 `/api/v1/`，不会自动连接生产网站，也没有内置示例账号或 mock 回退。独立 Vite 服务没有后端时会如实显示接口不可用，不返回假模型消息。
• 实际账号使用需要同源的新 FastAPI 服务与符合 Secure Cookie 要求的浏览器环境；不允许去掉 Secure／CSRF 来演示。阶段 07 已有真实新 API＋浏览器＋假提供方自管 HTTPS 联调，仍不修改旧站或部署配置。
• 固定 test 命令包括单元／组件测试和「真实 Chromium＋拦截合成 API」的界面检查。使用宿主已有 `/usr/bin/google-chrome`，测试自动创建回环 Vite 服务、临时浏览器环境并在结束时关闭。没有下载浏览器、访问真实提供方或使用真实账号。
• `test:e2e` 先 build，再使用 node:test＋既有 Playwright 启动 `backend/tests/e2e_server.py`：真实 FastAPI 服务本轮 dist、独立文字／媒体 worker、官方协议假 CLI 和持久假媒体账本。没有 route.fulfill 或假 API。需要本地 .venv、宿主 openssl 与 Chrome；不新增依赖或下载浏览器。
• E2E 自动在 backend/var/e2e-* 创建临时库、素材及一天有效的自签证书，端口绑定 127.0.0.1:0；浏览器仅本轮 context 忽略证书链，不改全局信任、不去掉 Secure Cookie／CSRF。测试结束关闭页面、浏览器、API 和自建 worker 后删除临时数据。不要将此测试入口用于生产。

▶ 界面与职责
• App.vue：登录生命周期、独立导航／历史、当前对话和内存草稿、上传／确认／消息、作品与任务入口。
• api.js：Cookie／CSRF 引导、轮换、统一 Problem、身份撤销取消在途请求、Retry-After 暂缓新增、精确受保护下载路径、启动同步清除 fragment。
• events.js：一致快照先行、fetch SSE、十进制大序号去重、Unicode 增量、chunk 断档恢复、410 重取快照、控制帧关闭、有限退避与手动重连。订阅仅 GET，不触发模型调用。
• actions.js：每个明确动作持有稳定闭包和幂等键，防双击；网络不确定只手动重试原动作，等待提示生效。尚有未确认请求时，其他共用写入不能清除原重试；App 的任务／轮次／历史／能力查询与注销使用各自独立状态，不覆盖或阻塞原动作。不为 VERSION_CONFLICT／待核对状态自动换键生成；邀请／重置签发不提供重试。
• TranscriptView.vue／WaveRail.vue：消息和实际媒体状态、旧消息分页、阅读锚点、显式回到最新；横线波峰计算、弹簧参数和交互移植自批准原型，原文件不变。
• MediaCard.vue／TaskCard.vue：保护下载、按实际宽高展示、无自动播放的视频、本地运镜来源、requested 与 actual 分开、取消排队／续查／下载恢复、缺失版本降级。
• LibraryPanel.vue／RefinePanel.vue：个人库和版本分页、明确引用、删除回执、文案保存／追加、图片编辑、确认后首帧视频、明确文生视频和本地运镜；同一任务 API，不另设生成通道。
• AuthView.vue／AdminPanel.vue：邀请注册／登录／重置、动态策略、人工核验与一次性链接、账号停启、邀请撤销、无私密内容的故障概况、本地暂停开关。
• ModalShell.vue：native dialog 焦点约束、Escape 关闭与原焦点恢复；精修提交中／响应未确认时保留原窗口和重试编号，只有成功、明确失败或明确放弃后才关闭。手机侧栏另有键盘焦点循环及关闭恢复，桌面双侧栏缩窄时只保留一个抽屉。

▶ 安全与状态说明
• 正文、标题、错误和文案只按文本插值，不使用 v-html；媒体来源按作品／版本 UUID 构造同源 content 路径，不信任任意 content_url。
• 不把 Cookie、CSRF、一次性凭据、消息或作品写入 localStorage／sessionStorage，不加载第三方脚本。邀请／重置链接 fragment 在页面第一次请求前同步清除，仅在当前流程内存持有；密码提交后清空。
• 非秘密的当前 conversation_id 放地址 query，刷新仍恢复该对话并由 API 复核归属。每个对话的未发送草稿和未确认请求的重试编号只在当前页面内存隔离；刷新／退出会丢弃这些内容，应先从真实任务／消息状态核对，不进行旧匿名历史导入。精修响应未确认时，关闭按钮和 Escape 不会无意丢弃原请求；明确放弃重试不发送取消或再次生成请求。
• 未确认写入期间仍可打开并刷新任务、查询文字轮次，查询失败独立提示，随后仍可用原编号重试。账号窗口明确说明退出将丢弃本页草稿／重试信息但不取消已受理工作；注销成功或身份已失效才清理私密界面，注销失败不冒称退出、不覆盖原重试。客户端暂缓新增的等待不拦截精确的注销 POST，Cookie／CSRF 与后端校验不变。
• 明确选择不等于视频确认；确认绑定精确图片版本，切图／删图清除当前确认。没有图时默认首帧路径不可提交，需先做视觉稿或显式选择直接文生视频。
• 202 只显示保存／排队事实，真正文件完成通过事件或刷新恢复；没有虚构百分比。停止 run 不取消已受理媒体，不对未知提交提供「重做」按钮。
• 前端不是授权边界；后端仍须逐资源复核身份、CSRF、归属、幂等、基础版本、确认事实和实际提交条件。
• 手机 320／390px、桌面、键盘／减少动画有自动检查；Mac／iOS 真机播放、代理长连接、长期运行、真实提供方权益与隔离均未验证。

▶ 组件与响应式代码检查（2026-09-09 用户缩减范围后）
• 当前规范为 `docs/design/design.html` 与 `docs/design/tokens.css`；`tools/sync-tokens.js` 只复制这一份公开 token 到 `src/tokens.css`，在 dev／build 前运行，不扩大 Vite 文件可见范围。`tests/design.test.js` 校验逐字节一致与对比度。
• `UiIcon.vue` 提供统一 20px SVG；触摸控件至少 44px、主要表单操作 48px。原批准横线几何／弹簧算法未改，只扩大轨道命中区域和预览字级。
• 精修标题和提交区固定，中间表单独立滚动；列明缺少项。原生 dialog 补首尾 Tab 循环，Escape 返回原入口；从手机抽屉打开的窗口返回可见的侧栏开关。
• 型号、UUID、原始时长与字节进入详情；卡片保留 AI／本地运镜来源和格式化实际规格。HTTP 加载失败只说暂不可查看，可重试同一读取，不假称删除。消息与任务不重复大图，仍保留任务事实。
• 真实 Chromium 测试会生成 `frontend/test-results/ui-v2/*.png` 和 `manifest.json`。后者记录视口、截图时间、控件实测尺寸和当次应用源码 SHA256。包括桌面、320／390 手机、长弹窗、提交中、暂停／恢复、波峰、登录与本地视频。
• `tests/assets/visual-portrait.png`、`visual-landscape.png` 为本轮 Pillow 本地绘制的合成视觉稿；`visual-local.mp4` 为这张图制成的 1.25 秒 FFmpeg 合成测试视频，无真实 AI 请求。画面和 API 标签仅是测试夹具，不把它们装进产品演示或充作供应商效果证据。
• 用户已取消视觉模型复审、PNG 读图和外观审美验收；上述截图仅为自动诊断产物，不要求查看或作为审美门槛。保留既有组件、规范和功能断言，外层 reviewer 负责独立代码／安全审查及实际功能回归，不再逐项打磨配色或图标。

▶ 阶段 07 证据与接续
• 实际命令、结果及独立审查接续见 `docs/progress/07-e2e.md`。固定前端 81 项与后端全量继续保留，不修改既有生产入口。
• 浏览器联调覆盖邀请注册、上传／对话工具生图、确认精确版本后视频、页面关闭与 worker 重启、实际下载／HEAD／Range、A/B/A 上下文、跨账号拒绝、精修新版本、原键重放、下载恢复、提交未知和注销撤销旧流；320／390px 与桌面检查无横向溢出。
• `backend/tests/test_runtime_contract.py` 比对全部 51 操作、64 schema、请求响应及安全声明；仅文档元数据／等价 JSON Schema 表示和已批准固定 low 型号收窄进行显式归一化。默认产品文本型号仍为 gemini-3.8-flash-low。
• 生产发布、真实 agy／Agnes 调用、Google 授权、收费回退及生产数据迁移均未完成且未在本阶段执行。

▶ 阶段 08 最终交接
• 最终回归为前端 82 项、后端 809 项、E2E 5 项及生产构建通过；实际命令、失败过程、修复与剩余闸门见 `docs/progress/08-audit.md`。
• 修复横线定位到末轮时的高度反馈：已经到达底部不再强制插入「回到最新」按钮，避免滚动／尺寸回调把当前标记退回上一轮。新增多帧、按钮插入次数、实际目标可见性检查；批准的波峰算法／配色和旧消息阅读锚点保留。
• 整体发布已获用户批准，且明确要求补齐真实接入后发布完整新版；接续第09生产运行装配和第10发布工具，不能把离线假提供方或缺省禁用状态发布为完整生成服务。本阶段不执行真实调用或生产切换。
