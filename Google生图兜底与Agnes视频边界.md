【Google 生图兜底与 Agnes 视频边界】

▶ 用户新增要求
• 图片生成可在主服务不可用时切换 Google 的 Banana 系列作为备用；用户拥有 Google AI Pro，希望利用已有权益。
• AI 视频生成仅依赖 Agnes 当前选定的免费模型；免费额度／优惠到期时提示用户，不切换其他供应商。
• 此要求不等于授权开通付费账单、购买额度或调用收费模型，也不自动批准前文全部配额参数。

▶ 官方权益核对
本轮已访问并读取以下官方资料，均返回 HTTP 200：
1. https://ai.google.dev/gemini-api/docs/google-ai-plans
2. https://support.google.com/googleone/answer/14534406?hl=en
3. https://ai.google.dev/gemini-api/docs/billing
4. https://ai.google.dev/gemini-api/docs/image-generation

关键结论以第 1 项的 Limitations and compatibility 为依据：
• Google AI Pro / Ultra 可为 AI Studio Playground 与 Build 界面提供更高的原型开发额度，模型包含 Nano Banana 等。
• 开发者订阅权益仅适用于 AI Studio 网页界面；API key 或外部应用直接使用 Gemini API 单独计费和管理，不能把会员网页额度直接视为本站自动生图额度。
• 符合条件、启用 Cloud Billing 的订阅者可能获得 Google Developer Program 的月度 Cloud credits，用于包括 Gemini API 在内的云服务；资格、领取情况与本账号余额尚未核实。
• 文档说明预付费用户激活优惠积分可能需要正数的付费余额，因此不能承诺零充值即可使用。Google One AI credits 与 Google Cloud credits 是不同体系。
本轮未读取用户 Google 凭据或账户账单，未提交生图／视频任务，未开通任何计费。

▶ 生图备用设计
主服务仍优先 Agnes；Google Nano Banana 系列为用户允许的备用方向，具体模型 ID、文生图／改图能力及可用额度待核验。
自动切换的前提是确认存在合法可调用的 Gemini API 项目及适用免费／赠送额度，明确预算边界并获准接入；不能仅凭 Google AI Pro 订阅就启用自动调用。
如目前只有网页会员权益，可考虑人工在 Google 网页生成后上传回工作台作为过渡，不默认通过共享账号、抓取登录 Cookie 或模拟网页绕过 API 计费。
切换供应商意味着提示词与参考图发送给 Google，需在产品中明确显示备用来源，并在隐私说明中交代第三方处理；不能静默绕过内容安全拒绝。
主服务提交结果不确定时先核对，不立即向备用服务重复提交；区分限额、服务故障、参数错误与内容安全拒绝，不能把所有错误都当成可切换条件。

▶ AI 视频当前策略与后续探索
此前确认 AI 动态视频使用 Agnes 的 agnes-video-2.5-flash，不自动切换收费 Agnes 型号、Google 视频或其他供应商。
用户随后要求探索 Google AI Pro 视频备用并尝试验证。官方已确认订阅含网页视频权益，但尚未完成用户账号生成实测，API 额度／费用也未核验；因此当前策略暂不改变，Google 视频作为待验证候选，而非永久排除或已经启用。后续结果见 GoogleAIPro视频权益与实测阻塞记录.md。
区分：
• 当日／周期额度用尽：提示额度暂不可用；仅在供应商给出可信恢复时间时显示时间。
• 免费优惠结束或开始收费：暂停新的 AI 视频任务，向用户及管理员提示；不自动扣费继续。
• 网络或服务异常：提示暂时故障／状态待核对，不谎报免费期结束。
已完成作品继续可查看和下载。已受理任务尽量续查并取回，不因为新提交暂停而删除记录。
此前独立保留的本地快速运镜能力不冒充 AI 动态视频，也不在 Agnes 不可用时静默替换。
如何发现免费条件变化（官方状态、账户额度接口、配置的明确截止日期或人工核验）尚未验证，不能承诺只靠报错即可自动准确判断或在价格变化前阻止所有费用。免费状态不明时应暂停新提交并要求核对，而非试调用探测计费。

▶ 待补充信息
用户是否已有 Gemini API / Google Cloud 项目、是否领取过适用于接口调用的赠送额度；不需要在聊天中粘贴密钥。账户资格和余额核验前，Google 自动生图备用保持未启用。
