【第09阶段：真实运行装配说明】

▶ 状态与范围
• `OMNIFLOW_PROVIDER_MODE=real` 已连接实际 API／RunManager／ToolGateway／TaskWorker、官方 agy 启动器、Agnes HTTPS 下载及 FFmpeg。生产代码不导入 tests，也没有自动假生成入口。
• 默认仍为 disabled。没有配置、有效证据或独立执行器时拒绝新增；API 启动不会顺便运行 worker 或调用供应商。
• 本轮只用合成登录文件、可执行替身、HTTP 替身及合成图片测试「同一装配入口」。真实 agy／Agnes、真实免费状态和正式发布未完成。整体发布授权已取得，不再询问 A/B；第10阶段准备镜像／发布工具与同源静态交付。

【一、运行配置】

▶ 同一配置交给已启用的独立服务
```bash
export OMNIFLOW_ENVIRONMENT=production
export OMNIFLOW_PROVIDER_MODE=real
export OMNIFLOW_RUNTIME_CONFIG=/REVIEW_REQUIRED/private/runtime.json
export OMNIFLOW_DATA_DIR=/REVIEW_REQUIRED/independent-data
export OMNIFLOW_PUBLIC_ORIGIN=https://ai.pannel.kris42y.tech
export OMNIFLOW_ALLOWED_HOSTS='["ai.pannel.kris42y.tech","localhost","127.0.0.1"]'
# 存储仅由显式 migrate 初始化；不得指向旧站库。
.venv/bin/python -m omniflow.cli migrate
.venv/bin/python -m omniflow.cli run-manager --workers 4
# 分别在其他受控前台终端运行：
.venv/bin/python -m omniflow.cli media-worker
.venv/bin/python -m omniflow.cli serve --port 8765
.venv/bin/python -m omniflow.cli runtime-check
```
• 这是待现场配置的命令说明，本轮没有按这些占位地址运行生产服务。第10阶段再生成用户 systemd 与发布工具；不能复制既有 disabled 示例冒充完整新版。
• `runtime-check` 不调用供应商，输出运行配置摘要、已启用能力的就绪／固定拒绝原因，未装配项显示 disabled，不作为失败。任一已启用能力条件不满足退出 1；`/health/ready` 同样只核对配置启用范围。ready 不是已成功生成。
• API 与已启用的执行器必须使用同一数据目录、来源、配置及证据公钥。配置或启用提供方的凭据内容变化后，旧心跳和旧签名不能供新运行身份使用。服务重启重新读取配置；不提供管理网页修改模型／密钥／证据的入口。
• 密钥只能放显式私有文件，不放环境变量、命令参数、提示词或普通日志；配置与密钥文件必须当前运行用户所有、普通文件、0600、无符号链接。原 `.env` 和全部 HOME 不会被自动扫描。

