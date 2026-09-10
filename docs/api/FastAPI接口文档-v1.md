【OmniFlow FastAPI 接口文档 v1】

【一、文档状态与阅读入口】

版本：1.0.0-draft，接口设计第一版；对应 ../specs/创作工作台重构-Spec-v1.0.md。
用户已确认Vue 3＋Vite、Python＋FastAPI、SQLite、独立worker、systemd。本文件定义待实现的接口，不是对现有server.py的自动导出，也不表示现有域名已经支持/api/v1。
本轮不启动FastAPI、不接生产数据库、不修改网站、不创建systemd服务、不调用模型或媒体提供方。

文件分工：
• openapi-v1.json：全部路径、方法、请求／响应schema、状态码及安全声明。
• 路由索引-v1.md：由相同源生成的逐接口清单，含请求类型、成功响应、鉴权和幂等要求。
• build_openapi.py：可重复生成契约与索引的文档工具。
• test_contract.py：验证契约引用、示例、访问控制声明及关键请求约束。
本文补充OpenAPI无法完整表达的事务、归属、SSE恢复和业务语义。发现冲突须先统一契约，不由前后端各猜一种行为。

示例均为虚构UUID和内容，不能当作真实账号、邀请、任务或有效授权。文档中的链接和Cookie格式仅示意，不能在日志、报告或源码中填入实际凭据。

【二、通用约定】

▶ 路径与传输
接口前缀/api/v1；OpenAPI servers使用相对路径，避免让导入工具自动对当前生产站点试写。
JSON使用UTF-8；普通响应application/json；错误application/problem+json；事件text/event-stream；文件使用实际MIME。
ID使用UUID字符串。应用conversation_id与Google CLI内部ID严格分开，后者不向浏览器返回。
时间使用带Z的RFC3339 UTC字符串；事件序号使用十进制字符串，避免JavaScript大整数精度损失。不存在百分比进度时不伪造progress字段。

▶ 请求／响应
请求对象按extra=forbid处理，拒绝未声明字段；尤其不接收user_id、owner_id、CLI ID、任意model、base_url、shell或本地路径。
读取响应是资源对象或分页对象，不套success=true通用壳。201表示资源已创建，202只表示已持久受理，绝不等于图片／视频已经生成。
X-Request-ID由服务端生成并出现在响应头；错误体包含同一ID。不能把提供方完整响应、堆栈或密钥返回给用户。
受保护响应使用Cache-Control: private, no-store；签发链接和CSRF响应使用no-store，页面设置Referrer-Policy: no-referrer。

▶ 分页
列表请求cursor为不透明游标，limit默认20、最高100；这是单次分页大小，不是业务用量配额。
返回items和next_cursor；没有后续页时为null。游标绑定资源、过滤条件与排序，不作为授权凭据；每次读取仍校验当前用户。
会话／任务／作品默认按(created_at,id)倒序稳定分页，列表中的更新时间不决定游标排序。
消息第一页返回最近limit条，页内按seq升序；next_cursor读取更早消息。版本列表按version_number倒序。
筛选conversation_id必须属于当前用户，不能用筛选参数越权探测他人内容。

【三、登录、CSRF与管理员权限】

▶ 网站登录
使用__Host-omniflow_session Cookie，HttpOnly、Secure、SameSite=Lax、Path=/，不设置Domain。网站会话与agy的Google登录完全独立，退出网站不清除Google授权。
身份由Cookie对应的服务端会话确定，禁止信任请求体中的用户ID。停用账号、注销或重置密码后旧登录失效。
未登录／过期返回401 AUTH_REQUIRED；对其他用户或不可见对象统一404 RESOURCE_NOT_FOUND，避免暴露对象是否存在。已认证但不是管理员访问/admin时403 FORBIDDEN。

