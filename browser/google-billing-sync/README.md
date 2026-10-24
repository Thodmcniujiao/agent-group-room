# Google 余额同步扩展

从用户登录的 Google 官方页面读取余额，发送到配对的本机 Agent Group Room。无 Cookies、密码、API Key 或网络拦截权限。Google 公开 Cloud Billing API 的账号状态和项目绑定查询由应用内 ADC 完成；剩余赠金及预付余额来自浏览器可见账单字段。

## 安装和配对

1. 在 Chrome / Edge 扩展管理页开启开发者模式，加载本目录。更新 manifest 后需在扩展管理页重新加载，并接受变更的权限。
2. 打开应用的「Google 渠道与余额」页面，保存项目、结算账号和调用方式。
3. 点击「与已安装扩展配对，并启用每 5 分钟刷新账单页」。或在扩展弹窗手动填写本机地址、配对码和自动刷新选项。
4. 打开对应 Cloud 赠金页和 AI Studio 账单页，首次配对后刷新一次。保持页面和浏览器打开，才能持续更新。关闭后，应用中的观察数据超过 10 分钟会失效。

## 数据和边界

- Cloud：读取 `treegrid` 的赠金 ID、名称、状态、币种、剩余额度、适用范围、起止日期。缺失或截断 ID 的行跳过；自动路由只使用当前可用且到期日期明确、适用范围已确认的行。
- AI Studio：父页面读取结算账号 ID 和明确标为适用的 GCP 赠金；其 `payments.google.com/payments/u/*/embedded_landing_page` 框架只提取 Cloud Prepay 账号及余额。后台同时核对父页 URL、父页账号和框架账号，三者不一致时不入库。完整 Google Payments URL、姓名、交易和支付方式均不发送。
- 新 Free Trial 不作为 Gemini Developer API 余额。Developer 可用金额包含预付余额与 AI Studio 明确标为适用的赠金；预付余额为零时不把赠金当作可用资金。
- 币种分别显示，不做隐式汇率换算；两个渠道余额不能直接相加，当赠金覆盖范围重叠时也不代表两份资金。
- 每 5 分钟只重新加载已打开的两类账单标签页。重复、未变化的 DOM 不会延长旧读数的有效时间；刷新得到新页面后才重新确认。
- 配对令牌只允许向本机 `/api/google-billing/sync` 写入余额，不能读取聊天或密钥。应用中「重置配对码」会使旧令牌失效。

账单存在延迟，快照路由不是扣费硬上限。自动模式没有新鲜可用余额时阻断；手动指定渠道遵守 Google 的正常收费规则。项目切换及 API Key 所属项目由调用前的官方接口独立核验，不通过失败推理来探测余额，也不在工具续轮中切换协议。

## 离线验证

```powershell
node scripts/test_google_billing_reader.cjs
.\.venv\Scripts\python.exe -m unittest discover -s gui -p test_google_billing.py
```

运行目录是项目根目录。测试只使用构造数据及 MockTransport；真实联调需由用户授权，并使用自己的 Google 登录和模型额度。
