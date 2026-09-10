【Gemini 登录后文本验证记录】

▶ 本轮目标
用户报告“我已登录”后，验证独立环境能否复用 Google 授权，并使用指定模型 gemini-3.8-flash 完成一条无工具文本请求。不生成图片／视频，不修改正式服务。

▶ 实际检查结果
独立目录：/home/kris/.local/share/omniflow-gemini-probe/config-home/.gemini/
• settings.json 的 selectedType 与 enforcedType 均为 oauth-personal。
• google_accounts.json 存在，但当前 active 账号为空；未输出账号信息。
• oauth_creds.json 不存在，专用配置目录中也未发现 gemini-credentials.json。
• 账号／设置文件最后修改时间约为本机 2026-09-08 14:49:42，早于本轮 14:52 的模型探测。不能认定是本轮请求删除了登录状态。
• 本轮只检查凭据文件是否存在及账号元数据状态，没有读取、复制或输出 OAuth token。

▶ 最小请求的实际结果
运行仓库之外的本人验证脚本：
/home/kris/.local/share/omniflow-gemini-probe/probe-text.py

固定请求 gemini-3.8-flash，禁止全部工具，使用隔离 HOME、官方 CLI OAuth 认证及 JSON 输出；环境未提供 API key 或按量计费配置。
约 1.23 秒后退出，exit code 41：
“Manual authorization is required but the current session is non-interactive.”
结果中没有模型用量统计、没有生成文本；失败发生在认证阶段，不能据此判定 gemini-3.8-flash 是否支持，也不能声称 Pro 模型验证已通过。
无图片／视频任务提交，未启用备用模型或切换正式文本服务。

▶ 原因判断与不确定项
已确认当前进程找不到可复用的登录授权。
已安装 CLI 源码中的 clearCachedCredentialFile 会同时删除凭据并清空 active 账号，现状与退出登录后的表现相符；但本轮没有证据确认用户是否执行了退出登录或 CLI 是否经历其他认证重置，因此不把原因归咎于用户，也不宣称找到了具体触发动作。
此前曾登录的痕迹不等于当前授权仍可复用。安装脚本、账号信息与权限存在，也不能代替实际认证成功。

▶ 后续更新：优先复用 agy，不再要求本段重登
用户反馈反复授权后，已按建议使用服务器现有 /usr/local/bin/agy；无需重新登录即可列出 Gemini 3.8 Flash 并成功完成一次指定型号文本测试。详见 agy复用授权与Gemini文本实测.md。新安装 Gemini CLI 的授权问题尚未解决，但不再阻塞当前文本验证。

▶ 原先建议的恢复动作（已由 agy 路径替代，不必再执行）
原先建议用户在自己的 SSH 终端重新运行：
/home/kris/.local/share/omniflow-gemini-probe/login.sh
完成 Google 官方授权后，保持 CLI 界面打开，不选择退出 Google 账号；授权码仅输入该终端，不发给助手。
之后从另一进程复测持久授权与指定模型。无需重装 CLI、无需复制 Cookie、无需提供 API key，也无需改网站。

▶ 原始结果位置
/home/kris/.local/share/omniflow-gemini-probe/results/flash-3.8-summary.json
原始 stdout／stderr 留在同一受限目录，文件权限由 077 umask 限制。本报告不包含账号或授权秘密。
