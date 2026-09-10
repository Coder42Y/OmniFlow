【Gemini 主控决策验证记录】

▶ 范围
用户认可复用 agy 通路后，继续用已有登录做一次离线业务决策验证。目标是判断 Gemini 是否理解已确认的创作流程，不实现网页代理，不调用实际生图／视频服务，不修改正式网站。
调用 /usr/local/bin/agy，版本 1.1.22，请求型号 gemini-3.8-flash-low；low 仅为当前验证档位。

▶ 输入与实际结果
一次请求包含四个独立的虚构案例，不含用户真实素材或聊天：
1. 只咨询如何图片转视频，明确不要开始 → reply。没有提出生成任务。
2. 想生成横屏雨夜短片，但没有参考图 → generate_image，先出视觉确认稿，needs_image_confirmation_before_video=true，画幅 16:9。
3. 已确认 asset_demo_v1，要求四秒视频，固定镜头、丝带轻动 → generate_video，引用 asset_demo_v1，继承 9:16，时长 4 秒，不重复请求图片确认。
4. 有两张图、没有选中目标，只说“改这张” → clarify，reference_image_id=null，没有猜测目标。
四项业务断言全部通过。这里的 action 是模型提出的下一步建议，没有交给实际生成工具执行。

▶ 结构化输出与校验
通过 CLI --json-schema 指定严格输出对象。CLI 实际返回独立 structured_output 字段，内部仅含 decisions 数组。
本轮独立检查了对象字段集合、四例 ID 唯一性、动作枚举、参数类型、引用图、时长、画幅以及确认规则，均通过。
发现 response 字段包含 Markdown 代码块、额外文本以及 CLI 用于提交结构化结果的内部附加字段，不能直接将 response 当 JSON 解析，也不能从其中任意截取一段就执行动作。
后续适配应读取经过校验的 structured_output，或明确的受控工具事件；正文只负责展示。即使结构符合规范，仍需服务端检查用户权限、版本归属、额度和确认状态，不能把模型结果直接视为授权。
CLI 内部为结构化结果使用提交机制，不能把这次试验表述成“已经实现生图／视频工具调用”。

▶ 性能与开销（单样本）
• CLI status：SUCCESS；进程退出码 0。
• CLI 报告 duration_seconds 约 7.12 秒；含启动和上下文准备的总耗时约 20.93 秒。
• num_turns：2。
• input_tokens：35,770；output_tokens：943；cache_read_tokens：28,379；total_tokens：36,713。
这些是 CLI 返回的统计，不能直接作为账单或平均延迟。即使用户只给短需求，Coding Agent 自带较大上下文；正式接入需评估是否可收窄上下文与工具集，不能把当前冷启动表现当成理想实时聊天性能。

▶ 权限与限制
本轮使用 --mode plan、--sandbox，没有再同时传 --disable-slash-commands，避免上轮发现的参数冲突；stderr 为空。
这不等于完成完整沙箱审计，也不等于对任意用户提示词证明无越权。实际多用户工具权限、会话隔离与个人订阅服务许可仍需独立验证。
这里只测试了一批按明确规则构造的四个例子，不是生产准确率、不证明长对话稳定性或恶意输入安全性。
指定模型可被 agy 接受并返回结果，但最终 JSON 未提供独立的供应商实际模型证明，继续沿用先前模型核验边界。

▶ 原始证据
脚本：/home/kris/.local/share/omniflow-gemini-probe/probe-orchestrator.py
验证输出：/home/kris/.local/share/omniflow-gemini-probe/results/agy-orchestrator-validation.json
CLI 原始输出与日志保存在同一受限 results 目录，未写入仓库，不含真实用户创作资料。

▶ 对方案的结论
Gemini 作为主控理解需求、决定下一步、沿用已确认参数的方向获得初步实证支持。可继续设计受控工具协议与后端校验边界；不能据此宣布真实生成链路、登录体系、数据库和重构全部完成。
