【agy 复用授权与 Gemini 文本实测】

▶ 结论
按用户建议检查并使用服务器现有 agy。通过 /usr/local/bin/agy 入口，无需重新登录，模型列表查询和一次明确请求 Gemini 3.8 Flash 的非交互文本调用成功。
不再把修复另一套 Gemini CLI 登录作为当前文本验证的前置条件。没有复制 Cookie、导出 OAuth 凭据或自行调用内部服务；使用的是现有 CLI 的 models / print 入口。多用户网站接入许可与运行隔离仍未验证，不能把本人调用成功直接当作可安全公开服务。

▶ 入口差异的实证
• /usr/local/bin/agy 是 562 字节的本地 shell 启动脚本，会先 unset SSH_CLIENT、SSH_CONNECTION、SSH_TTY，再 exec /usr/local/libexec/agy。
• 底层程序报告版本 1.1.22，支持 models、--print、--model、--output-format json / stream-json、--sandbox 等参数。
• 在当前 SSH 环境直接执行 /usr/local/libexec/agy models，返回“Please sign in”。
• 改用用户日常入口 /usr/local/bin/agy models，立即返回模型列表。
由此可确认入口及 SSH 环境会影响该程序识别现有登录；为什么内部做此选择没有继续反向分析。此结论仅针对 agy，不是对 Gemini CLI 反复授权问题的根因证明。
未修改全局 agy 启动脚本，未改变用户已有授权。

▶ 当前可见的目标模型
• gemini-3.8-flash-high
• gemini-3.8-flash-medium
• gemini-3.8-flash-low
列表还返回其他模型，但本轮不改用它们。
本次选择 low 仅用于小成本连接验证；后续正式服务默认档位尚未单独确认。

▶ 本轮真实文本测试
调用脚本：/home/kris/.local/share/omniflow-gemini-probe/probe-agy.py。
底层调用使用 /usr/local/bin/agy，指定 --model gemini-3.8-flash-low、JSON 输出、45 秒 print 超时、sandbox，并要求只返回固定测试文本、不调用工具、不读取文件。
CLI 输出：
• status：SUCCESS
• response：AGY_TEXT_OK
• num_turns：1
• 程序退出码：0
• CLI 报告本轮响应耗时约 3.05 秒；包含启动等开销的总耗时约 12.69 秒。
• 输入 tokens：30,130；输出 tokens：60；thinking tokens：54；总 tokens：30,190。这反映 CLI Agent 自带上下文开销，不能按用户提示词只有一句就宣称调用成本很低。
• 模型列表支持该型号，执行日志多次记录 Resolving model gemini-3.8-flash-low，未发现另一个模型名；最终 JSON 不包含供应商签署的实际模型标识，因此这是“指定该型号并成功调用”的验证，不是独立证明服务端绝不替换路由。
未请求生图／视频，未开通 API key、Cloud Billing 或充值；真实账户计费／额度扣减未独立核验，不声称已经审计零费用。

▶ 必须记录的安全限制
测试同时传入 --mode plan 与 --disable-slash-commands，CLI 明确警告前者在关闭 slash 扩展时不生效。不能把本次成功说成已经验证只读模式。
本次固定测试提示词并未要求工具，日志未发现 tool_use、tool_call 或 run_shell_command 这些文本标记，但未建立完整工具审计；不能保证凭这些字符串就穷尽所有工具执行。
--sandbox 也不等于已经确认所有工具权限边界。向外部用户接入前必须核实实际工具配置、沙箱行为、上下文来源和身份隔离，而不是依靠提示词“不要执行命令”保障安全。
不建议直接把任意用户输入传给当前完整 Coding Agent。未来主控适配层需要仅暴露受控生图／视频／任务查询工具，任务服务负责权限与额度；每用户消息保持独立，不能复用同一 CLI conversation_id 混合他人对话。

▶ 原始证据与当前状态
结果与日志留在仓库之外的受限目录：
/home/kris/.local/share/omniflow-gemini-probe/results/agy-text-summary.json
/home/kris/.local/share/omniflow-gemini-probe/results/agy-text-stdout.private.txt
/home/kris/.local/share/omniflow-gemini-probe/results/agy-text.private.log
本轮生成了一条专门的测试对话，没有恢复或修改用户原有对话。未启动多用户服务、未修改正式网站、未提交或发布。
下一步优先基于既有 agy 通路评估主控接入的安全及授权边界，不再要求用户重复给另一套 CLI 登录。
