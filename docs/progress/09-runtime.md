【09-runtime：真实生产入口与隔离进程接线】

▶ 本轮结论（2026-09-09，coder 尝试1）
• 本阶段生产装配代码与替身回归通过，交外层独立 reviewer。最终后端850 passed，保留原809项，新增41项；前端82 passed，浏览器 E2E 5 passed，Vite 构建通过。编码者自查不冒称第09阶段独立审查已经通过。
• 已实质连接 real 配置下的 API、RunManager、ToolGateway、TaskWorker、官方 agy 隔离启动器、Agnes HTTPS／下载与 FFmpeg；不导入 tests 上线，不以默认禁用模式或假生成作为新版生产入口。
• 同一 `python -m omniflow.cli` 入口实际执行合成 agy 镜像、合成官方登录文件及本地合成图运镜，验证跨进程配置、独立目录与精确恢复；不是仅在测试里另写一套启动入口。
• 真实 agy／Agnes、现场费用／授权／真实版本隔离验证及生产发布「未完成」。用户已经批准发布并选择完整新版，不再询问 A/B；第10阶段继续发布工具，现场核验与切换由主控按已获授权执行。

【一、依据和实际差异】

• 完整阅读实施计划、Spec v1.0、FastAPI 接口文档、完整新版发布实施计划、发布预检；阅读08进度、任务指定08独立审查报告及直接依赖05记录。
• 实际检查 cli／app／Settings、RunManager、TaskWorker、ToolGateway、媒体路由、Agnes、FFmpeg、EvidenceGate、HTTP 传输、能力和健康接口；不重做已通过的账号／素材／任务业务层。
• 阅读项目已保存的 `agy官方常驻调用与多人使用条款核对.md`，完整阅读既有公开 headless／permissions／settings 文档缓存。官方文档明确列明指定 --model 时 init.model、--json-schema 时 init/result.json_schema，以及 structured_output 和对应序列化 response；没有新增官方未承诺的 isolation/free 等协议返回字段。
• 发布预检指出的缺口真实存在：Settings 原仅 disabled/mock，生产 CLI 未装配真实提供方，media_provider 禁止生产注入。现通过明确 real 配置与内置 Runtime 工厂补齐；默认拒绝和生产禁止任意测试注入仍保留。
• 实现选择 Spec/API 已允许的「官方 structured_output→可信 ToolGateway→统一任务服务」。CLI 内不另装外部 MCP，因此全部 MCP deny，不能一边 mcp(*) deny 一边声称五工具 allow 生效；原受控网关的归属、确认、数量和幂等规则不变。
• 用户撤销视觉／审美复审后，本轮未调用视觉模型、未读 PNG 验收、未调整配色；保留既有响应式／键盘／弹窗和布局功能测试。

【二、实质实现】

▶ 一致的真实装配
• Settings 增加 `provider_mode=real` 和必须显式指定的绝对 `runtime_config`，不扫描原 `.env` 或授权目录；production 仍拒绝 mock／调试／开发来源。
• `runtime.py` 统一装配 AgyProvider＋AgyLauncher、AgnesProvider＋SafeHTTP＋PinnedHTTPSExchange、FFmpegProvider／MediaRouter。API TaskService、默认 TaskWorker 和默认 RunManager 使用相同工厂；文字管理器自动接入原 ToolGateway。
• AgySession 给模型提供原有封闭工具参数契约与当前消息，动作仍由可信管理器使用 run＋动作序号校验执行；不把 owner/run、Cookie、提供方 key 或主机文件内容放进提示。
• 增加 `runtime-check` 只读命令，输出配置摘要、四类运行条件和固定错误码；不生成、不签发证据。配置不足时非零退出，不伪称生产已经可用。
• 既有任务入队／提交前闸门、429 持久暂停、首帧确认、不可变版本、提交未知、原结果续查／保存、requested／actual 差别均复用，未改迁移1–8或公开成功契约。