▶ CSRF引导
1. GET /auth/csrf；无登录时设置HttpOnly的__Host-omniflow_csrf匿名上下文Cookie，并返回csrf_token。已登录时返回绑定该登录会话的令牌。
2. 所有POST／PATCH／DELETE请求携带X-CSRF-Token，包括登录、注册和邀请／重置校验。
3. 服务端校验Cookie上下文、令牌匹配、有效期和Origin。匿名CSRF上下文不是登录授权，不能凭它访问普通私有接口。
4. 登录／注册成功轮换登录会话及CSRF令牌，响应返回新csrf_token；客户端不能继续使用登录前的令牌。
5. GET／HEAD不改变业务状态；GET /auth/csrf仅建立防护上下文。CORS仅允许明确配置的同源／开发来源，不使用通配凭据跨域。
普通只读请求使用SessionCookie；登录后的修改请求必须同时满足SessionCookie和CsrfHeader。匿名表单允许“匿名CSRF上下文＋令牌”或“已有登录会话＋令牌”。OpenAPI中的多项同一security对象表示AND，不是任选其一。

▶ 账号路由
GET /auth/policy：返回用户名、密码、登录时长等实际策略，注册页动态读取，不硬编码体验参数。
POST /auth/invitations/validate：校验邀请用途、有效期和未消费状态，不消费，不能返回被邀请人的隐私。
POST /auth/register：同一事务创建用户并消费邀请；无公开任意注册入口。成功201 User与登录信息；邀请码无效用统一TOKEN_INVALID，用户名冲突409 USERNAME_UNAVAILABLE。
POST /auth/login：错误用户名／密码统一401 INVALID_CREDENTIALS。账号停用只在密码验证成功后报告ACCOUNT_DISABLED，不以错误密码探测账号状态。
GET /auth/me：读取当前用户；POST /auth/logout：撤销当前登录并清理Cookie，204。
POST /auth/password-resets/validate：校验专用重置token，不消费；POST /auth/password-resets/complete：事务消费、改密、撤销全部旧登录，204后要求重新登录。
密码与一次性token均不写入应用／代理访问日志，验证错误不回显输入值；不能原样返回FastAPI默认验证错误中的input字段。

▶ 默认策略的地位
第一版建议用户名3–32位ASCII字母／数字／下划线，转小写；密码6–128字符（用户于2026-09-09明确调整为至少6位）；邀请7天、重置30分钟、网站会话7天。这些是Spec第十二部分的设计默认，发布前复核，不冒称此前用户逐项批准。
OpenAPI请求只给出防滥用的外层长度限制，服务端按/auth/policy实际规则再验证。密码不trim、不转小写、不截断；用户名仅按公开策略规范化。
不存在邮箱／短信找回，不提供虚假的自动验证流程。管理员经既有可信联系渠道或当面核验后签发重置链接。

▶ 管理员接口
/admin/users只返回账号元数据；PATCH状态不能停用唯一管理员。停用时撤销登录与媒体grant，并禁止新任务／未提交任务继续执行；已受理任务仍核对真实终态，不删除数据。若提供方尚未取到参考图，撤销grant可能导致该任务失败，不能保证停用后仍完成生成。
/admin/invitations创建和列表、/revoke撤销；/admin/users/{id}/password-resets签发重置。完整链接仅在签发响应返回，后续列表不再显示；数据库只保存凭据校验值。
邀请／重置链接优先把token放网页fragment，由前端读取、清除地址栏后以POST体校验／消费，避免进入服务器路径日志；页面不得加载会接收该内容的第三方脚本。
这两个签发操作不自动重试。响应丢失时先查列表／撤销再签发；重新签发重置使该用户旧重置凭据失效。不能为了重放响应把明文token永久保存在幂等账本中。
/admin/tasks只给故障概况，不含prompt、聊天正文、文件路径、下载链接或原始提供方响应；管理员不能借普通/tasks/{id}越权读取他人内容。
/admin/generation-policy只管理本地暂停开关，不能配置密钥、任意模型、收费回退或业务额度。关闭后拒绝新入队，并暂停尚未提交的同类工作；已被提供方受理的仍续查／保存。恢复本地开关不覆盖提供方限额与免费状态检查；worker在实际提交之前必须再次核对这些条件，不能只在API入队时检查。
核验方式字段是管理员操作记录，不代替实际人工核验；这些接口不向Gemini的MCP工具开放。

【四、幂等与并发】

必须提供Idempotency-Key的操作详见路由索引：新建对话、提交消息、上传图片、确认参考版本、创建媒体任务、保存文案作品／版本。
键为8–128位ASCII字母、数字、点、冒号、下划线或短横线；前端每个明确用户动作生成新键，网络重试复用原键。

