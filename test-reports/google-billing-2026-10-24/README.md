# Google 余额与双渠道调用验证

应用支持原 Gemini Developer API Interactions 和 Vertex generateContent；连接库的 `google_billing` 记录模式、项目、结算账号和区域，API Key 继续使用原 DPAPI 存储，Vertex 凭据使用本机 ADC。现有未配置 Google 结算路由的连接保持原行为。

自动选择先检查 10 分钟内的余额观察，优先使用当前可用、到期明确且适用于 Vertex 的赠金，再考虑 Gemini Developer API 的有效预付余额及 AI Studio 明确标为适用的赠金。预付为零时不使用其附带赠金。无新鲜、适用余额时阻断，不把未知数当成零或无限额，不切换模型。

调用前独立核验项目启用结算、结算账号一致及开放状态；自动选择 Developer 时另查 API Key 所属项目。路由先于 ContextTurn 创建，会话绑定包含渠道、项目、结算和 ADC 文件身份。工具续轮固定渠道，完整签名块原样保留；失败请求不会跨渠道自动重试。

## 离线验证

运行 `python scripts/run_context_checks.py --report-dir test-reports/google-billing-2026-10-24`，结果见同目录 `unit-tests.json` 和 `unit-tests.log`：282 项，280 通过，2 跳过，0 失败。新增测试覆盖过期和未来赠金、缺失适用范围或到期时间、快照过期和时钟回拨、不同账号和币种、预付为零、API Key 项目不匹配、关闭结算账号、签名及并行函数结果、原生调用 ID、配额项目请求头、配对权限、错误来源、过大请求和 OAuth 错误脱敏。

浏览器解析基于真实页面检查后修正：Cloud 为 `treegrid/gridcell`，AI Studio 预付为跨域 Google Payments 框架。扩展按父页与内嵌框架核对账号，且只发余额字段。

## 真实验证

- 安装官方 Google Cloud CLI 590.0.1 ZIP，按官方发布值校验 SHA-256；用户完成 ADC 登录并设置配额项目。
- 实际查询项目结算绑定和结算账号开放状态，均成功。启用需要的元数据 API，实际模型目录请求成功。
- 直接 Vertex 请求返回 `API_TEST_OK`。
- 已保存 Gemini Agent 的自动路由通过真实应用调试返回 `ROOM_VERTEX_OK`，渠道为 Vertex。
- 临时指定原 Developer 渠道的真实应用调试返回 `ROOM_DEVELOPER_OK`，测试后恢复自动模式。
- Vertex 原生工具续轮完成一次本机只读探针，返回 `SIGNATURE_ROUNDTRIP_OK`，确认下一次请求保留模型签名；不执行文件写入。
- 将真实余额快照中的 Vertex 资金从测试输入中移除后，自动逻辑选择 Developer；使用真实 Google API 完成 API Key 所属项目与结算绑定核验。此为受控回放，不修改实际赠金余额，也不冒充真实耗尽。
- 用户在 Edge 安装、重新加载并授权配套扩展；本机页面完成配对，实际 Cloud 赠金和 AI Studio 内嵌预付余额均成功写入本机状态，且适用赠金与预付金额分项显示。

真实账号标识、完整账单数据、配对码、浏览器截图及私有调试记录不保存在此公开报告中。原始联调资料位于忽略的 `work/`，凭据及持续余额状态位于用户本机状态目录。

## 限制

余额是官方页面的快照，账单和抵扣有延迟，不能充当严格扣费上限。需要浏览器及已登录账单页持续打开；关闭或读取失败超过有效期后，自动模式等待同步。真实赠金耗尽没有被人为制造；耗尽后的路由选择由离线用例验证。模型目录不保证每个模型都可用，模型权限和地区限制仍由 Google 决定。

官方依据：[Cloud Billing API](https://docs.cloud.google.com/billing/docs/reference/rest)、[赠金页面字段](https://docs.cloud.google.com/billing/docs/how-to/resolve-issues)、[Gemini API 计费及试用赠金限制](https://ai.google.dev/gemini-api/docs/billing)、[Gemini 模型快速入门](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart)。