▶ 真正的进程／网络边界
• AgyLauncher 对实际只读镜像核对摘要及权限，不信任自报 manifest。镜像内是经审查的官方执行文件与必要依赖；测试镜像包含合成 Python 可执行替身，不是生产 agy。
• 启动 bwrap 的独立用户／PID／IPC／UTS／网络空间，清除能力、独立 session、die-with-parent；在新的临时根只读挂镜像顶层，单独挂本对话 HOME/workspace，不挂宿主根、数据库、媒体库或其他会话。
• 每 owner／conversation 私有目录；首次不带恢复参数，重启只带精确绑定 UUID。官方型号仍 `gemini-3.8-flash-low`，不使用 coding 模型或别名。
• 账号持有者提供经核验的「单个最小授权文件」挂载清单，只读挂进该 HOME；禁止整个授权目录、settings、MCP 配置、相对来源或越界目标。设置写入自己的私有 HOME 并只读覆盖；既有设置不符直接拒绝，不更改原 Google 配置。
• 文件／命令／网页／unsandboxed／全部 MCP 工具拒绝。没有 `--dangerously-skip-permissions`、全局 --continue、shell 拼接、宿主 HOME／PATH／代理环境透传或真实凭据日志。
• 隔离空间内的固定代理通过专用 Unix socket 到宿主 CONNECT broker；后者不监听宿主 TCP。只放精确域名的443，校验全部 DNS 地址并固定公共 IP，拒绝私网／混合地址／转换范围／其他域名／端口／HTTP URL。并发、头部、转发均有界；TLS 内容不解密、不记录。
• 实際替身测试证明宿主合成文件、另一对话文件不可见，PID 范围隔离、宿主环境未继承、合法代理可传合成数据且非法出口拒绝。此证据不等于真实 agy 所有内置工具、授权刷新或长时退出已经验证。

▶ 可核验条件，不靠布尔开关上线
• 新增严格私有配置和 RSA/SHA256 签名证据读取器。应用只有公钥，没有签发／自动刷新接口；签名绑定完整配置、公钥摘要、独立数据目录、来源以及费用／超额／隔离／授权／协议记录摘要。
• 费用、超额和授权记录必须标记账号持有者只读核验；不能拿 offline-probe 或公开价格页代替。签名保证来源和完整性，不能自动证明人为填写内容真实；具体人工证据流程和缺项已写入 `backend/RUNTIME.md`。
• 既有最长300秒有效期不变；读取不更新 checked_at。未知、过期、未来时间、错误型号、超额未关闭、非免费、限额或未验证隔离继续拒绝。没有随手 free=true/isolation=true 的运行开关，没有强制绕过或付费回退。
• 公钥内容启动时固定；轮换须重启重新计算运行身份，不允许旧心跳接受新公钥对旧配置身份的签名。
• 未发现官方稳定的账单／超额自动只读接口，故没有编造采集器、提取 OAuth token 或自动续签。需要账号持有者现场提供真实核验；五分钟之外没有新核验就暂停新增，明确不承诺持续免费可用。

▶ 独立执行器心跳
• 文字／媒体 CLI 先核验本地材料，再按角色发布私有原子心跳；每2秒更新、10秒过期，绑定配置摘要、PID、随机 token、原始写入和到期时间。
• API 不写心跳。缺失／过期／退出／配置不一致均不允许将能力标为可用；正常退出仅撤回自己的 token。real 模式 ready 叠加全部能力闸门；live 始终只是 Web 进程存活。
• 当前建议每个角色一个 CLI 服务，文字内部 --workers 1–8；多个同角色 CLI 轮流覆盖心跳可能造成短暂「保守不可用」，不会绕过任务领取互斥，未扩展分布式执行器注册中心。
• 心跳是执行器服务状态，不是一次模型成功证明。已受理媒体可在免费证据过期后继续查询／保存，新增仍逐次核验。

【三、测试、失败与修复】

