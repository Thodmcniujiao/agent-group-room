# Agent Group Room · 代理协作室

**可配置的多模型协作群聊，由独立监工按你的规则调度与核对。**

**A configurable multi-model group chat, coordinated and checked by an independent supervisor following your rules.**

[中文](#中文介绍) · [English](#english)

## AI 生成说明 · AI generation disclosure

本仓库新增的应用代码、界面、文档和部署脚本，**全程由 GPT-6.1 Sol 在项目所有者指导下生成并迭代**。项目目标、合作方式和功能取舍由项目所有者提出。第三方开源项目及其依赖保留原作者署名和各自许可证，不包含在上述原创生成声明内。

**All project-specific application code, UI, documentation, and deployment scripts were generated and iterated by GPT-6.1 Sol under the project owner's direction.** The project owner supplied the requirements, collaboration preferences, and feature decisions. Third-party projects and dependencies retain their original authorship and licenses and are excluded from this generation claim.

## 中文介绍

Agent Group Room 是一个运行在本机的代理协作窗口。你可以组建自己的模型团队，让不同成员在同一条群聊时间线上提出方案、互相质疑、编写代码和核对结果。另一个独立的“监工”依据你用自然语言写下的合作方式，安排下一步并检查是否偏离。

成员不绑定固定的规划、评审或执行职责。默认成员是 ChatGPT、Claude、DeepSeek，可以增删、改名，也可以把同一家服务添加为多位成员，分别选择模型。

### 主要功能

- **可配置团队**：支持 1–20 位成员，每位独立配置名称、连接和模型。
- **多种连接**：ChatGPT / Claude 本机 CLI 登录、DeepSeek 官方 API，以及自定义 OpenAI 兼容 Chat Completions API。
- **独立监工**：复用任意成员的连接，独立选择模型；把你的合作规则转成可观察的检查项。
- **动态协作**：支持串行或并行只读发言、独立提案、互相质疑和单方编码，同一项目不会同时派出多个写入者。
- **运行中调整规则**：编辑完整规则，或在群聊中发给监工补充，当前步骤结束后重新核对；过期安排不继续执行。
- **可见过程**：群聊保留各方真实答复、实际模型、安排、规则状态、工具活动及本机验收退出码。
- **本机保存**：历史与事件存入 SQLite；API 密钥使用当前 Windows 用户的 DPAPI 加密保存。
- **交付工具**：查看 diff、执行契约、监工报告，导出群聊，停止运行，或在修改文件前暂停。

### 环境要求

- Windows 10 / 11，Python **3.12**，Git。
- 若使用 ChatGPT / Claude CLI 连接，需要分别安装并登录对应的官方 CLI。
- 若使用 API 成员，需要配置服务地址、可用模型和密钥；回环地址上的本机服务可选择免密钥。
- 自动修改文件时，项目必须位于 Git 仓库根目录。
- 运行完整测试套件还需要 Node.js，其中一项检查会实际执行一个小型 Node 验收脚本。

### 安装与启动

```powershell
git clone https://github.com/Thodmcniujiao/agent-group-room.git
cd agent-group-room
.\Install.ps1
.\Start-GUI.ps1
```

若 Python 未注册到 `py -3.12`，可显式指定：

```powershell
.\Install.ps1 -Python 'C:\path\to\python.exe'
```

安装脚本会建立仓库内的 `.venv`、安装锁定依赖并创建桌面快捷方式。GUI 默认使用 `http://127.0.0.1:49315`；端口占用时启动器会选择后续可用端口。关闭浏览器不会停止后台服务；再次启动会连接到现有服务。

此版本沿用 `tiered-dev` 作为内部插件及数据目录名，用户状态保存在 `~/.local/share/tiered-dev/`。密钥、历史和状态不写入本仓库。

### 开始一次协作

1. 在“团队配置”添加、编辑或移除成员，为每位选择连接与模型。
2. 在成员编辑窗口配置 API 密钥；CLI 账户可在“连接设置”完成登录。
3. 新建协作并选择本机项目。
4. 在“合作规则”中写你希望团队怎样工作，为监工选择连接与模型。
5. 选择“监工协作”，发送要完成的任务。也可使用“自由群聊”直接向成员提问。

合作规则示例：

> 先让两位成员分别提案，不看彼此答复；再互相质疑。出现分歧时，请第三位成员找证据。形成共识后再决定谁实现。监工需要逐项指出哪些要求已经符合、哪些仍待核对。

团队和模型变更在下一次开始协作时生效；合作规则修改在当前步骤结束后生效。删除成员保留历史答复。

### 能力边界与验证

监工的检查依赖模型判断，不能代替人工核对。完成校验要求存在实际工作者记录、有效证据引用及完整规则检查；最近一次编码失败不能直接宣布交付完成。

ChatGPT 编码使用 workspace-write 沙箱；Claude 使用受限文件工具；API 编码复用有工作区约束的 MCP 执行器。兼容 API 的编码模型还需支持工具调用。连接、模型权限或余额失败会如实显示，不会伪造参与或自动替换身份。

执行契约列明允许文件和验收命令。未运行测试时会明确标记；发现范围外变更会停止协作，但不会自动撤销已经发生的修改。DeepSeek 命令和本机验收会运行项目代码；命令过滤和超时不等于 OS 沙箱，请仅在信任的项目运行自动执行。

已有 20 项 GUI / 调度 / 连接测试和 6 项适配器测试。开发环境中实际验证过监工安排新增 CLI 成员、收到真实答复并核对结束，以及 Claude 对隔离文件的写入。该环境的 ChatGPT 写入曾被本机权限阻断；DeepSeek 和任意外部兼容服务的真实调用仍取决于用户的密钥、权限与服务兼容性。受控替身测试不代表所有服务均已实测。

```powershell
.\.venv\Scripts\python.exe gui\test_gui.py
.\.venv\Scripts\python.exe scripts\test_deployment.py
```

可选的 Claude Code 插件源码位于 `plugins/tiered-dev`。使用插件时，先激活 `.venv`，使插件的 `python` 命令指向安装了依赖的环境。GUI 本身无需安装该插件。

### 来源与许可

应用界面、可配置团队、监工调度和 Windows 适配层为本项目新增代码；编码执行器复用 [deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent)（MIT），固定源码版本见 [sources.lock.json](sources.lock.json)。

MCP Python SDK 为 MIT，OpenAI Python SDK 为 Apache-2.0，Starlette、Uvicorn、HTTPX 为 BSD-3-Clause。其他依赖及许可文本见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Agent Room 和 codex-review-server 只做过架构考察，未集成其应用实现。Codex CLI 和 Claude Code 由用户单独安装，分别受其自身许可与服务条款约束。

**本项目新增代码尚未指定开源许可证。** 把源码上传 GitHub 不代表它自动采用上游 MIT 协议；详见 [LICENSE_STATUS.md](LICENSE_STATUS.md)。

## English

Agent Group Room is a local workspace for collaborating with a configurable team of model agents. Members can propose solutions, challenge one another, implement changes, and inspect results in one shared conversation. An independent supervisor uses your natural-language collaboration policy to choose the next step and check whether the team is following it.

Members have no permanent planner, reviewer, or implementer roles. ChatGPT, Claude, and DeepSeek are the initial members. Add, remove, or rename them, or create several members using the same service with different models.

### Features

- **Configurable roster:** 1–20 members with separate names, connections, and model selections.
- **Multiple transports:** locally authenticated ChatGPT / Claude CLIs, the official DeepSeek API, and custom OpenAI-compatible Chat Completions endpoints.
- **Independent supervisor:** shares a selected member's connection while using its own model; turns your policy into observable checks.
- **Dynamic coordination:** sequential or parallel read-only contributions, independent proposals, critiques, and one writer at a time per project.
- **Live policy changes:** edit the policy or send supplemental instructions to the supervisor. Rechecking occurs after the current step; stale arrangements are discarded.
- **Visible evidence:** actual member responses, model IDs, assignments, policy checks, tool activity, and host-run acceptance command exit codes.
- **Local persistence:** SQLite conversations and events, with per-member API credentials encrypted using the current Windows user's DPAPI.
- **Delivery controls:** inspect diffs, execution contracts, and supervisor reports; export chats, stop a run, or pause before file changes.

### Requirements and installation

Windows 10 / 11, **Python 3.12**, and Git are required. Install and authenticate the relevant official CLI when using ChatGPT or Claude CLI connections. API members require a compatible endpoint, model, and credentials; loopback services can use the no-key option. Editing requires selecting the root of a Git repository. Node.js is also required to run the full test suite.

```powershell
git clone https://github.com/Thodmcniujiao/agent-group-room.git
cd agent-group-room
.\Install.ps1
.\Start-GUI.ps1
```

Use `Install.ps1 -Python 'C:\path\to\python.exe'` if Python 3.12 is not registered with the Windows Python launcher. Installation creates `.venv`, installs pinned dependencies, and adds a desktop shortcut. The default URL is `http://127.0.0.1:49315`; the launcher tries subsequent ports when needed and reuses an existing service.

The internal plugin and state namespace remains `tiered-dev`. User data lives under `~/.local/share/tiered-dev/`, outside the repository. Closing the browser does not stop the background service.

### Using the room

1. Open the team configuration and add, edit, or remove members.
2. Configure each member's model and API credentials, or authenticate the corresponding CLI.
3. Create a room and select a local project.
4. Write your collaboration policy and choose the supervisor's connection and model.
5. Start a supervised task, or use free chat to address members directly.

Example policy:

> Have two members propose solutions independently without seeing each other's replies. Then ask them to critique one another. If they disagree, ask a third member to find evidence. Choose an implementer after agreement. The supervisor must show which requirements are satisfied and which remain unverified.

Roster and model changes apply to the next run; policy changes apply after the current step. Removing a member preserves previous messages.

### Limits and verification

Supervisor judgments are model-generated and should remain reviewable by a person. Completion checks require real worker records, valid evidence references, and checks for every policy rule. A failed latest implementation cannot be reported as a successful delivery.

ChatGPT writing uses the workspace-write sandbox; Claude writing uses restricted file tools; API writing uses a workspace-scoped MCP executor and requires tool-call support. Connection, model, and quota errors are reported rather than replaced with fabricated participation.

Contracts specify allowed files and acceptance commands. Missing tests are identified as untested. Out-of-scope changes stop collaboration without automatically undoing changes. The API executor and acceptance commands run project code; command filtering and timeouts are not an OS sandbox. Run automatic execution only in trusted projects.

The project has 20 GUI / orchestration / connection tests and 6 adapter tests. Development checks included real supervisor coordination of a newly added CLI member and an isolated Claude file write. ChatGPT writing was blocked by the development machine's permissions. DeepSeek and other remote endpoints still require valid user credentials and compatible services. Controlled fixtures are not evidence that every external provider has been tested.

```powershell
.\.venv\Scripts\python.exe gui\test_gui.py
.\.venv\Scripts\python.exe scripts\test_deployment.py
```

The optional Claude Code plugin lives in `plugins/tiered-dev`. Activate `.venv` before starting Claude Code so the plugin's `python` command resolves to the installed environment. The GUI does not require installing the plugin.

### Attribution and licensing

The UI, configurable roster, supervisor orchestration, and Windows adapters are project-specific code. The coding executor reuses [deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent), licensed under MIT and pinned in [sources.lock.json](sources.lock.json).

MCP Python SDK is MIT; OpenAI Python SDK is Apache-2.0; Starlette, Uvicorn, and HTTPX are BSD-3-Clause. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency notices and license texts. Agent Room and codex-review-server were inspected for architecture only; their application implementations are not integrated. Codex CLI and Claude Code are separately installed tools governed by their respective licenses and service terms.

**No open-source license has yet been selected for the project-specific code.** Publishing source on GitHub does not automatically apply an upstream dependency's MIT license to this repository. See [LICENSE_STATUS.md](LICENSE_STATUS.md).