服务端顺序：身份／CSRF／归属验证 → 幂等查询 → 字段和状态校验 → 同一事务保存资源与幂等关联。
范围为owner＋HTTP方法＋规范化资源路径＋键；同键同请求返回原资源和原成功状态码，并标记Idempotency-Replayed: true；同键不同语义请求409 IDEMPOTENCY_CONFLICT。
摘要基于规范化JSON语义字段，不能依赖JSON键顺序；上传摘要基于文件字节和字段，不包含随机multipart boundary。未提交的失败校验不占用成功记录。
MessageCreate额外有client_message_id用于前端乐观渲染，(conversation_id,client_message_id)唯一。重放即使换了HTTP键也不能产生第二轮；同ID不同内容仍409。
幂等关系至少在资源可用期间保存；业务删除后用墓碑避免同键复活资源，返回410 RESOURCE_GONE。重放不能绕过新发生的账号停用或资源删除。
上传和文件写入需临时区、稳定摘要及最终原子关联，不能因为重传复制出两个作品。

同一对话run串行执行，允许后续消息排队；不同对话独立调度，不设每日业务用量上限。队列过载429 QUEUE_BACKPRESSURE并给Retry-After，避免打满机器，这不是用户日配额。
修改现有作品时target_artifact_id与base_version_id成对提交；创建时校验base仍为当前版本，否则409 VERSION_CONFLICT。该作品已有非终态修改任务则409 ARTIFACT_BUSY，避免两个结果竞争覆盖当前版本。
图片／视频均追加版本而非覆盖；每个任务最多关联一个输出版本，下载恢复不得再追加相同版本。

【五、独立对话、消息与轮次】

POST /conversations只创建应用会话，不消费模型请求；首次消息才创建新的CLI。前端不传Google会话ID，也不传伪造的assistant／system历史。
同用户每次新建对话与其他用户一样，得到独立上下文；标题相同也不复用。打开旧对话时服务端按绑定关系恢复，不使用全局--continue。
POST /conversations/{id}/messages先保存用户消息与run，再202返回；模型不可用或排队未开始时仍如实显示状态，不回伪造回复。
content与attachment_version_ids不能同时为空，纯空白无附件拒绝422。引用图片可以是用户明确选择的自有旧版本，但这只授权该版本，不授权自动读取另一段聊天。
generation_permission设计默认requested_only：只执行用户明确要求的、通过后端校验的创作动作；discuss_only禁止该轮创建媒体。模型的解释不是无限执行许可；意图不清时澄清，不因关键词“视频”自动执行。
selected_version_id仅为选中目标，不等于图片确认事实；reference_confirmation_id须属于同用户、同对话和同版本，并检查未失效。

run与task分开：run是一轮对话协调，task是真实媒体任务。一个run可有多个task；run结束后，视频仍可能排队／生成，前端继续订阅task事件。
GET /runs/{id}或对话/runs列表可恢复队列、失败和待核对状态；GET消息返回稳定seq与状态，不能仅依赖浏览器localStorage。
消息的artifact_version_ids记录其关联的生成／保存作品，attachment_version_ids记录用户输入附件。task完成时将结果绑定到对应助手消息并发布message.updated，即使run早已结束也要补齐关联；刷新后不能只恢复文字却找不到作品卡片。
POST /runs/{id}/cancel返回202；queued可直接canceled，running先stopping。它停止当前轮次继续交付内容／新增动作，不承诺已受理媒体取消或提供方停止计费。
取消与工具创建要做状态竞争校验：run进入stopping后不接受新的工具任务，已经提交数据库的任务不丢弃。CLI终止的安全实现必须实测，不能全局pkill agy或影响其他会话。
取消完成之前以及意外断线时保留已输出消息；无法确定执行结果标记interrupted或needs_reconciliation，不自动重放整轮。
删除对话前要求没有未结束run或非终态task，否则409 RESOURCE_IN_USE。删除网站聊天不删除作品，也不保证立即删除提供方已留存的交互数据；CLI本地会话清理应使用经过验证的官方能力。

▶ 示例 schema：ConversationCreate
```json
{"title":"香水创作测试"}
```

