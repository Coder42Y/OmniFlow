【agy 官方常驻调用与多人使用条款核对】

▶ 用户后续决定（最新）
用户认为当前仅是调用官方CLI的测试，决定不再把取得官方共享许可答复作为当前受控测试的前置条件。项目据此继续技术验证，不再反复追问或自动联系官方。
以下资料核对保留为事实记录：多人共享许可未独立核验，不写成已获官方确认；转为长期对外、收费或扩大规模时再评估。此决定未授权规避实际用量限制、提取token、自动付费或变更正式部署。

▶ 本轮结论
1. 官方 Antigravity CLI 明确支持程序直接启动、复用已有登录，并通过持续 stdin / stdout 在同一个进程中进行多轮对话。这不是模拟终端输入，也不需要导出 OAuth token 调用内部接口。
2. 常驻技术能力与个人订阅共享给外部网站用户是不同问题。官方附加条款和 FAQ 对第三方软件／服务接入有明确限制；官方 headless 文档同时明确支持脚本和应用程序驱动官方 CLI。不能只引用其中一边，推导出“全部 CLI 自动化被禁”或“套官方 CLI 的多人网站一定获准”。
3. 对“本人服务器运行官方 CLI、使用一个个人 Pro 订阅、为不超过10名外部受邀网站用户处理各自请求”的具体场景，未找到明确许可或豁免。当前结论是：技术路径有官方依据，但共享许可仍未确认且存在实质条款风险；Docker／systemd 不能替代许可确认。
4. 本轮没有部署 Docker、systemd 或常驻进程，没有开展外部共享服务，也没有追加任何模型／图片／视频生成。此前实测已经证明单次真实文本和媒体工具调用；本轮常驻结论来自官方协议文档，未新增长时运行或多用户隔离实测。

▶ 权威来源及读取范围
本轮直接读取 Antigravity 自身官方站点，不再把 Gemini CLI 的另一套文档套用于 agy。
• https://antigravity.google/llms.txt：官方文档索引。
• https://antigravity.google/docs/cli/headless：完整非交互／常驻输入协议。
• https://antigravity.google/docs/cli/settings：设置与 useG1Credits 开关。
• https://antigravity.google/docs/cli/permissions：权限匹配与优先级。
• https://antigravity.google/docs/cli/sandbox：操作系统隔离及其范围。
• https://antigravity.google/terms：完整附加服务条款。
• https://antigravity.google/docs/faq：第三方客户端使用登录的限制说明。
• https://antigravity.google/docs/plans：个人套餐与实际限额／超额用量机制。
• https://antigravity.google/docs/enterprise：企业授权和 Cloud 接入的不同路径。
• https://antigravity.google/docs/sdk/overview：SDK 使用 API key／Cloud 的入口，不是已确认的个人 Pro 共享替代品。
• https://policies.google.com/terms?hl=en-US：已下载，核对账号、组织／商业使用和软件许可相关段落。
• https://policies.google.com/privacy?hl=en-US：已下载，核对数据收集、第三方数据和用途相关段落；未宣称逐条完成隐私法律审查。
页面快照位于 .superpowers/agy-terms-check/。本轮读取的是滚动更新公开文档，不推断它们对应每个历史版本或特定账户的合同；已安装 CLI 报告版本1.1.22。

▶ 官方明确支持的常驻方式
headless 文档原文：
“Use --input-format stream-json to maintain a single, continuous conversation process, feeding it prompts one by one on standard input (stdin). Each prompt executes a full turn and emits its own result event.”
该文档明确给出 Python subprocess.Popen 示例，保持 stdin 打开，收到当前轮 result 后发送下一条；第二轮无需重新启动进程，复用已热身的上下文。

建议调用形态（文档示意，未在本轮执行）：
```bash
/usr/local/bin/agy \
  --model gemini-3.8-flash-low \
  --input-format stream-json \
  --output-format stream-json \
  --sandbox
```
输入协议：
```json
{"event":"user","message":{"content":"本轮用户消息"}}
```
• 不再通过 -p 给常驻模式送消息。
• init 一次，每轮一个 result；通过 step_update 观察文字和工具事件。
• 同一常驻进程使用一个 conversation_id，因此不能把不同网站用户的对话混进同一进程。
• usage、num_turns、duration_seconds 为会话累计数，不能逐轮重复累加。
• 保持 stdin 打开才会继续等待；关闭 stdin 后完成当前轮并退出。
• 不支持 control_request / control_response，不应照搬别家 CLI 的双向审批协议。
• --model 需要启动时固定；流输入不能用 /model 切换型号。
• 程序退出码0不一定代表业务成功，必须检查 result.status 与工具状态。