▶ 新增41项
• real 配置必填、生产拒绝任意假提供方注入、缺证据／缺 worker／live 与 ready 区分、runtime-check 的真实 CLI 输出及退出码。
• 签名但费用未知／超额未关／证据过期／未来／限流／隔离不足拒绝；签名篡改、配置改变、公钥轮换不能复用旧身份；心跳过期、PID 退出与正常撤回。
• 实际生产 run-manager CLI＋bwrap＋合成登录文件，A/B/A 三次独立进程验证新上下文、精确恢复、原历史保持和正常退出；坏协议待核对，重启不自动重放。
• 同一真实装配的结构化图片动作进入统一任务服务；Agnes HTTP 合成传输验证只有一次 POST，重建 TaskWorker 查询／下载同一结果，存储 GET 无 Authorization。
• 实际生产 media-worker CLI 三次独立执行，合成上传图经真实 FFmpeg 完成 local_motion，来源为 local-ffmpeg，退出撤回媒体心跳。
• 实际隔离环境读取已存在宿主合成文件／另一对话文件失败，PID 与环境隔离；隔离内代理→Unix socket→受限宿主 broker→127.0.0.1 合成服务正常交换字节。
• 7类非法出口／DNS拒绝、私有文件／符号链接／目录拒绝、6类非法授权挂载目标拒绝、镜像摘要错误、只读设置被篡改拒绝、镜像文件边界摘要不可混淆。

▶ 真实失败记录
• 首轮专项13 passed／2 failed：bwrap 直接只读挂整个测试根后不能创建 /proc。诊断捕获仅测试进程 stderr，发现 `Can't mkdir /proc: Read-only file system`；产品改为新临时根＋逐顶层只读挂镜像，保留全部隔离要求。随后16项通过。
• 扩展出口测试最初3项失败：当前 worktree 的 pytest 路径超过 Unix socket 长度上限，同时暴露构造失败未关闭 socket。产品补构造失败关闭，测试使用自身短私有临时目录；不是放宽目的地或网络限制。随后37项通过。
• 本地运镜测试初次因误用既有上传 helper 的位置参数失败；核对 helper 后改为 cid=，真实 CLI＋FFmpeg 专项通过，未改变产品预期。
• 代码侧自查发现镜像摘要裸拼接存在文件边界歧义，改为规范类型／模式／路径／逐文件摘要记录并增加专门测试；再发现公钥轮换可在原运行对象中改变验证者，固定启动公钥并增加轮换拒绝测试。均为本轮新增代码具体缺陷，不重做旧业务。
• 最终总数850；首轮全量848、摘要修复后849、增加公钥轮换回归后850。后两次全量对应实际安全代码变化，不把之前成功当作新代码证据。