▶ 示例 schema：MessageCreate
```json
{
  "client_message_id":"33333333-3333-4333-8333-333333333333",
  "content":"请根据已确认图片制作视频，固定镜头，让丝带轻轻飘动。",
  "selected_version_id":"77777777-7777-4777-8777-777777777777",
  "reference_confirmation_id":"88888888-8888-4888-8888-888888888888",
  "generation_permission":"requested_only"
}
```

▶ 示例 schema：MessageAccepted
```json
{
  "message":{
    "id":"33333333-3333-4333-8333-333333333333",
    "conversation_id":"11111111-1111-4111-8111-111111111111",
    "seq":1,
    "role":"user",
    "content":"请根据已确认图片制作视频，固定镜头，让丝带轻轻飘动。",
    "status":"completed",
    "run_id":"44444444-4444-4444-8444-444444444444",
    "client_message_id":"33333333-3333-4333-8333-333333333333",
    "attachment_version_ids":[],
    "selected_version_id":"77777777-7777-4777-8777-777777777777",
    "artifact_version_ids":[],
    "created_at":"2026-09-08T08:00:00Z",
    "updated_at":"2026-09-08T08:00:00Z"
  },
  "run":{
    "id":"44444444-4444-4444-8444-444444444444",
    "conversation_id":"11111111-1111-4111-8111-111111111111",
    "user_message_id":"33333333-3333-4333-8333-333333333333",
    "assistant_message_id":null,
    "status":"queued",
    "task_ids":[],
    "error":null,
    "created_at":"2026-09-08T08:00:00Z",
    "updated_at":"2026-09-08T08:00:00Z"
  }
}
```
用户消息status=completed只表示其消息已保存，run仍queued，不表示生成完成。

【六、图片确认、上传与版本】

POST /uploads使用multipart/form-data：file必填，conversation_id可选；仅接受可解码JPEG、PNG、WebP。服务端验证MIME、实际文件头、字节数、像素数和动画格式，不信任文件名或Content-Type。
响应201 ArtifactCreated，包含作品与第一个版本。用户文件名不作为磁盘路径；拒绝SVG／HTML等主动内容，首版不接收任意视频上传冒充图片输入。
安全限制由/capabilities返回。契约外层限定消息16000字符、最多8个附件、单任务最多5个参考图、分页100；实际部署限制可更低，超限返回明确错误。这些是单次请求保护，不是每日业务配额。
同用户明确从作品库选旧版本可以引用；未明确选择时Gemini只能看到当前对话已授权版本。

POST /conversations/{id}/reference-confirmations表示用户明确“用这张做视频”；请求version_id＋purpose=video_first_frame，返回reference_confirmation_id。
确认绑定owner、conversation、不可变version和用途。修改图片不会改变原确认指向，用户选择新版需要新确认。确认所指图片删除、撤销访问或归属不符即失效；不把确认当作可访问其他素材的令牌。
自然语言明确选图可由服务端记录等效确认及原始消息证据，但模型或普通MCP工具不能调用该确认接口自我批准。

▶ 示例 schema：ReferenceConfirmationCreate
```json
{"version_id":"77777777-7777-4777-8777-777777777777","purpose":"video_first_frame"}
```

GET /artifacts、/artifacts/{id}、/versions和单版本接口用于作品库、历史和媒体卡片。版本元数据返回实际MIME、字节数、SHA256、像素、时长与帧率，以及execution_engine，保证刷新后仍可区分AI视频与本地运镜；上传或直接保存的文案该字段为null，不虚构生成来源。不返回主机路径、供应商URL或授权凭据。
POST /artifacts只用于将文案保存为text作品，不再次调用模型；POST /artifacts/{id}/text-versions保存文案修改，要求base_version_id仍是当前版本。图片／视频的新版本由媒体任务生成，不通过通用文件写入接口替代。

【七、真实媒体任务】

POST /tasks接收有kind判别字段的三类请求；每task产生一份媒体，多方案创建多个task。所有入口复用同一校验、持久化、worker和作品服务。

