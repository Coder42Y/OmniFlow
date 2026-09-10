【Google AI Pro 视频权益与实测阻塞记录】

▶ 用户请求与当前结论
用户要求探索 Google AI Pro 是否也能为视频生成提供备用，并尝试验证其订阅能力。这是对先前“AI 视频仅 Agnes”方向的进一步探索，未据此启用 Google 备用或授权付费调用。
结论：官方权益确认 Google AI Pro 包含 Google Flow 视频创作能力；Gemini Apps 官方帮助也说明个人 Google AI 订阅账号具备视频生成资格。但本轮未进入用户自己的已登录会员环境，未提交视频任务、未生成样片，不能称为用户账号实测成功。

▶ 官方证据（本轮读取，HTTP 200）
1. https://support.google.com/googleone/answer/14534406?hl=en
“Use Google Flow”列出 AI Pro 更高的 Flow 使用权限，支持 Text to video、Ingredients to video、Frames to video，同时支持文生图与改图。地区、年龄、账号资格等限制仍适用。
2. https://support.google.com/gemini/answer/16126339?hl=en
视频生成要求个人账号具有 Google AI plan、登录 Gemini Apps，且当前不面向 18 岁以下用户。当前帮助页描述 Gemini Omni，可用模型随版本变化，不能只按旧印象固定写成 Veo。
3. https://labs.google/fx/tools/flow
服务器 Chrome 实际打开了公开落地页，显示 Gemini Omni、Nano Banana、Veo 3.1，并列出 AI Pro 方案的 Flow 使用权益。该页面不能证明用户个人剩余额度或所在地资格。
4. https://ai.google.dev/gemini-api/docs/google-ai-plans
Google AI Pro / Ultra 在 AI Studio 的开发者权益适用于网页界面；外部应用／API key 调用的 Gemini API 另行计费管理。
5. https://ai.google.dev/gemini-api/docs/pricing
当前 Veo 3.1 的 Standard／Fast／Lite 均标注 Free Tier 为 Not available；付费表按输出秒数计费。因此不能把 Pro 网页视频权益视作本站免费的 Veo API 用量。
6. https://support.google.com/googleone/answer/16287445?hl=en
Google One AI credits 与各产品额度的规则、历史查询及资格限制。不能把这些额度混同 Google Cloud credits。
7. https://ai.google.dev/gemini-api/docs/video
官方当前视频 API 概览分别介绍 Gemini Omni Flash 与 Veo 3.1；模型能力存在不等于用户账号可免费调用。

▶ 可进一步核验的 Cloud 赠送权益
Google AI Pro 官方权益页的 Google Developer Program premium 部分列出每月 $10 Google Cloud credits，要求有效 Pro 订阅关联开发者资料，家庭成员不能共享此开发者福利。
是否已关联、领取，是否适用于选定视频接口、当前余额与预付费激活条件均未核验；不把“可能抵扣”写成“已免费可调用”。不自动开通 Cloud Billing、不充值、不领取附带未确认义务的服务。

▶ 本轮实际检查
• 当前进程未配置 GEMINI_API_KEY、GOOGLE_API_KEY、GOOGLE_APPLICATION_CREDENTIALS、GOOGLE_CLOUD_PROJECT 或 GOOGLE_CLOUD_LOCATION。
• 当前 worktree 无 .env；原主目录 .env 存在，但未发现 GOOGLE／GEMINI／VERTEX 配置项。只输出字段是否存在，不输出任何密钥值。
• 服务器的 google-chrome 与 google-chrome-headless 配置目录无 Local State，未发现可用的账号配置元数据，也无正在暴露的常见浏览器调试端口。未读取或复制 Cookie、未扫描无关项目凭据。
• 使用全新隔离 Chrome 实际打开 Gemini /app 与 Flow 公开入口。Flow 落地页可见；Gemini 页面取得 HTTP 200，但本轮正文为空，不能据此判断账号资格或宣称进入登录页。
• Flow 没有进入用户已登录创作环境。用户在 Mac 浏览器的 Google 登录不会自动传到 SSH 服务器，当前无已授权可控的 Mac 浏览器连接。
• 无视频任务提交、无样片下载、无费用产生操作。未尝试绕过登录、地区限制、验证或计费。
原始官方页面、公开入口报告和截图保存在 .superpowers/google-video-check/；脚本为 check-web-access.cjs。

▶ 产品备用结论
A. 人工备用：有合资格 Pro 账号及剩余额度时，可以在 Google Flow／Gemini 网页创作，下载成片并上传工作台；这是一条待个人账号验证的人工路径，不是自动后台切换。
B. 自动备用：需要可用的官方视频 API 项目以及已确认的费用／赠送额度边界，单靠 Pro 网页订阅不足。本轮没有启用。
C. 当前已定 Agnes 路径保持不变。Agnes 免费优惠结束时仍提示并暂停新生成；若未来用户确认 Google 自动备用条件，再单独更新路由，不把本次探索当成默认切换许可。

▶ 下一步所需最小输入
用户在 Mac 用购买 AI Pro 的账号打开 Google Flow，提供会员／可用额度和创建视频入口截图（可遮住邮箱），或后续安排受控、已授权的浏览器访问。不要在聊天中发送密码、验证码、Cookie 或 API 密钥。
拿到账号可用性证据后，可先用无品牌、无真人、无私密资料的简单短片提示词做一条最低用量测试；仅在确认使用包含额度且不会触发额外收费后提交。未达到这一步前，不报告“订阅实测可用”或样片效果。