▶ 实际最终命令与结果
```bash
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run test -- --run
npm --prefix frontend run test:e2e
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 最终后端850 passed in 196.86s；所有原809项保留。最后代码仅变更签名读入器并新增公钥轮换测试；最终固定门槛实际覆盖这一修改。
• 前端82 passed，21.28s；E2E 5 passed，13.26s，前置实际执行 Vite build（24 modules）。前端源码未改，不重复相同成功浏览器测试；最终后端全量仍包含实际API契约回归。
• Vite 产物仍为 index-Bp2rnhwE.js 与 index-DvZ_Iile.css。没有视觉审美门槛或模型读图。
• Ruff／95个 Python 文件格式、空白和列明旧站／部署文件差异检查通过。文档契约40路径／51操作／64 schema／525引用、22示例／8文档JSON／1SSE／22类拒绝通过；未提供官方元schema，不冒称完整官方结构验证。
• 新增安全专项最终在公钥轮换用例之前40 passed in 17.17s，轮换用例随后纳入850项最终全量，未用旧专项总数冒称包含新增用例。
• 日志：`backend/var/09-runtime-targeted.log`、`09-runtime-final-targeted.log`、`09-backend.log`、`09-backend-final.log`、`09-backend-delivery.log`、`09-frontend.log`、`09-e2e.log`。这些是本地忽略的合成测试输出，不是正式运行证据。
• 14:51检查本轮 omniflow.cli／runtime-entry／runtime_fake_agy／fake_agy／e2e_server／Vitest，无残留匹配进程；只清理自己的测试资源，未结束其他会话。

【四、文件与完整性】

▶ 新增
• `backend/src/omniflow/runtime.py`
• `backend/src/omniflow/runtime_config.py`
• `backend/src/omniflow/runtime_launcher.py`
• `backend/src/omniflow/runtime_egress.py`
• `backend/src/omniflow/sandbox_entry.py`
• `backend/tests/test_production_runtime.py`
• `backend/tests/test_runtime_boundaries.py`
• `backend/tests/runtime_fake_agy.py`
• `backend/RUNTIME.md`
• `docs/progress/09-runtime.md`

▶ 修改
• `backend/src/omniflow/config.py`、`cli.py`、`media_provider.py`、`run_manager.py`、`artifacts.py`、`agy_adapter.py`、`safe_http.py`、`provider_gate.py`、`api/health.py`。
• `backend/README.md` 更新第09阶段现状与启动边界；旧05阶段描述中已过期的启动器／证据声明同步纠正。
• 无依赖安装、锁文件／迁移变化，无前端产品改动。未改原 server.py／index.html、生产部署配置、实施计划、控制器、控制状态或批准原型。原有未提交成果保留；无 commit／push／reset／clean／merge。
• backend/uv.lock：`9ae464f9d5027c4331dd305c9a41295916cfb695c817b8d350bdac9c10778396`。
• frontend/package-lock.json：`056032b9f039423e22a355da2acfc19e9b142869bec7ba8f454361b9d796d80f`。
• 批准原型：`be8f15d9510bfb6a2bd937409c142fa7473876c17f901a7e495228ece1189448`，保持不变。

【五、未验证与后续】

1. 外层 reviewer 独立复核真实工厂入口、bwrap 只读镜像／最小挂载／网络出口、签名身份与公钥轮换、心跳以及默认拒绝，并亲自执行固定回归；本报告不是独立审查替代品。
2. 第10阶段实现可审查镜像／配置制作和启动工具、用户 systemd、生产前端 dist 同源交付、安全代理检查、发布／保留数据的回退及真实验收记录工具。第09已有可运行装配，不以测试目录作为生产入口；本轮不越阶段切域名、Vercel、隧道或生产服务。
3. 现场账号持有者具体事项：核验当前官方登录最小文件及只读刷新行为，提供独立受保护 Agnes 授权文件，在官方账户界面核实权益／额度／AI Credit Overages 与账单、完成真实记录签名；管理员密码仍由本人终端隐藏输入。不给聊天或 argv 提供密码，不重复索取整体发布授权。
4. 真实 agy 安装版本／模型可用性／官方代理支持、精确 Google 出口、真实存储域名、真实内置工具权限和退出、长时运行、真实生成及参考编辑质量均未核验。合成 Python 镜像不能代替这些现场证据；失败必须保守拒绝，不靠改布尔值或不断更新时间放行。
5. 生产发布、生产迁移、真实供应商完整闭环、正式 HTTPS／Cookie／CSRF／SSE／HEAD／Range 代理链、Mac／iOS 真机播放「未完成」。不把准备好的生产代码或 HTTP200 当作已经上线，也不发布 AI 禁用基础版替换旧站。
6. 无备份／灾损恢复承诺；正常任务续查不是备份。旧站及原数据继续保留，最终切换由主控在真实核验通过后执行。

---
▶ In brief
• What's happening：真实运行所需连接已写好，850项后端检查与全部页面检查通过，交独立复核。
• Reason：不同聊天分开运行，只有可信核验仍有效且执行程序在场时才允许新增生成。
• Impact：旧网站不变；真实账号生成和正式上线尚未验证，时间仍不确定。
• Require Input：本轮不需要 input；现场需要账号持有者核对费用、授权并隐藏设置管理员密码。
