# 推理强度原始值更新 · 测试记录

2026-10-21，部署版本 **0.10.1**。本次没有发送真实对话或生成请求；只读取官方模型元数据。界面测试在独立夹具中进行，没有使用实际协作室或密钥。

## 结果

- **106 项离线回归检查全部通过**，0 失败、0 错误。逐项结果见 [unit-tests.log](unit-tests.log)，源码 SHA-256 和用例清单见 [unit-tests.json](unit-tests.json)。
- 官方回包已脱敏留存：[official-metadata.json](official-metadata.json)。ChatGPT 7 个模型，Claude CLI 12 个模型，Gemini 63 个模型，DeepSeek 2 个模型。这是当前连接当时返回的目录，不代表全部型号都通过推理调用验证。
- 五处选择器共用原始值：Agent 默认设置、群内成员设置、群侧栏、监工、调试控制台。选项文字与提交值完全相同，保留官方顺序，不翻译、不插入保存过但未返回的值，不删除 `ultra`。
- DeepSeek 的 `low / high / max` 来自回包 `effort.supported_levels`；未加入兼容别名或文档中存在但目录未返回的值。Gemini 当前兼容接口没有返回强度枚举，选择器禁用，不推断枚举。
- 换模型后重新核对支持列表。ChatGPT 从支持 `ultra` 的型号切到 `gpt-6-luna` 时移除 `ultra`，使用该型号实际返回的默认值 `medium`。Claude 切到 Opus 4.6 时不提供 `xhigh`；Haiku 4.5 未返回可选强度时禁用。
- “未指定”是界面控制项，不是模型档位，表示不发送强度参数；官方没有返回默认值时不猜测 `high`。
- 原生 ChatGPT thread 配置与 turn 参数、Claude CLI 参数、DeepSeek SDK 序列化请求、DeepSeek 编码 MCP 配置均有精确参数/省略参数检查；未知保存值在调用前校验，支持信息未返回时省略参数。
- 部署后模型目录四个连接均为 ready，见 [deployed-models.json](deployed-models.json)。现有 **3 个协作室、29 条消息及全部群设置保持一致**，Agent 配置、专用 CLI 配置和加密密钥文件逐字节一致，见 [deployment.json](deployment.json)。

## 界面证据

- [侧栏原始选项与当前选择](ui-sidebar.json)，[侧栏可访问性快照](ui-sidebar.txt)
- [Agent 默认设置](ui-agent-default.txt)，[更换默认模型的结果](ui-model-change.json)
- [群内成员 Opus 4.6 选项](ui-room-member.txt)
- [监工更换模型的结果](ui-supervisor.json)
- [ChatGPT 调试控制台](ui-debug.txt)，[调试模型切换](ui-debug-model-change.json)，[DeepSeek 调试选项](ui-debug-deepseek.txt)
- [Agent 设置截图](agent-default-sol.png)，[群成员设置截图](room-member-opus46.png)
- [浏览器脚本错误](ui-errors.txt)：空，无错误。

界面快照中的 refs 是测试浏览器会话标识。开窗初始快照可能处于调试记录加载阶段，模型选项会短暂禁用，加载结束后恢复。测试只修改了夹具的群成员配置，没有运行模型生成。

## 复现

在仓库目录运行：

```powershell
.venv/Scripts/python.exe scripts/run_context_checks.py --report-dir test-reports/effort-recheck
node --check gui/web/app.js
node --check gui/web/debug-console.js
node --check gui/web/reasoning-effort.js
```

重新读取本机当前已连接账号的官方元数据（不产生模型答复）：

```powershell
.venv/Scripts/python.exe scripts/inspect_effort_metadata.py --output test-reports/effort-recheck/official-metadata.json
```

独立界面夹具需使用新的空目录，随后访问 `http://127.0.0.1:49341/`：

```powershell
.venv/Scripts/python.exe scripts/serve_context_fixture.py --state "$env:TEMP/agent-effort-recheck" --port 49341 --catalog-file test-reports/effort-2026-10-21/official-metadata.json
```

## 官方字段来源

- [OpenAI App Server：model/list](https://learn.chatgpt.com/docs/app-server)：`supportedReasoningEfforts`、`defaultReasoningEffort`。
- Claude：本机 CLI 控制协议初始化实际回包 `models[].supportedEffortLevels`；完整保留于本目录的官方元数据文件。
- [DeepSeek Models](https://api-docs.deepseek.com/api/list-models/)：`effort.supported_levels`、`effort.default_level`。
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)：请求参数 `reasoning_effort`。

本次未逐模型执行收费生成测试；实际账户授权和可用性仍由服务端在调用时判断。