▶ 生图
kind=image；必填prompt、aspect_ratio、size_tier、conversation_id。可选reference_version_ids必须为自有且本次明确授权的图片版本；参考编辑还需/capabilities声明已开启。
第一版图像尺寸档位1K／2K／3K／4K，画幅以契约枚举和实际能力接口交集为准；接口能力不保证参考图编辑已经通过效果验收。

▶ 示例 schema：ImageTaskCreate
```json
{
  "conversation_id":"11111111-1111-4111-8111-111111111111",
  "kind":"image",
  "prompt":"冷白背景的虚构透明香水瓶，银色瓶盖与淡蓝丝带，无品牌文字。",
  "aspect_ratio":"9:16",
  "size_tier":"1K",
  "reference_version_ids":[]
}
```

▶ AI视频
kind=ai_video；秒数为4–12的整数，size_tier固定720P，第一版画幅9:16或16:9。
mode=keyframe必须带本对话有效reference_confirmation_id；mode=text必须不带该字段，直接接口提交本身是明确文生视频动作。
通过Gemini的text视频工具调用仍须检查原用户明确要求直接文生视频，不能让模型借text模式绕过默认的先图确认流程。
不接收first_frame URL、images URL、视频路径或供应商key；后端按确认版本生成受限取图地址。

▶ 示例 schema：VideoTaskCreate
```json
{
  "conversation_id":"11111111-1111-4111-8111-111111111111",
  "kind":"ai_video",
  "mode":"keyframe",
  "reference_confirmation_id":"88888888-8888-4888-8888-888888888888",
  "prompt":"保持参考图瓶体和文字不变，固定镜头，仅让丝带末端轻轻摆动。",
  "seconds":4,
  "size_tier":"720P",
  "aspect_ratio":"9:16"
}
```

▶ 本地运镜
kind=local_motion；使用image_version_id，允许dolly_in／pan_left／pan_right／dynamic_float，第一版时长1–30秒。明确标记local-ffmpeg，不冒充AI视频，不开放任意命令或FFmpeg参数字符串。

▶ 示例 schema：LocalMotionTaskCreate
```json
{
  "conversation_id":"11111111-1111-4111-8111-111111111111",
  "kind":"local_motion",
  "image_version_id":"77777777-7777-4777-8777-777777777777",
  "motion_type":"dolly_in",
  "seconds":4,
  "aspect_ratio":"9:16"
}
```

▶ 重新生成与版本更新
不提供含糊的“失败就重新提交”接口。用户明确重新生成时POST新任务＋新幂等键，可带regenerate_from_task_id作为溯源；仍需完整参数、归属和引用确认校验。
希望追加到同一作品时，成对提供target_artifact_id和base_version_id；图像编辑的新版本绑定旧图，视频新版本可记录旧视频为父版本，但旧视频不因此成为Flash的视频输入。
前端继承已明确的参数，生成接口不凭默认猜测缺失的关键参数；没有值时模型或界面先补齐。

▶ 状态与恢复
queued：已持久保存；submitting：已记录提交意图；running：提供方已受理；saving：完成后下载／验证；completed：文件保存、版本和事件事务写入。
failed、canceled为终态；submission_unknown和needs_reconciliation不是可以盲目重试的失败。API给出can_cancel／can_recover供展示，但操作时仍再次检查，状态竞争可能返回409。
POST /tasks/{id}/cancel只允许queued任务，与worker领取原子互斥；已经submitting或受理的任务409 TASK_NOT_CANCELABLE。对已canceled重复请求返回当前状态。
POST /tasks/{id}/recover只请求续查或重新下载同一结果，202；不新建任务、不重新生成。没有任务ID／结果线索的未知提交409 RECONCILIATION_REQUIRED。
worker保留提供方任务ID与结果线索，仅由后端读取；崩溃后以稳定任务及目标文件名核对，不能因为文件已落盘但事务未提交就再次生成。文件与数据库间不能承诺跨系统严格exactly-once。
用户关闭页面不取消任务，提供方失败也不删除已确认输入图；没有业务额度扣减／退款逻辑。

【八、事件流与断线恢复】

GET /conversations/{id}/events使用登录Cookie，无URL登录token；它只订阅，不触发模型或生成。
建议前端使用fetch流读取，便于处理开始流前的401／410 Problem；若使用EventSource，错误后补查身份／快照，不无限盲目重连。
成功响应text/event-stream、Cache-Control: private, no-store、X-Accel-Buffering: no。代理不得缓存／聚合整段输出，实施时验证Vercel与隧道行为。

