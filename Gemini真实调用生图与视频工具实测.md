【Gemini 真实调用生图与视频工具实测】

▶ 结论与用户授权范围
用户明确要求测试能否调用生图和视频工具。本轮真实跑通：
指定 Gemini 3.8 Flash → agy MCP 工具调用 → 后端受限 worker → Agnes 媒体 API → 查询结果 → 下载文件 → Gemini 读取状态并报告。
不是只让模型返回任务建议，不是由助手根据模型正文代为手工调用，也不是本地图片平移／缩放冒充 AI 视频。
只提交一张图片和一条视频；生成成功，均有本地文件、工具审计、供应商响应及播放验证。未接入正式网站，未发布或提交业务代码。

▶ 调用环境与费用边界
• 入口：/usr/local/bin/agy，版本 1.1.22，沿用既有登录。
• 请求的主控型号：gemini-3.8-flash-low。仍沿用前文的模型核验边界：CLI 支持并接受该型号，未从模型自报名称判定实际路由。
• 生图：agnes-image-2.5-flash，n=1，size=1K，ratio=9:16。
• 视频：agnes-video-2.5-flash，n=1，mode=keyframe，seconds=4，size=720P，aspect_ratio=9:16。
• 调用前重新读取 Agnes 官方价格页和两个模型页，明确列明这两款 Flash 当前免费／限时免费；现有密钥的 /v1/models 列表也包含它们。
• 未启用付费模型、未开通计费或充值、未进行模型回退。但没有登录供应商账单后台核验最终扣款，不能把公开价格核对表述成完整账单审计。
• 使用虚构香水主题，不含真实用户人物或商业资料。视频使用此前测试的合成香水参考图，不是本轮尚未确认的新图；两种媒体是独立功能验证，不宣称完成了新图确认后的全流程。

▶ 工具与最小权限
临时 MCP 服务名称 omniflow-media-probe-5283，只提供三个工具：
1. omniflow_probe_image：最多创建本次唯一图片任务。
2. omniflow_probe_video：最多创建本次唯一视频任务，固定已约定的测试参考素材，不接受任意 URL、模型、时长或文件路径。
3. omniflow_probe_status：只读本次两个任务状态，单次最多等 15 秒。
本次独立调用凭证带一小时期限，供应商 API key 只由 worker 从现有配置读取，不传入 Gemini 或 MCP 工具参数。
服务端固定模型、数量、视频时长与档位；重复调用返回同一槽位，不重复提交；提交前保存意图，不确定提交不会重试 POST。
worker 独立启动，正常情况下父 CLI 退出不终止已启动任务。此架构处理意图已实现，但本轮没有另做强制杀进程可靠性测试。

▶ 首次真实调用的权限阻塞与处理
首次 CLI 返回 status=CANCELED，说明非交互模式不能询问 mcp 权限而自动拒绝。此时未创建任何媒体状态文件，也未提交媒体任务。
没有采用 --dangerously-skip-permissions。只在 agy 的 settings.json 临时添加了三个精确规则：
• mcp(omniflow-media-probe-5283/omniflow_probe_image)
• mcp(omniflow-media-probe-5283/omniflow_probe_video)
• mcp(omniflow-media-probe-5283/omniflow_probe_status)
之后第二次调用成功。没有对其他 MCP、终端或文件工具增加放行权限。
测试结束后已移除这三个规则、注销临时 MCP 服务，并撤销测试调用凭证。当前设置与测试前备份的 JSON 结构比较完全相同，原 MCP 配置中临时服务也已不存在。
这证明可以按具体工具授权，不代表整个 Coding Agent 已完成生产级隔离审计。

▶ 实际工具调用审计
已记录六次成功的真实 MCP 请求：
1. omniflow_probe_image
2. omniflow_probe_video
3. omniflow_probe_status
4. omniflow_probe_status
5. omniflow_probe_status
6. omniflow_probe_status
两种媒体的 submission_count 均为 1，无重复生成。主控读取到了 completed / saved 的后端结果后才报告成功。
成功的 CLI 调用总耗时约 90.71 秒，CLI 报告 duration_seconds 约 81.44 秒；输入 tokens 83,879，输出 tokens 4,318，cache_read_tokens 154,957。这是 CLI 原始聚合统计，不换算为账单，也不把 num_turns=1 等同于只有一次内部模型请求。