▶ runtime.json 字段
• `version`：固定 1。
• `enabled_kinds`：非空、无重复的 text／image／ai_video／local_motion 列表；省略时兼容原完整四类装配。生产视频先行应显式设为 `["ai_video"]`。此范围被证据签名绑定，管理员的本地开关不能启用未装配能力。
• 只有启用 text 才要求 rootfs／rootfs_sha256／auth_files／google_hosts；只有启用 image 或 ai_video 才要求 agnes_key_file／download_hosts。未启用提供方的材料不会读取，不要求对应费用证据或执行器；仅 local_motion 也不要求 Google／Agnes 材料。
• `rootfs`：经审查的独立、只读运行镜像绝对目录；不能是 `/`、宿主 `/usr` 拼出的未经审查全量根或原工作区。
• `rootfs_sha256`：`runtime_launcher.image_digest(rootfs)` 对实际目录类型、模式、相对路径、链接和逐文件内容摘要生成的规范摘要。不能仅放一个自报 manifest 作为校验。
• `auth_files`：1–8 项 `{source: 绝对文件路径, target: .gemini/下的单个相对文件名}`。账号持有者核对当前官方安装版本真正需要的最小登录文件，逐个读取并将核验过的密封内存快照只读挂载；不挂目录、其他会话、插件、settings.json 或 mcp_config.json。测试专用 `.gemini/synthetic-auth.json` 不是官方授权路径，不得复制上线。
• `agnes_key_file`：显式 Agnes 私有授权文件绝对路径。启用 Agnes 时，API／执行器读取同一最小材料以绑定身份；只有媒体 HTTP 传输发送该授权，不传给 CLI。
• `evidence_file`／`evidence_public_key`：私有签名证据 JSON 与 RSA 公钥 PEM 的绝对路径；签名私钥不得交给 API、worker 或 CLI 镜像。
• `download_hosts`：现场确认的 Agnes 存储「精确域名」列表；不能填 `*`，不接受 URL／端口／私网。启用 Agnes 时空列表拒绝启动，不猜域名后自动重试。
• `google_hosts`：当前官方 CLI 实际需要的精确 HTTPS 出口域名；由现场核验后配置，不开放整个 Google 域或任意网络。测试只使用合成受控出口，未宣称已枚举实际登录／生成／遥测域名。
• `reference_editing_enabled`：默认 false，启用值同样被运行配置签名绑定；不得以字段启用代替真实参考图编辑验证。
• 所有未声明字段拒绝。没有 free=true、isolation=true、付费兜底或忽略证据的运行配置选项。

▶ 视频先行的最小配置（只有占位路径，无真实凭据）
```json
{
  "version": 1,
  "enabled_kinds": ["ai_video"],
  "agnes_key_file": "/REVIEW_REQUIRED/private/agnes.key",
  "download_hosts": ["storage.review-required.example"],
  "evidence_file": "/REVIEW_REQUIRED/private/evidence.json",
  "evidence_public_key": "/REVIEW_REQUIRED/private/evidence-public.pem"
}
```
• 存储域名必须换成现场核实的精确域名，不能照抄占位值。只启动 API＋media-worker，不启动 run-manager；仍通过原上传、确认和 POST /tasks，不新增临时视频通道。签名 evidence 只需 ai_video，不能据此宣称文本或生图已验证。
• 不要求 agy、Google 授权、bwrap 镜像或 FFmpeg 运镜执行文件；视频结果仍使用现有 ffprobe 做基本解码与真实元数据检查。Agnes 授权、签名验证、免费条件、限流、私有文件、执行器心跳和持久任务规则全部保留。

【二、进程与网络隔离】

• 以下 agy 隔离要求仅适用于启用 text。宿主必须具备可用 `/usr/bin/bwrap`、用户命名空间及 `/usr/bin/openssl`；本轮已在此宿主实际运行 bwrap＋合成 Python 镜像，没有 sudo 或安装全局组件。
• 镜像顶层仅允许 usr／bin／sbin／lib／lib64／etc／opt。实际按顶层条目只读挂载到全新的临时根，不挂宿主根；缺少镜像内 `/usr/local/bin/agy`、`/usr/bin/python3` 或依赖则失败。Python 标准库供固定网络入口使用，不是模型 shell 工具。
• 独立用户／PID／IPC／UTS／网络空间、能力清除、独立会话、`--die-with-parent`；仅本对话 HOME/workspace 可持久写。宿主数据库、媒体库、其他对话和普通配置均不挂入。
• 最小 Google 授权读取为绑定签名身份的快照，启动时核对源文件仍一致；通过带写入／增减大小／再封口禁止的 memfd，交 bwrap `--ro-bind-data`。不是先检查再挂可能已换号的原路径，不产生宿主磁盘上的临时凭据副本；宿主在 spawn 返回后关闭自己全部描述符，进程退出由系统回收其挂载。源授权、持久 HOME、argv 和提示词均不写入凭据内容。
• HOME 下仅生成本对话 settings.json，并把其再次只读覆盖挂载；已存在但内容不同直接拒绝，不悄悄覆盖可疑配置。具体型号、stream-json、--sandbox、--json-schema 均为已保存官方文档列明的参数；精确 --conversation 才恢复，禁止 --continue。
• 此入口使用官方 structured_output 的 text/actions，由可信进程的 ToolGateway 校验执行；因此 CLI 内 MCP 全拒绝，不把五工具 allow 与 mcp(*) deny 同时写成虚假放行。文件、命令、网页和 unsandboxed 工具全部拒绝。输入给模型的是固定工具参数契约和当前消息，不含 Cookie、真实令牌、服务器 owner/run 或原历史目录。
• 网络空间不能直接访问公网或宿主回环。固定入口在隔离空间的 127.0.0.1 建立 HTTP 代理，流量经专用 Unix socket 到宿主受限 CONNECT broker。broker 本身不监听宿主 TCP／公网；只允许明确域名的 443，DNS 所有返回地址都须为公共地址，并连接固定 IP。
• broker 不解密 TLS，不记录地址、头或内容；官方 CLI 自行验证 TLS。任意域名／其他端口／HTTP URL／私网／混合 DNS 被拒绝，单次头部有界、并发最多16、转发时限600秒。HTTP_PROXY/HTTPS_PROXY 只传隔离空间代理地址，不继承宿主环境。
• 独立会话关闭核对自身进程退出，随后关闭自己的 broker；未知退出与坏协议仍保持待核对，不全局 pkill 或强行删除持久占用。
• 已实际验证合成宿主文件、另一对话文件不可见，PID 空间受限，宿主环境未继承，受控网络出口可传合成数据且非法出口拒绝。该事实不是「真实 agy 已无法逃逸」的完整承诺；真实版本、内置工具、授权刷新及长时退出仍须现场核验。