事件：message.created、message.delta、message.updated、run.updated、task.updated、artifact.ready、conversation.updated；各自data完整schema见OpenAPI x-event-schemas。
每条持久事件带id及JSON中的event_id、conversation_id、type、occurred_at、data；id在该对话内单调增长。序号断档可由其他已过滤事件造成，但同一资源内容的delta必须按chunk_index连续处理。
message.delta使用chunk_index＋delta，不用含糊的字节偏移；客户端按Unicode文本追加，最终message.updated用完整内容替换，避免重复拼接。

SSE帧示例（虚构；heartbeat注释用于保活，run.updated的数据为完整事件）：
```text
: heartbeat

id: 12
event: run.updated
data: {"event_id":"12","conversation_id":"11111111-1111-4111-8111-111111111111","type":"run.updated","occurred_at":"2026-09-08T08:00:00Z","data":{"id":"44444444-4444-4444-8444-444444444444","conversation_id":"11111111-1111-4111-8111-111111111111","user_message_id":"33333333-3333-4333-8333-333333333333","assistant_message_id":null,"status":"queued","task_ids":[],"error":null,"created_at":"2026-09-08T08:00:00Z","updated_at":"2026-09-08T08:00:00Z"}}

```

▶ 一致性恢复流程
1. GET /conversations/{id}/snapshot取得同一数据库读事务中的最近50条消息、非终态run／task、必要版本和last_event_id。必要版本包含消息附件、选中目标及artifact_version_ids所引用的可用版本；已删除的版本不返回文件信息，前端按缺失引用显示不可用，不丢弃其他作品。
2. 订阅/events?after_event_id=该值，服务器先重放严格大于该值的持久事件，再转实时流。不能先注册监听而漏掉读取期间发生的事件。
3. 连接重试可发送Last-Event-ID；若同时带query，以header为准。客户端按event_id去重，不重复应用快照已包含的增量。
4. 游标高于当前最大值返回400 INVALID_EVENT_CURSOR；早于事件保留边界返回410 EVENT_CURSOR_EXPIRED，重新获取快照。不得把其他会话游标当成访问凭据。
5. 事件保留策略是派生重放日志的运行参数，不是自动删除用户消息／作品；即使事件过期仍可从持久资源快照恢复。
6. 未给游标时从连接建立时的当前最大事件序号开始，只看之后的变化；恢复历史必须先取快照，不依赖订阅重放全部历史。
7. 已开始返回200后若登录撤销、对话删除或服务重启，用不带持久id的control事件提示并关闭，不能再改HTTP状态。下次请求重新鉴权；注销／停用后及时终止旧流，不能继续泄露数据。
8. CLI原始日志、工具完整参数、思考过程、凭据、宿主路径和其他会话ID不得直接透传给浏览器。业务事件来自经过脱敏的应用状态与持久事件表。

【九、下载、供应商取图与删除】

▶ 用户读取作品
GET /artifacts/{artifact_id}/versions/{version_id}/content返回实际MIME；download=true时使用安全的Content-Disposition附件名称，不能拼接未校验文件名造成头注入。
同一路径HEAD只返回头，不返回响应体；先鉴权再提供Content-Length。HEAD忽略Range，不能借它绕过访问控制。
GET支持单区间bytes=a-b、bytes=a-、bytes=-n；有效范围206并返回Content-Range／Content-Length，无范围200。多区间或不可满足范围416并给Content-Range: bytes */总字节数。
不得接受任意URL或路径参数。文件内容、标题、错误与Range边界不能让未授权用户探测文件大小；归属错误先404，不先计算范围。
版本content_url是受保护相对路径，不是无需登录的永久公开链接。已被用户下载或供应商取得的副本不可能靠网站删除即时撤回。