▶ 推荐运行结构〔建议，未部署〕
首选先使用 systemd 托管一个常驻会话管理服务，由其通过原启动入口管理 agy 子进程，避免先搬动已经可用的登录环境。Docker 作为隔离部署候选，不是必须。
• 每个活跃创作会话绑定独立 CLI 进程与 conversation_id，数据库同时保存 owner_id 和 conversation_id 对应关系。
• 同一会话按顺序发消息，收到 result 才推进下一轮。
• 空闲可回收，下次按明确 conversation_id 恢复；绝不在多人服务中使用全局 --continue 续接“最近一次对话”。
• 常驻管理服务不等于把10个完整 Agent 永久开着；进程数量、空闲策略、队列背压按机器实测设定，是资源保护而非每日业务用量配额。
• 媒体 worker 独立运行；CLI 退出或重启不删除任务，不重复提交已知供应商工作。
• 保留启动器对 SSH 环境的处理，不改变现有 Google 登录状态；独立运行身份／容器如何安全使用官方授权需单独验证，不复制 token 到自制请求器。
• 若使用 Docker，授权状态不能烘焙到镜像，不挂整个 HOME／源码目录／Docker socket，不用 privileged 模式。持久卷及嵌套沙箱兼容性需实测，不能声称仅写了 Dockerfile 就实现隔离。

▶ 精确权限的官方依据
官方权限格式为 action(target)，MCP 格式 mcp(server/tool)；优先级 Deny > Ask > Allow。
因此不能先 deny mcp(*) 再期待 allow 某个具体工具把它放行。正确做法是仅加载本项目工具，精确 allow 必要工具，其余 MCP 默认 Ask；非交互时自动拒绝。文件／命令／网页等能力应使用各自的限制，并验证实际生效。
文档说明工作区内读写默认可自动允许；--sandbox 主要约束终端命令，不等于整个 Agent 所有工具都已封闭。模型提示词“不要读文件”不能代替权限与运行隔离。
本轮不改用户全局权限配置。

▶ 条款中确切存在的限制
Antigravity 附加条款原文：
“Using third party software, tools, or services to access the Service (e.g. using OpenClaw with Antigravity OAuth) is a breach of this Agreement.”
前一句还包含“using the Service in connection with products not provided by us”；后文列出可能暂停／终止 Antigravity 或 Gemini CLI 账号的后果。
FAQ 对第三方软件使用 Antigravity 登录也明确重申禁止，并建议第三方 coding agent 使用 Vertex 或 AI Studio API key。

但本项目计划不是提取授权给第三方客户端，而是调用官方 agy CLI。headless 文档明确支持“use the agent’s output in a program”和程序持续对话；条款也允许用户监督自主 Agent 的生产用途。因此不能把 OAuth 转接禁令简单等同于禁止全部官方 CLI 脚本，也不能把技术示例解释成对所有网站转供场景的合同许可。
通用条款允许组织／商业使用的相关安排，软件许可同时写有 personal、non-assignable。不能仅凭这些用语断言“任何商业使用都被禁止”，也不能忽略附加条款对第三方产品接入的限制。
Plans 区分个人 Google 条款与团队 Cloud 条款；企业路径可能涉及许可或消耗计费，不等于用户当前 Pro 已有企业权益。本轮不创建 Cloud 项目、不购买许可、不切换账号通道。
如后续需要取得明确的许可结论，应由有权的官方人员对具体场景答复，社区普通用户的经验或“能跑通”不能作为许可依据。已另存一份中文询问草稿，未向外发送；根据用户最新决定，当前暂停该确认流程，不作为受控测试前置。

▶ 用量与费用的新发现
Antigravity Plans 说明个人 Pro 额度按五小时窗口刷新并有周上限，消耗与 Agent 工作量相关；不能套用 Gemini CLI 文档的每日请求数作为 agy 额度承诺。
官方允许超额时使用个人 AI credits，受 AI Credit Overages / useG1Credits 控制。不设应用业务配额不代表授权消耗付费 credits，也不代表供应商无限额。
本轮只检查设置中相关字段是否存在，没有读取／输出任何 token；两个相关本地设置文件未发现显式开关，不能据“字段缺失”断言远端超额消费已关闭。下一步在启用共享服务或进一步用量测试前，需要验证实际设置为不自动消耗 credits；未完成账单核验。
同理，个人服务的数据使用／改进偏好需核对，并向内测用户说明请求会送往第三方；不能把个人通道说成企业级隐私保障。

▶ 用户本轮变更与域名核实
• 暂不设置每日／每人／全站业务用量或存储配额；仍尊重提供方自身限额，保留并发、磁盘与请求安全保护，不承诺无限资源或收费回退。
• 暂不实施备份，不再把异机备份／RPO／RTO作为当前内测的必须验收；明确当前无数据恢复保障。进程重启后按现有数据库恢复任务仍保留，不等于备份。
• 继续使用现有阿里云购买的域名 https://ai.pannel.kris42y.tech 。旧记录与配置表明前端为 Vercel、后端经 Cloudflare 隧道到当前机器；购买域名的平台不等于服务器所在地。
• 本轮对该域名首页及 /api/health 做只读 GET，均为HTTP200。未改DNS、转发配置、Vercel部署或线上业务，也未据健康页200宣称全部生产链路已验证。
以上已同步进入 Spec v0.2，尚未批准的首版范围、默认档位和账号／删除细则仍保留原状态。