▶ 本地产物与实际规格
根目录：/home/kris/.local/share/omniflow-media-probe-5283/

图片：gemini-tool-image.png
• 状态 completed，实际 PNG 可解码。
• 736 × 1312，972,514 字节，任务约 13.57 秒。
• 画面是冷白背景、透明香水瓶、淡蓝液体与丝带，没有真实品牌文字。输出尺寸是供应商的原生档位结果，不是要求它交付任意精确像素。

视频：gemini-tool-video.mp4
• 状态 completed，供应商终态 completed，文件 330,105 字节，任务约 59.05 秒。
• ffprobe：H.264，704 × 1280，24 fps，107 帧，实际视频流及容器时长均为 4.458333 秒；另有 AAC 音频。
• 请求 seconds=4 / size=720P，但不能在产品上虚报为严格 4 秒或 720×1280。实际画幅也与严格 9:16 略有不同，需要后续输出标准化或供应商约束处理。
• 每秒抽帧可以看到丝带变化；也有画面尺度／位置轻微变化迹象。没有对镜头绝对固定、瓶体几何和标签逐帧保真做严格量化验收，因此不能称完全满足所有画面要求。
• 本轮未裁剪、变速、静音或重编码来掩盖供应商输出偏差，原始文件原样保留。

▶ 用户对样片的反馈
用户查看本轮结果后反馈“its been good”。记录为：本轮生图与视频样片的主观效果获得认可，可以作为后续效果参考，无需重复要求确认这批样片。
此认可不改变实测尺寸和时长差异，不等于所有未来输出均达标，也不是整体重构或正式发布授权。后续新生成的视觉确认稿仍按既定流程由用户确认。

▶ 已执行检查
• 7 项无网络单元测试全部通过：错误／过期调用凭证拦截；拒绝任意模型与路径参数；图片单槽位及固定参数；视频素材和时长限制；状态结果不暴露内部 URL 与请求；拒绝越界等待与路径注入。
• 图片用 Pillow 解码并验证；视频用 ffprobe 检查，并抽取联系图。
• 临时预览页面 HTTP 200，图片可读，视频 Range 请求返回 206，错误访问路径返回 404。
• Chrome 真正播放测试通过，播放进度超过 1 秒，未报告解码错误；390px 手机布局无横向溢出，页面无 JS 异常。
• git diff --check 通过。正式业务文件无新增改动。
这些检查不替代多用户权限、订阅使用许可、持久任务恢复与生产安全测试。

▶ 证据与预览
全部脚本、私有响应和素材保存在上述仓库外目录，包括：
• media_mcp.py、test_media_mcp.py、run_gemini_tools.py
• image-state.json、video-state.json、tool-audit.jsonl
• agy-media-summary.json、denied-agy-media-summary.json
• video-metadata.json、video-contact-sheet.jpg
• preview-browser-validation.json、preview-desktop.png、preview-mobile.png
浏览器测试代码：.superpowers/agy-media-probe/preview-smoke.cjs，已被 .superpowers 忽略规则排除。
预览只绑定服务器私网地址，使用随机访问路径、无目录列表，只展示本轮合成素材；约一小时后自动关闭，不删除图片／视频。实际带访问凭证的地址保存在仓库外 preview-info.json，仅在用户会话提供，不写入版本管理报告。

▶ 对下一步方案的意义
“Gemini 主控可以真正调生图／视频工具”已有端到端实证。下一步重点从能否调用转为：收窄完整 Agent 的上下文与权限、确认个人订阅的多用户许可、完善服务端身份／配额校验、持久任务与下载重试、素材确认流程，以及精确时长／画幅和画面质量验收。

▶ 本轮官方依据
• https://agnes-ai.cn/zh-Hans/docs/pricing
• https://agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash
• https://agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash
官方页面抓取文本保存在 .superpowers/agy-media-probe/，不包含任何授权信息。