【三、可信证据与费用条件】

▶ 谁核验、谁签名
• 生产只保留公钥。账号持有者在受信任、与服务分离的签名环境完成核验并签名；私钥不挂到运行镜像或服务配置，不能让 API／模型给自己盖章。
• 签名验证使用 `openssl dgst -sha256 -verify`，校验 RSA/SHA256。证据封装精确为 `{payload, signature}`，signature 为 Base64。签名对象为 `runtime_config.canonical(payload)` 的原始字节。
• payload 精确包含 `config_sha256`、`checks`、`evidence`。config_sha256 来自 `runtime-check` 所打印摘要，绑定完整运行配置、公钥摘要、已启用提供方的实际最小凭据内容摘要、独立数据目录及网站来源。只记录路径的旧签名不再有效。
• checks 精确包含 billing／overages／isolation／authorization／protocol，每项为 `{method, record_sha256}`。billing、overages、authorization 必须是 account-holder-readonly；隔离／协议也允许 offline-probe。record_sha256 指向私有原始核验记录的摘要，不把秘密或原始账号页面放入服务响应。
• evidence 以已启用能力（text／image／ai_video／local_motion）为键，每项遵循 `AccessEvidence`：精确 model、实际 checked_at／expires_at、available、free、overages_disabled、limit_reached、isolation_verified。字段必须反映该次真实核验，签名只是来源与完整性证明，不自动证明人工写的内容真实。
• 现有最长有效期300秒不变；未来时间、过期、错误型号、费用未知或非严格布尔值均不能放行。读取证据不刷新 checked_at；没有内建签发、按时改时间或管理员强制恢复功能。第10阶段可以提供离线材料／签名工具，但不得自动延续旧核验。

▶ 凭据轮换与检查后替换防护
• Agnes 和 Google 分别核对自身材料：常驻每次费用核验检查原路径内容是否仍匹配启动快照。原地换号、令牌轮换、删除、权限变宽或符号链接均不能继承旧证明。未启用提供方没有读取要求。
• Agnes 实际请求再次核对原材料，然后返回同一份已检查快照，不在检查后重读可能已经替换的密钥。Google 实际挂载也使用同一密封快照。已经在途的请求无法事后召回，但不会改用未核验的新账号。
• 轮换步骤：暂停新增→账号持有者只读核实新材料对应账号的费用／授权→针对新运行摘要签发新证据→重新启动使用该配置的 API 和执行器。只有换文件、只有重启或只有旧心跳均不放行。Google 正常刷新导致最小材料字节改变也采用同样保守流程，不擅改官方登录。
• 已受理任务仍保留原任务 ID，不因换号重新提交；新材料若无法查询原账号任务，需现场恢复该账号的有效授权后续查，不改为新生成。

