【Google 持久授权与 CLI 文本接入核对】

▶ 用户目标
希望在网站后端保留 Google 登录／授权状态，优先探索视频生成；如果 CLI 不能利用订阅直接生成视频，则希望作为主力文本模型。
这是新增接入方向，不代表允许付费、导出浏览器 Cookie、向客户端暴露授权凭据，或已经批准向外部内测用户共享个人订阅服务。

▶ 本轮官方资料核对
已读取以下 Google 官方仓库文档，HTTP 200：
• https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx
• https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md
• https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/quota-and-pricing.md
• https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/tos-privacy.md
• https://github.com/google-gemini/gemini-cli/blob/main/README.md
• https://github.com/GoogleCloudPlatform/vertex-ai-creative-studio/tree/main/experiments/mcp-genmedia
资料取自滚动更新的 main 分支，不代表已实测的个人账号行为。最初核对时未安装 CLI；用户随后同意自行完成登录，现已独立安装官方 Gemini CLI 0.58.0 并完成版本／帮助及配置静态检查，尚未登录或提交模型请求。具体进展见 Gemini主控与独立登录验证.md。

▶ 已核实：持久授权和无界面文本调用有官方支持
认证文档明确建议 Google AI Pro / Ultra 用户用订阅对应的 Google 账号选择 Sign in with Google；授权凭据会缓存在本机，后续会话复用。
Headless 模式可使用现有缓存认证；支持通过 -p / --prompt 或非 TTY 调用，并输出 JSON 或 stream-json。也就是说，官方 CLI 的订阅登录与文本自动化是可验证路径，不等于必须购买 Gemini API key。
额度文档当前将 Google AI Pro 列为每用户每日最多 1,500 次模型请求。该数字不是保证账号已识别套餐，也不代表 1,500 次用户对话；单个任务可能包含多次模型请求，实际可用模型、配额、服务负载和限频需登录后核验。
对前文结论的补充：网页会员权益不直接覆盖普通 Gemini API，但官方 Gemini CLI 的 Google 登录是一条不同的、明确支持 Pro 的入口，不能把两者混为一谈。

▶ 已核实：CLI 可以连接视频工具，但不自动继承网页视频权益
Gemini CLI README 提及通过 MCP 工具接入 Imagen、Veo、Lyria 等媒体生成。
所链接 GoogleCloudPlatform 媒体工具样例使用 Google Cloud Genmedia APIs、PROJECT_ID／LOCATION 和 ADC 授权等配置；这不是用 Gemini CLI Pro 文本登录直接消耗 Flow 会员额度。
因此不能说“CLI 绝对不能生成视频”，但也没有找到仅凭缓存 Pro 登录、无需另行云服务资格或费用核验就能自动调用 Flow／Veo 会员视频额度的官方支持路径。当前不通过第三方网页会话转接或内部接口绕过这一边界。

▶ 必须区分的条款边界
Gemini CLI 的 tos-privacy.md 明确写道：使用第三方软件／工具／服务直接访问支撑 Gemini CLI 的服务，例如以 Gemini CLI OAuth 在 OpenClaw 中访问，违反适用条款，可能导致账号暂停或终止。
因此不应把 CLI 缓存的 OAuth token 拿出来，另写一个兼容接口去直接请求 Code Assist 内部服务。
官方文档同时支持运行官方 CLI 的 headless 自动化。上述直接访问限制不应被扩大解释为“一切官方 CLI 脚本调用都违规”。但把单个个人订阅包装成面向外部内测用户的网站文本服务是否允许，本轮仍未取得足够官方依据；不能声称一定允许，也不能仅加一层子进程就认定解决了条款问题。
在多用户用途的授权边界确认之前，仅建议进行项目所有者本人使用的官方 CLI 登录和文本验证，不直接作为外部内测主力上线。

▶ 授权与执行的设计边界
• 使用 Google 官方 OAuth 登录（授权凭据），不保存 Google 密码，不抓取 Mac 浏览器 Cookie。
• 首次登录由用户本人在官方授权页面完成；Mac SSH 场景按 CLI 实际提供的认证流程处理，不把验证码／token 写进对话或日志。
• 凭据放在仓库及网站静态根目录之外，由专用运行身份限制访问；不进入应用用户表，不下发给前端。持久化不等于永不过期或永不需要重新授权。
• 若未来允许面向外部用户使用，仍需每用户独立对话上下文、服务端限流／排队与账号级总额保护，避免将不同用户消息混入一个 CLI 会话。
• CLI 自带文件、shell、搜索、扩展等 Agent 能力；纯文本适配器必须限制工具、扩展和可见目录，并用运行隔离及测试验证。不能把不可信用户提示词直接交给拥有服务器操作权限的 Coding Agent。
• 采用固定可执行文件及参数列表调用，避免 shell 拼接；凭据不混入用户可访问的工具环境。结构化输出需校验，不能把任意模型文本作为已授权动作。
• 配额耗尽或授权失效应提示并停止／按明确批准的备用策略处理，不自动启用按量付费认证。

▶ 下一步验证范围
先做仅供本人使用的最小验证：安装固定版本官方 Gemini CLI → 用户完成 Google 官方登录 → 检查 Pro 识别及可用额度 → 一次无文件／shell 工具的简短文本请求 → 验证退出后重新调用是否可复用授权、能否读取结构化／流式结果。
这只验证登录与文本能力，不验证外部用户转用许可，也不自动接入生产。需要用户完成首次授权；当前没有可用 Google 登录，因此没有账号实测成功结论。
后续用户指定 Gemini 3.8 Flash 为主要文字交互与创作工具调度模型。已在仓库之外准备 /home/kris/.local/share/omniflow-gemini-probe/login.sh；请在本人 SSH 终端执行，授权码只交给官方 CLI。具体模型可用性需认证后根据实际返回元数据核验，不使用 auto／flash 别名或模型自述冒充 3.8 实测。