▶ 供应商读取参考图
GET／HEAD /media-grants/{grant_id}/content?token=…是唯一不要求网站登录、但必须具备短期媒体授权的文件通路。
grant由后端为具体任务和图片版本签发，保存高熵token的校验值、用途、到期与撤销状态；不允许前端／模型指定任意文件生成grant。
在任务即将提交时签发，而非长时间排队之前；到期上限和续期必须受任务生命周期约束。允许提供方在有效期内重复HEAD／GET，不把一次读取后失效误当作安全要求而破坏下载重试。
只能读取该参考图片，无目录列表、任意路径、视频读取或其他版本权限；错误／到期／撤销统一404，且不回显token。任务结束、素材删除或访问撤销后失效。
原始签名URL仅交给目标媒体提供方，不写入浏览器响应、普通日志或管理员故障列表。代理、应用、追踪系统均须对token查询参数及完整URL脱敏。
这是有限媒体交付授权，不是导出Google／Agnes账号凭据。

▶ 删除
DELETE /artifacts/{id}立即隐藏作品和全部版本、撤销下载／取图权限，并202返回DeletionReceipt。物理清理目标时间由当前策略计算，建议24小时，发布前复核。
同用户重复删除返回原清理回执；他人对象统一404。任何非终态任务仍引用输入或目标作品时409 RESOURCE_IN_USE，提示先结束该任务，不偷偷删除仍被使用的文件。
删除图片不连带删除已经完成的派生视频；删除对话不默认删作品。当前不备份，不在响应中虚构备份清除时间或恢复能力。

【十、错误码与客户端处理】

统一Problem包含type、title、status、code、detail、request_id、retryable，可选errors字段列表只含field／code／message，不含原始输入。
稳定错误分类：
• 400 TOKEN_INVALID：邀请／重置无效，不区分不存在、过期或已使用；请求方回到正确流程。
• 400 INVALID_EVENT_CURSOR：游标格式／范围错误；重新取快照。
• 401 AUTH_REQUIRED／INVALID_CREDENTIALS：登录已失效或账号密码错误；不自动切换提供方授权。
• 403 CSRF_INVALID／FORBIDDEN／ACCOUNT_DISABLED：安全上下文、权限或停用问题；不盲目重复提交。
• 404 RESOURCE_NOT_FOUND：不存在或不可访问，前端不猜测是否属于他人。
• 409 IDEMPOTENCY_CONFLICT／USERNAME_UNAVAILABLE／VERSION_CONFLICT／ARTIFACT_BUSY：修改冲突，刷新状态或由用户另起明确动作，不换键自动重试生成。
• 409 REFERENCE_CONFIRMATION_REQUIRED：缺少有效选图确认；先让用户确认，模型不能代为确认。
• 409 TASK_NOT_CANCELABLE／RUN_ALREADY_TERMINAL／RESOURCE_IN_USE：状态不允许操作，刷新真实状态。
• 409 RECONCILIATION_REQUIRED：提交是否受理尚未确定，不自动再生成。
• 410 EVENT_CURSOR_EXPIRED／RESOURCE_GONE：分别重新取快照或停止复活已删资源。
• 413 UPLOAD_TOO_LARGE、415 UNSUPPORTED_MEDIA_TYPE、422 VALIDATION_ERROR／IMAGE_DECODE_FAILED：调整内容，不能静默丢字段或截断。
• 429 RATE_LIMITED／QUEUE_BACKPRESSURE：安全限流或排队保护，遵守Retry-After；不是本地业务日配额。
• 503 PROVIDER_UNAVAILABLE／PROVIDER_LIMIT_REACHED／FREE_ACCESS_UNCONFIRMED／GENERATION_PAUSED：明确提示暂停原因，不切收费模型、不自动使用credits。
• 507 STORAGE_UNAVAILABLE：停止新增，已有作品保持可访问；不自动清理旧作品。
• 500 INTERNAL_ERROR：返回追踪ID，不泄露堆栈；是否可重试由retryable及幂等语义共同决定。
参数结构缺失／类型错误首先由422处理；例如keyframe缺必填confirmation字段为422，字段存在但无有效确认事实为409。不存在或不属于当前用户的版本仍优先404。
提供方任务已经被本地接受之后出现的错误记录在run／task资源和事件中；不能将已经返回202的HTTP响应倒改成失败状态，也不能仅凭202称最终成功。