▶ 不能自动声称已经核验的事项
1. 官方 CLI 的 useG1Credits=false 在独立设置中真实写入并只读保护，但不能推导远端 AI Credit Overages 或账单已关闭。账号持有者需要在官方账户界面只读核对订阅、当前权益、剩余额度、超额状态及账单，并保留脱敏记录；无法确认就不签免费证据。
2. 本项目保存的官方文档未承诺可自动查询以上账单状态的稳定协议。本轮没有编造内部接口、提取 OAuth token、以模型回答作证明或调用真实账户。五分钟之外无新的真实核验就暂停新增，不能拿自动重签实现永久免费声明。
3. 现有官方登录的最小文件清单、只读挂载下的刷新行为、CLI 镜像依赖和精确网络域名必须由现场账号持有者／主控核对。需要重新认证时仍由账号持有者在官方流程中操作，代码不复制整个授权目录、不更改原 Google 登录。
4. 供应商429继续先持久化；即使重建真实 Runtime 或更换新有效证据，也不能清除等待截止。等待结束后还要求证据晚于该限流事实。已受理任务继续查询／保存；没有查询线索的提交未知绝不重发。
5. 共享条款未独立确认仍仅记录风险，不重新增加为当前受控测试前置，不重复索取整体发布授权。

【四、执行器就绪与恢复】

• 文字／媒体 CLI 启动先按启用范围核对本地运行材料，再建立独立心跳；未装配角色拒绝启动，不写心跳。每2秒原子更新当前角色文件，10秒过期。文件绑定配置摘要、进程 PID、随机 token 和原始写入／到期时间；不存在、进程已退出、时效异常或配置不同均不可用。
• API 不写心跳。正常 worker 退出撤回自己的 token，不删另一执行者覆盖的记录；异常退出由 PID／时效检查拒绝。当前同一角色推荐一个 CLI 服务（文字内部 --workers 1–8），避免多个同角色服务轮流覆盖心跳导致短暂保守不可用。
• 心跳表示执行器服务存活，不代表当前对话已有热进程，也不证明一次供应商请求成功；还必须叠加有效证据、持久限流、本地暂停及磁盘检查。模型不可用不会产生假回复。
• 租约、幂等、不可变版本、grant、提交未知、下载恢复均复用既有业务层和数据库；没有改写迁移1–8，没有操作旧站或生产库。
• 媒体 worker 的启动不以「免费证据仍有效」为必要条件：已有供应商任务可继续续查／下载；新增入队和实际提交仍须通过全部闸门。

【五、离线验收与现场接续】

```bash
.venv/bin/python -m pytest backend/tests/test_production_runtime.py backend/tests/test_runtime_boundaries.py -q
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run test -- --run
npm --prefix frontend run test:e2e
```
• 新测试构造独立签名密钥、镜像、合成授权材料，实际执行生产 CLI 及 bwrap；绝不能把测试镜像或 signing.pem 用于真实部署。
• 测试需要当前已有 Linux bwrap、Python、openssl、FFmpeg，不自动下载模型或工具。短 Unix socket 使用自身私有临时目录，正常退出自动清理；网站测试仍只在127.0.0.1。
• 第10阶段继续准备运行镜像／配置检查与用户 systemd、同源 dist、代理检查、发布／保留数据的回退和真实验收记录工具。本轮没有生产静态站点、域名、隧道或 Vercel 切换。
• 主控现场负责已启用能力的真实供应商、费用与授权、安全、媒体真实元数据及完整 HTTPS 代理链验收。用户最新授权为视频先行：视频自身通过后可发布视频可用版，不要求未启用的 Google／agy 先通过；其余能力保持明确不可用。没有真实视频成功不能称为视频试用版，不用全部 AI 禁用的基础版替换旧站。
