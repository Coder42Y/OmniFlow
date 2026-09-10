【03-artifacts：素材、不可变版本与交付】

▶ 当前进度
• 本轮 coder 阶段实现与测试通过，可交独立审查。最终固定命令 `.venv/bin/python -m pytest backend/tests -q` 为 `367 passed in 73.93s`。进入阶段的实际基线为 `282 passed in 49.16s`，保留全部既有测试，新增 85 项素材／交付／故障注入与真实回环冒烟测试；不是整个工作台或生产发布完成。
• 已完整阅读指定实施计划、Spec v1.0、FastAPI 接口文档、全部已有阶段记录，读取本阶段完整操作与递归引用 schema，并实际核对已有账号、对话、迁移、安全边界与测试；没有把上轮泛化 process_or_model_error 当作已完成或安全失败的证明。
• 实现位于当前 worktree 的 `backend/`，本地 `.venv` 新增 Pillow 12.3.0、python-multipart 0.0.32，锁定在 `backend/uv.lock`。未改原站源码、部署配置、控制器、实施计划或控制状态；未读 `.env`／真实凭据、未调用真实供应商、未执行 commit／push／reset／clean／merge。
• 本报告先保存首轮已验证事实，再补齐最终复测。媒体真实回环 HTTP 冒烟已独立运行通过，并加入固定 pytest 门槛；三个冒烟脚本均自行回收本轮创建的子进程，不涉及其他服务或 tmux 会话。

▶ 已实现
• 追加迁移 5：artifacts、不可变 artifact_versions、明确对话版本关联、reference_confirmations、独立素材幂等账本、任务媒体访问范围／输入与目标占用、短期 media_grants。版本元数据禁止 UPDATE／DELETE，任务输出版本唯一；已有迁移 1–4 保持不变，v4→v5 合成升级保留账号和全部迁移历史。
• 图片 multipart 上传：身份／CSRF 在解析前检查，总请求／单文件／表单字段／头部有界；不依赖 Content-Length。仅允许真实解码 JPEG、PNG、WebP；核对 MIME 与实际格式，限制字节、解码像素，拒绝动画、多帧、坏图、主动内容和任意视频。用户文件名不参与路径或下载响应头。
• 文案保存、追加版本、作品与版本分页、参考图片明确确认、必要版本快照、artifact.ready 持久事件、受保护 GET／HEAD／单区间 Range、短期供应商 GET／HEAD、能力与安全上限查询。当前生成能力如实 unavailable，模型仍固定 gemini-3.8-flash-low，没有把上传或保存文案标成 AI 生成。
• 文件临时区在私有 media 目录，0600 文件、UUID 名、fsync 与不覆盖的原子链接；文件解码与临时写入在 SQLite 写锁外，最终版本／事件／幂等事务关联。并发重传只保留一个作品／版本／文件；事务失败留下的私有孤立文件不对外可见，由显式维护核对清理，不声称文件与数据库跨系统 exactly-once。
• 文案 base_version_id 必须仍为当前版本；旧版本不覆盖。输入与选中版本逐项校验归属，确认绑定 owner／对话／精确图片版本；模型没有确认或任意签发 grant 的入口。新图片版本不改变旧确认／授权指向；后排消息的引用不能提前进入前轮内部可见版本集。
• 下载先身份与归属后判断文件大小和 Range；HEAD 忽略 Range 且实际 ASGI 不交付响应体，合法单区间 206，多区间／越界 416 并返回 Content-Range。错误脱敏、no-store、nosniff、安全文件名；文件无符号链接，逐块重新核对权限，客户端提前断开也关闭文件句柄。
• grant 只由内部可信任务媒体范围签发，绑定具体输入图片、任务、用户授权代数、用途和期限；排队范围未激活不能签发，续期不能延长原任务授权窗口。只保存 token 校验值，不进入事件、幂等、普通 API 或日志。错误／到期／撤销／任务结束／账号停用或重置统一 404；重新启用或新建服务不复活旧 grant。
• 删除作品立即隐藏全部版本并撤销在线读取，重复删除返回原回执，原幂等键返回 410；有未释放输入或目标占用则 409。删除对话不删独立作品。`purge-artifacts` 显式本地命令清理到期删除文件，保留墓碑；仅清理已核实孤立且超过一小时的自建临时文件，flock 防止误删正在上传的文件，不以低磁盘为由清理可用作品。