▶ 示例 schema：Problem
```json
{
  "type":"about:blank",
  "title":"资源不可见",
  "status":404,
  "code":"RESOURCE_NOT_FOUND",
  "detail":"资源不存在或当前用户不可访问。",
  "request_id":"22222222-2222-4222-8222-222222222222",
  "retryable":false
}
```

【十一、内部MCP与FastAPI映射】

MCP只在受控CLI环境中注册，不提供给浏览器一个匿名的通用执行端点。
• submit_image → ImageTaskCreate＋可信owner／conversation／run上下文 → 与POST /tasks相同的任务服务。
• submit_video → VideoTaskCreate；必须检查用户意图、模式与确认事实，不接收模型自报用户ID或签名URL。
• submit_local_motion → LocalMotionTaskCreate。
• read_task／list_artifacts → 仅当前对话与显式授权版本；不能等同于用户网页可浏览的全部个人库。
内部幂等键由服务端绑定run与稳定工具动作标识，CLI重连和重复工具事件不新建任务；不能相信模型传来的任意幂等键绕过重复保护。
Cookie／CSRF用于网站边界，内部工具使用服务端签发的最小会话权限；不把网站Cookie、供应商key或Google token塞进模型提示词。
真实业务执行读取校验后的工具请求或structured_output；不从混杂说明文字的response中随便抓一段JSON执行。stream-json累计统计和原始工具输出先由适配器消化，不能直通前端。

【十二、FastAPI落地要求与旧接口迁移】

建议按auth、conversations、runs、tasks、artifacts、admin、media_delivery、health拆APIRouter；身份、CSRF、角色及归属检查使用统一依赖／服务层，不散落在各端点自行实现一套。
Pydantic请求模型禁止额外字段；动态账号策略、版本归属、确认事实和状态竞争需要运行时校验，OpenAPI schema不能代替它们。
统一处理RequestValidationError、HTTP错误与未捕获异常，生成Problem而非回显原输入。同步SDK／磁盘工作不堵塞事件循环；长生成工作交独立worker，不依赖请求或BackgroundTasks存活。
待实际服务实现后，以FastAPI app.openapi()和本契约做差异检查；不能因为本文件验证通过就称FastAPI运行正确。
/docs、/redoc、/openapi.json是将来服务的文档入口，测试阶段可启用；正式对外应限制调试文档访问或关闭交互执行，不能在文档示例中保存真实Cookie／token。

旧→新对照：
• /api/chat → 对话/messages创建run＋/events订阅；不再以stream标志决定是否生成媒体。
• /api/agy/prompt → 受归属保护的run；不保留匿名通用宿主CLI代理。
• /api/generate-image → POST /api/v1/tasks kind=image。
• /api/motion-video/create、/api/wan-video/create → 明确local_motion或ai_video，不能用旧名称混称两者。
• /api/upload、/api/upload-binary → 受保护/uploads＋版本。
• /uploads/和/clips/静态文件 → 受保护版本content；供应商另用短期media-grants。
• 旧/api/test-provider不作为普通用户配置任意密钥／URL的入口；新版仅返回安全的capabilities。
• 直播、语音、复杂剪辑等旧路由未纳入v1，不在本轮删除或部署兼容别名。旧接口若未来仍公开，必须另行纳入鉴权范围，不能成为绕过新版安全的后门。

【十三、校验与下一步】

生成契约和索引：
```bash
python3 docs/api/build_openapi.py
```
离线schema／路由／示例检查（需要jsonschema，见requirements-docs.txt）：
```bash
python3 docs/api/test_contract.py
```
完整OpenAPI结构还可用官方元schema校验：
https://spec.openapis.org/oas/3.1/schema/2022-10-07
```bash
python3 docs/api/test_contract.py --openapi-meta /path/to/openapi-3.1-meta.json
```
这三个命令均不调用Google／Agnes，不生成媒体、不启动网站。示例标记“schema：名称”的JSON会自动验证，SSE示例也会按事件schema检查。

实现阶段必须另测：跨用户／跨会话访问、CSRF、邀请码原子消费、幂等竞争、SSE快照／重放、授权撤销中断流、媒体Range／HEAD、短期grant、提交未知、worker重启与下载恢复。元schema无法证明这些行为。
首版文档完成后再拆具体实施任务；生产部署、数据迁移、客户可见旧功能下架等仍单独批准。
