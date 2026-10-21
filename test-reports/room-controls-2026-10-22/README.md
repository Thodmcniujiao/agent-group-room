# 协作室上下文控制、强制继续、气泡与头像测试

本地日期：2026-10-22（Europe/London）。版本 **0.11.0**，已部署到桌面“代理协作室”。

## 结果

- 回归检查共 **129 项：127 通过、2 跳过，0 失败、0 错误**。逐项记录见 [unit-tests.log](unit-tests.log)，源码 SHA-256 与用例清单见 [unit-tests.json](unit-tests.json)。跳过的两项是原有 Git 所有权实境检查，需通过 `AGENT_ROOM_DUBIOUS_REPO` 提供专用目录；本次没有修改实际项目的所有权来构造测试。
- JavaScript 语法检查通过：`app.js`、`avatars.js`、`connections.js`。界面浏览器没有脚本错误，见 [ui-errors.txt](ui-errors.txt)。
- 本次没有请求真实模型生成。API 序列化检查使用模拟 HTTP 响应；界面使用隔离数据目录与明确标注的测试夹具，不调用真实模型或修改实际项目文件。

## 四项功能的证据

### 1. 每个协作室的上下文设置

顶部“上下文设置”集中配置本地软预算、输出预留/API 输出上限、安全余量、工具续轮上限、监工轮数和历史溢出处理。默认数值沿用旧版：128,000、32,768、6,000、16、12。CLI 的原生输出和工具执行还受运行器约束；服务端限制不会被修改。

测试覆盖两个协作室设置隔离、非法修改不落盘、输出和工具轮数传到编码执行器、CLI 历史等待时保留原生 thread，以及完整请求中系统提示、工具定义和 JSON 转义的计量。群原文不会因预算裁剪而从档案删除。

证据：[设置截图](context-settings.png)、[界面字段快照](ui-context-settings.txt)。

### 2. 输入框右侧“超预算继续”

等待中的请求可以原地继续，不重新派发执行契约、不重复追加用户提示。本次协作的覆盖开关不写入协作室长期设置。调大预算并保存，也能放行符合新预算的请求。

界面实际执行了两条路径：

1. 模拟请求超过预算 → 界面显示等待状态 → 点击强制继续 → 原请求完成。
2. 再次超过预算 → 在此协作室把预算调到 256,000 → 保存 → 原请求完成，输入框恢复可用。

还核对了恢复请求时的状态事件顺序，避免请求已经完成，界面却又显示“运行中”。

证据：[等待截图](budget-paused.png)、[等待时按钮状态](ui-budget-paused.txt)、[强制继续记录](ui-force-resumed.txt)、[调大预算后完成状态](ui-raised-budget-resumed.json)。离线检查还核对了实际 SDK 请求只发一次、原有系统历史与契约文本不变。

如果服务已重启、原进程已消失，按钮会开启一次带本地预算覆盖的继续协作，让监工先核对已完成结果；不会声称恢复已消失的进程。此分支有 API 检查。部署时没有替用户点击按钮，也没有启动原先阻断的任务。

### 3. 用户与成员气泡对称

用户头像和消息靠右，成员靠左；保留现有蓝灰配色，统一气泡内距和边框。浏览器读取实际元素边界，验证用户头像贴在行右边、成员头像贴在行左边。

证据：[聊天布局](chat-layout.png)、[带头像与继续结果的界面](final-room.png)、[元素边界](layout-bounds.json)。还检查了桌面最小宽度 1024 px，无横向溢出，设置与继续按钮均可见：[截图](room-minimum-width.png)、[宽度记录](minimum-width.json)。

### 4. 你、监工和全部 Agent 的头像

“Agent 管理”中提供“设置所有头像”，也可以点击单个 Agent 的头像进入。支持 PNG/JPEG/WebP，在浏览器居中裁切、缩小后保存在本机，可恢复默认。头像按身份保存，与密钥和入群状态分离。

界面上传了测试用的纯色图片，核对用户与 Claude 的所有可见头像均更新、图片能解码，并在刷新后保留。离线检查覆盖用户、独立监工、同一服务的多个 Agent 独立保存、重启读取、恢复默认、非法格式和大小限制、API 身份与请求令牌检查。

证据：[头像设置](avatar-settings.png)、[完整身份列表](ui-avatars.txt)、[上传后的 DOM](avatar-dom.json)、[刷新后的图片解码状态](avatar-after-reload.json)。测试图片为本目录两个 `*-avatar-fixture.png`，没有使用实际用户照片。

## 部署与数据保留

[deployment.json](deployment.json) 记录健康检查与备份位置。原有 **4 个协作室、90 条消息**、群设置、合作规则、原生会话记录、上下文投递与消息顺序均逐行一致；Agent 配置、专用 CLI 配置和加密密钥逐字节一致。启动没有自动继续旧任务，没有给实际身份设置测试头像。

## 复现

在仓库目录运行，使用新报告目录，避免覆盖本次留档：

```powershell
.venv/Scripts/python.exe scripts/run_context_checks.py --report-dir test-reports/room-controls-recheck
node --check gui/web/app.js
node --check gui/web/avatars.js
node --check gui/web/connections.js
```

隔离界面测试（数据目录必须是新的空目录）：

```powershell
.venv/Scripts/python.exe scripts/serve_context_fixture.py --state "$env:TEMP/agent-room-controls-recheck" --port 49341 --catalog-file test-reports/effort-2026-10-21/official-metadata.json --controls-demo
```

访问 `http://127.0.0.1:49341/`。发送任意测试需求会触发模拟预算等待；全程不会请求真实模型。