▶ 阶段边界与必须接续
• task_media_scopes 是任务服务的媒体占用／授权范围，不是影子任务队列或已实现的三类媒体任务。当前没有公开任务创建、worker 或真实供应商调用；本阶段通过合成内部任务验证这些事务和生命周期边界。
• 阶段 04 必须在真实任务创建事务中调用 TaskMediaService.reserve（包括无输入任务），在实际提交前通过所有费用／权限／状态闸门后调用 activate／issue，并在真实终态事务调用 close。未知提交和仍在保存结果的任务不能提前 close。该真实任务接线尚未实现，不能把合成调用说成完整视频闭环。
• 阶段 04 接入任务状态、租约、恢复、source_task_id 唯一关联以及助手消息 artifact_version_ids／message.updated；阶段 05 工具网关复用受控版本集，不能把网页用户作品列表当作模型全库权限。
• 图片原始字节保留，不承诺清除原图 EXIF 等用户自带元数据。已下载或已送达供应商的副本无法撤回；文件权限与路径检查不等于宿主沙箱；不实施备份或灾损恢复。
• 生产发布、生产数据迁移、真实 agy／Agnes、真实费用权益和完整宿主隔离验证均「未完成」。前端、浏览器 HTTPS、代理长连接、Mac／iOS 播放尚未验证；npm 测试／构建及 E2E 命令仍分别从阶段 06／07 提供。

▶ 已执行测试
```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python docs/api/test_contract.py
```
• 首轮最终后端 `363 passed in 70.93s`；Ruff `All checks passed!`；格式 `47 files already formatted`。
• 契约：40 路径／51 操作／64 schema／525 引用，22 契约示例／8 文档 JSON／1 SSE 帧／22 类拒绝用例通过。未提供官方 OpenAPI 元 schema，不声称全量官方校验；本阶段另对实际素材操作、安全声明、请求字段、成功响应与事件做契约子集检查。
• 单独素材 API `47 passed in 11.47s`；交付／占用／存储故障 `18 passed in 4.71s`；额外不变量 `16 passed in 4.16s`。全部只用合成图片、临时 SQLite、假任务范围及离线客户端。
• 早期集成全量曾 `2 failed, 280 passed`：基础测试把 capabilities 永久当作未实现路由，账号 OpenAPI 测试把三种安全机制当作永久全量。本阶段已实现该路由与 MediaGrantToken，保留原测试用例并改为验证匿名 capabilities 必须 401，安全机制集合精确增加唯一短期授权；没有放宽原路由、安全断言或修改设计契约。
• 新文件首次 Ruff 导入／长行／格式问题已实际修复，未屏蔽规则或降低 warnings-as-errors。当前新增业务测试没有通过删除测试或改预期伪造通过。

▶ 最终实际复测与构建
全部在当前 worktree 前台、有界执行，临时数据与媒体仅为合成内容。
```bash
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python docs/api/test_contract.py
.venv/bin/python backend/tools/smoke_artifacts.py
.venv/bin/python backend/tools/smoke_conversations.py
.venv/bin/python backend/tools/smoke_health.py
```
• 最终：`367 passed in 73.93s`；`All checks passed!`；`48 files already formatted`。首轮 363 项为前面的历史证据，随后增加 3 项错误 grant ID 的统一 404 检查及 1 项回环媒体冒烟，没有删除原有用例。
• 媒体冒烟实际启动两次独立回环服务并验证重启恢复：图片上传、重传同响应、真实 HEAD 无响应体、单区间 206／多区间 416、未登录 HEAD 拒绝、匿名供应商精确短期授权、任务占用拒绝删除、范围结束撤销授权、删除后 410 幂等墓碑，以及本地 purge-artifacts 实际移除已删除文件。服务访问日志关闭且不含合成密码／Cookie／grant token。结束后仅本脚本创建的进程与临时目录被回收。
• 健康与对话冒烟独立复跑均通过；固定 pytest 也包含它们及新媒体冒烟。手工发送合成 Cookie 到回环 HTTP 只验证服务行为，不降低 Secure，也不冒充浏览器 HTTPS 或真实提供方验收。
• 并发专项通过前台 Python `subprocess.run(..., check=True, timeout=60)` 有界重复 5 次：
```bash
.venv/bin/python -m pytest backend/tests/test_artifacts.py \
  backend/tests/test_media_delivery.py -q -k 'concurrent or reservation_race'
```
每次 `3 passed, 62 deselected`，耗时 0.94／0.70／0.71／0.70／0.71 秒；覆盖 6 路上传同键、文案基版本并发修改，以及删除与输入占用竞争，不靠后台循环或真实网络。

```bash
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
uv sync --project backend --locked --group dev \
  --python "$(command -v python3)" --python-preference only-system
UV_CACHE_DIR="$PWD/backend/var/uv-cache" uv pip check --python .venv/bin/python
UV_CACHE_DIR="$PWD/backend/var/uv-cache" \
uv build --project backend --out-dir "$PWD/backend/var/dist" \
  --python "$(command -v python3)" --python-preference only-system
```
• 锁定同步成功，38 个锁定包／36 个安装包核对通过，依赖兼容；未改全局运行时。
• sdist 和 wheel 均构建成功。构建器提示缓存位于源码树内，随后实际用 tarfile／zipfile 检查归档：sdist 52 项、wheel 30 项，包含素材实现，不含 var／.venv／.env。产物位于 `backend/var/dist/`，不是生产部署。
```bash
git diff --check
git diff --exit-code -- server.py index.html requirements.txt deploy.sh \
  docker-compose.yml Dockerfile nginx.conf vercel.json start.sh manage.sh
```
• 空白和列明原站／部署文件差异检查通过，未切换原网站。没有修改 docs/api 契约、批准原型、实施计划或自动开发工具／控制状态。

▶ 本轮修改文件
新增：
• `backend/src/omniflow/artifact_models.py`
• `backend/src/omniflow/artifact_schema.py`
• `backend/src/omniflow/artifacts.py`
• `backend/src/omniflow/media_storage.py`
• `backend/src/omniflow/api/artifacts.py`
• `backend/tests/test_artifacts.py`
• `backend/tests/test_media_delivery.py`
• `backend/tests/test_artifact_invariants.py`
• `backend/tools/smoke_artifacts.py`
• `docs/progress/03-artifacts.md`

更新：
• `backend/src/omniflow/app.py`、`config.py`、`db.py`、`cli.py`
• `backend/src/omniflow/conversations.py`、`conversation_models.py`
• `backend/src/omniflow/middleware.py`、`problems.py`
• `backend/tests/test_app.py`、`test_auth.py`、`test_smoke.py`
• `backend/pyproject.toml`、`backend/uv.lock`、`backend/README.md`
本地 .venv、backend/var 缓存／测试／构建产物不属于源码交付；原有未提交文档、原型和代码均保留。

▶ 最后接续与未验证闸门
• 下一迁移从 6 追加，不能重写 1–5；本地安装、启动、维护及所有测试入口已更新 `backend/README.md`。
• 当前物理清理是已验证的显式命令，没有安装生产定时服务；阶段 04／部署审查须安排维护调用，24 小时是策略目标，不是假称已运行的线上定时保证。
• 自然语言自动识别选图与原消息证据的生产接线尚未实现；当前确认必须经过明确的用户接口。阶段 05 不得让模型自造 confirmed=true，也不能把普通网页全库列表作为工具可见范围。
• 完整任务生命周期／worker 接线及恢复仍由阶段 04 实施，真实 CLI／媒体提供方、免费权益／超额开关与宿主隔离由阶段 05 独立验证。生产发布、真实供应商验证均「未完成」，本阶段 pass 仅指上述素材离线实现与真实本机 HTTP 测试通过。

---
In brief
• What's happening：新版能保存图片和文案、保留各次修改，并保护下载；367 项检查通过。
• Reason：每份内容有独立归属，重复上传不多存一份，删除后旧链接不能继续取走。
• Impact：旧网站不变；真实生成、正式上线和浏览器实测尚未完成。
• Require Input：不需要 input，我继续按已批准阶段接续。
