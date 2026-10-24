# Agent Group Room · 代理协作室

**当前版本 / Current release: v0.1.2** · [更新记录 / Changelog](CHANGELOG.md)

**可配置的多模型协作群聊，由独立监工按你的规则调度与核对。**

**A configurable multi-model group chat, coordinated and checked by an independent supervisor following your rules.**

[中文](#中文介绍) · [English](#english)

v0.1.2 汇总当前项目的全部功能更新：独立的协作室 Agent 实例、Gemini 与 Vertex 双渠道及赠金余额同步、持久工作流与方案审批、上下文与协作日志，以及可选的 Voice Studio 源码。安装步骤见下文；本机凭据、运行记录、下载的语音引擎和模型不随源码发布。

v0.1.2 brings together independent room member instances, Gemini/Vertex routing with credit balance sync, persistent workflows and plan approval, context controls and collaboration logs, plus the optional Voice Studio source. See the installation instructions below. Local credentials, runtime history, and downloaded voice engines/models are kept outside the source release.

## AI 生成说明 · AI generation disclosure

本仓库新增的应用代码、界面、文档和部署脚本，**全程由 GPT-6.1 Sol 在项目所有者指导下生成并迭代**。项目目标、合作方式和功能取舍由项目所有者提出。第三方开源项目及其依赖保留原作者署名和各自许可证，不包含在上述原创生成声明内。

**All project-specific application code, UI, documentation, and deployment scripts were generated and iterated by GPT-6.1 Sol under the project owner's direction.** The project owner supplied the requirements, collaboration preferences, and feature decisions. Third-party projects and dependencies retain their original authorship and licenses and are excluded from this generation claim.

## 中文介绍

Agent Group Room 是一个运行在本机的代理协作窗口。你可以组建自己的模型团队，让不同成员在同一条群聊时间线上提出方案、互相质疑、编写代码和核对结果。另一个独立的“监工”依据你用自然语言写下的合作方式，安排下一步并检查是否偏离。

成员不绑定固定的规划、评审或执行职责。默认成员是 ChatGPT、Claude、DeepSeek，可以在 Agent 库中保存、改名，并按服务商归组；每个协作室从库中独立邀请成员，同一家服务可保存多位 Agent，分别选择模型。

### 主要功能

- **独立桌面窗口**：通过 pywebview / Windows WebView2 启动应用，具有独立标题栏、任务栏图标和窗口尺寸记忆；重复启动唤回已有窗口。关闭窗口后后台继续处理任务，重新打开可查看进度。
- **两层管理**：Agent 库按服务商保存最多 100 位 Agent 的连接与密钥；每个协作室独立邀请 0–20 位成员。同一个已保存 Agent 可以反复加入，每个实例有独立的名字、头像、模型与会话，并共用来源 Agent 的连接、密钥和账户额度。在“协作室信息 → 邀请成员”中点击“再添加一个”，点击成员可编辑实例设置。
- **统一模型目录**：每次启动或刷新页面时，后台自动查询 OpenAI、Claude、Gemini、DeepSeek 四家的模型 ID。默认模型、团队成员、监工、调试控制台复用同一连接目录；默认模型和调试模型使用完整下拉选择，另设自定义 ID 输入，不按已填文字过滤目录；同连接合并查询，不同账号隔离。查询失败保留上次目录和当前选择，其他服务手动填写。
- **账户读数**：在团队侧栏与 Agent 管理卡片显示 ChatGPT / Claude 本机账户的额度使用率、剩余比例与重置时间，以及 DeepSeek API 的实际余额。约每分钟自动更新，也可逐账户手动刷新；同一账户共享额度，不同 API 密钥隔离。Gemini 及普通 OpenAI / Claude API Key 无可直接读取的账户余额时，显示明确说明和官方用量／账单入口，未知值不会显示成 0。
- **逐 Agent 调试**：在 Agent 管理中打开独立控制台，发送测试指令、临时选择模型，实时查看答复、错误与耗时，并可停止请求。无需加入团队，不带项目或群聊上下文。
- **多种连接**：ChatGPT / Claude 本机 CLI 登录、DeepSeek 官方 API，以及自定义 OpenAI 兼容 Chat Completions API。
- **独立监工**：复用任意已保存 Agent 的连接，无需将它加入普通成员名单，独立选择模型；把你的合作规则转成可观察的检查项。
- **动态协作**：支持串行或并行只读发言、独立提案、互相质疑和单方编码，同一项目不会同时派出多个写入者。
- **运行中调整规则**：编辑完整规则，或在群聊中发给监工补充，当前步骤结束后重新核对；过期安排不继续执行。
- **可见过程**：群聊保留各方真实答复、实际模型、安排、规则状态、工具活动及本机验收退出码。
- **本机保存**：历史与事件存入 SQLite；API 密钥使用当前 Windows 用户的 DPAPI 加密保存。
- **交付工具**：查看 diff、执行契约、监工报告，导出群聊，停止运行，或在修改文件前暂停。

### 持久协作与上下文控制（0.12）

工作流现在持久保存目标、规则、证据编号、方案版本、批准和待执行契约。方案从成员原文原样入库；用户批准绑定版本哈希，换版本后旧批准不再有效。监工只把公开安排发进群，完整控制返回另行归档。讨论成员继续直接接收带作者的公开原文，指定执行者只接收自足契约，契约正文不会同时塞进 task 和 context。

执行前由宿主检查 Git、实际执行配置、额度读数和恢复记录。确定的环境故障以红色宿主消息显示，点击其下方“复查并继续”；同一故障仍在且只请求续接时不会调用模型。修复后仅在方案、批准、政策、执行者和路径仍有效时续接原契约。契约外变更必须先恢复，预算覆盖不能绕过该检查。

顶部“上下文设置”新增监工账本模式（默认，可切换完整状态）、续接短语（初始仅“继续”）、讨论容忍度 1–5、维护阈值（默认 0.6）和交接原文条数（默认 8）。投递按实际覆盖区间记录，省略的原文保留为缺口；分段读取完整后才标为已投递。换连接、型号或角色指令时使用公开交接包，完整私有历史保留在原身份分支。

“变更与报告 → 工作流 / 用量 / 原始控制返回”提供逐请求用量和审计资料。Codex 累计计数取增量，Claude 缓存输入分项合计，Gemini 保留缓存与思考用量，兼容 API 工具续轮逐次记录；缺失字段保持未知。中文估算先以影子方式校准，连续 20 次达到误差要求后才参与拦截。原生 CLI 的压缩范围遵守本机返回的能力限制，不能满足阈值时使用公开交接包，不降低推理强度。

实现与验证留档见 [token 优化记录](test-reports/token-optimization-2026-10-22/README.md)。真实模型测试需显式传入 `--live`，会使用对应账户额度；普通回归不调用模型。

0.12.1 按独立审计继续修复：环境阻断指纹忽略临时路径、PID 和时钟并保留错误码；范围恢复后可续接有效原契约；复查/批准按钮使用独立动作记录，不再生成用户原文。监工优先接收最近咨询的回复及宿主检查，超长宿主原文分段投递并保留缺口；校准接入 CLI、API 及工具续轮的实际预算检查。账本支持嵌套及按 ID 增量，执行者交接保留完整契约、执行状态和恢复记录，执行入口仍只传契约正文一次。当前方案结束前须有明确共识，或由真实用户决定采用存在反对/未表态的版本。

审计修复与限制见 [0.12.1 复验记录](test-reports/token-audit-fixes-2026-10-22/README.md)。**完整任务的同条件 token 节省验收仍未完成**，原方案标准保持不变。Claude 的系统提示快照已按本机 CLI 能力显式启用；本次真实续接探针被账户周额度限制挡住，不能声称已实测通过。分享测试记录时使用 `scripts/export_test_reports.py` 生成公开白名单压缩包，勿直接打包含原生私有会话的整个目录。

0.12.2 将方案审批改为宿主生成的正式文档：群聊显示文档卡片，独立阅读、下载 HTML/Markdown、打印为 PDF；批准绑定文档版本及哈希。批准前自动清理已结束任务的残留项目锁；项目确有另一任务时，先记录批准并排队，空闲后自动继续。见 [审批文档与续接验证](test-reports/approval-documents-2026-10-22/README.md)。

0.12.3 由宿主在执行预检中自动为尚无 Git 仓库的所选项目执行 `git init`，在群聊留下宿主操作记录，再检查执行器环境。已有仓库不重复初始化，位于父仓库中的子目录仍需选择真实根目录；损坏元数据或权限异常会保留现场并报告。见 [宿主自动初始化验证](test-reports/host-git-init-2026-10-22/README.md)。

0.13.0 增加房间独立的“历史摘要与工具输出”设置，默认关闭。开启后可分别使用作者自述摘要、有界目录、工具输出归档与回取、只读项目工具及简洁发言指令；开关在下一次调用生效并使用新分支，当前调用保持原设置。摘要与编号不算全文已投递，分段读取完整后才更新覆盖；当前方案、相关未决异议和用户约束保留原文。Bash、Git 和宿主验收在预览前归档，退出码不被预览改写。关闭优化仍可回取已有归档；执行身份跨分支读取自己的执行输出，不跨角色读取讨论输出。

设置中可查看按成员、角色、会话、日期、适配器及发言类型的用量与回取报表。缓存读写已经包含在输入总量内，推理已包含在输出总量内；未知字段与尚未验证的净收益明确标出。双投递、Headroom、RepoMap、TOON、ccusage、CoMem、Context Mode、Claude-Mem、LLMLingua 保留独立实验准入，当前均关闭，待日后测试。实施范围见 [路线图](Plan_documents/成员摘要与上下文优化路线图.md)，功能验收与真实效果的区别见 [0.13.0 验收记录](test-reports/summary-roadmap-2026-10-22/README.md)。

每个优化开关与预算参数旁均有“功能与原理”链接，点击可查看该项功能、工作原理、默认值、依赖、关闭行为及限制；实验项另列未来测试要求和上游资料。说明在设置上方打开，返回后保留尚未保存的编辑，也可单独阅读[完整说明手册](gui/web/optimization-guide.html)。本机服务运行时可直接打开 [本机说明页](http://127.0.0.1:49315/assets/optimization-guide.html)。

0.14.0 在输入框上方增加默认开启的“本地 log”开关和“协作日志”入口。每次开始协作、自由讨论、批准续接或环境复查分别建立日志目录，关联成员、角色、会话分支、发言及逐次请求。API 保存实际 HTTP 请求体（可读 JSON 与原始字节 Base64）、返回正文数据和逐请求用量；CLI 保存宿主实际发送的提示、会话参数、stdout 事件及 stderr。CLI 内部未暴露的最终服务商请求体不宣称完整可见，未返回的计量字段保持未知。

日志位于本机状态目录 `~/.local/share/tiered-dev/gui/collaboration-logs/<房间>/<协作>/`，可在界面中分页查看或下载单份 JSONL；不记录鉴权请求头或环境变量。开关按房间保存，运行中切换影响后续诊断记录，关闭后保留已有文件；群聊、原生会话续接与基础用量记录继续按原逻辑保存。用量显示已知输入/输出、缓存命中率、上下文窗口与软预算各自的占用率，估算窗口与服务商返回值区分显示。日志含实际任务材料，不进入项目目录或公开测试导出。

“Agent 管理 → 设置所有头像”新增聊天头像大小滑块，24–96 px，即时预览、松开自动保存，可恢复默认 32 px。大小应用于全部协作室中的用户、监工及成员聊天头像，侧栏和头像图片保持原有设置。实现边界、数据格式与验收见 [0.14.0 日志及头像设置记录](test-reports/collaboration-log-2026-10-22/README.md)。

0.14.1 修复执行中途故障的分流：输出截断、工具轮数耗尽、写入待核实及运行器异常均交由宿主记录、检查与阻断，复查不再调用监工解释故障，也不重派已启动契约。原有文件、失败证据和未核实恢复记录保留；宿主完整验收可独立证明交付，不把执行器原失败改成成功。旧版本误标的最近一次运行异常在续接时也先走宿主检查。

编码执行输出上限现在与房间“上下文设置 → 输出预留 / API 输出上限”一致：移除宿主、MCP 配置及 HTTP 子进程的固定 16,384 校验，保留应用上限 128,000、原生进程隔离及其他资源边界。现有默认 32,768 无需重新保存即可用于下次请求；服务商限制仍独立生效。工具续轮默认 16，可在同处单独设置为 1–100。两个参数均附功能与原理说明。验证见 [0.14.1 宿主分流与输出上限](test-reports/execution-host-routing-2026-10-22/README.md)。

### Google 余额与双渠道调用

顶部「Google 渠道与余额」可为已保存的官方 Gemini Agent 配置 Gemini Developer API、Vertex AI 或按余额自动选择。自动模式优先使用当前适用于 Vertex 的 Cloud 赠金，再使用 Gemini API 预付余额和 AI Studio 明确标为适用的赠金；未知、过期或超过 10 分钟的余额不会用于自动请求。切换在新调用前完成，并按渠道创建独立会话，保留原生工具调用签名。

Vertex 使用 Google Cloud CLI 的本机 ADC；项目和结算账号绑定在调用前通过官方接口核验。剩余赠金和预付余额由用户登录的官方账单页通过[配套浏览器扩展](browser/google-billing-sync/README.md)只读同步，支持 AI Studio 的 Google Payments 内嵌账单。配对后每 5 分钟刷新已打开的账单标签页；浏览器关闭后读数会过期。账单存在延迟，此功能不是扣费硬上限。

已验证官方模型目录、两个渠道的真实应用调试、真实余额同步，以及 282 项离线回归；具体范围与限制见[验证记录](test-reports/google-billing-2026-10-24/README.md)。

### 环境要求

- Windows 10 / 11，Python **3.12**，Git，Microsoft Edge WebView2 Runtime（现代 Windows 通常已安装）。
- 若使用 ChatGPT / Claude CLI 连接，需要分别安装并登录对应的官方 CLI。
- 若使用 API 成员，需要配置服务地址、可用模型和密钥；回环地址上的本机服务可选择免密钥。
- 自动修改文件时，宿主自动初始化尚无仓库的项目；已有仓库需要选择其根目录。
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

安装脚本会建立仓库内的 `.venv`、安装锁定依赖并创建或更新桌面快捷方式。双击快捷方式或运行 `Start-GUI.ps1` 将直接打开独立桌面窗口。界面由 Windows WebView2 承载，继续使用本机后台（默认端口 49315，被占用时自动顺延）。重复启动会唤回已有窗口；关闭窗口不会停止后台任务。登录授权和官方账单等外部链接在默认浏览器打开。

桌面版沿用原有配置、加密密钥和 SQLite 历史，WebView2 的界面状态另存于用户数据目录。当前通过仓库内 Python 环境运行，尚未打包成免安装单文件 EXE。排查问题时可运行 `Start-GUI.ps1 -Browser` 打开浏览器界面；桌面宿主日志在 `~/.local/share/tiered-dev/gui/desktop.log`。本次迁移保留现有界面，不增加右键菜单。

此版本沿用 `tiered-dev` 作为内部插件及数据目录名，用户状态保存在 `~/.local/share/tiered-dev/`。密钥、历史和状态不写入本仓库。

ChatGPT / Claude 登录：在“Agent 管理”点击对应登录按钮，在系统浏览器完成授权。桌面启动会自动查找已安装的 CLI，无需先打开终端；授权结束后自动更新连接、模型和用量。CLI 缺失、启动失败、授权失败或超时会显示在对应 Agent 卡片，可处理后重试。

### 开始一次协作

1. 在“Agent 管理”保存各家的连接、密钥和默认模型；同一家归组，每个密钥字段都标注对应 Agent 名称。
2. 新建协作并选择本机项目，新协作室从空成员名单开始。
3. 打开顶部“协作室信息”，点击 ＋ 邀请成员、− 移出成员，点击头像设置群内模型。成员、模型、监工与暂停设置只影响此协作室；连接和密钥继续复用 Agent 库。
4. 在“合作规则”中写你希望团队怎样工作，为监工选择连接与模型。
5. 选择“监工协作”，发送要完成的任务。也可使用“自由群聊”直接向成员提问。

模型目录：页面打开时自动更新，也可点击右侧“↻”手动刷新。目录状态与失败原因显示在对应选择器旁，刷新不自动更换已保存模型。ChatGPT 和 Claude 从已安装 CLI 的模型目录读取，API 连接查询官方模型目录，不发送对话请求。目录条目不等于每个型号都已通过实际推理验证。

推理强度：所有选择器复用对应模型的官方返回值，保留原始英文标识与顺序，不翻译、不补固定档位。ChatGPT 使用 `supportedReasoningEfforts`，Claude CLI 使用 `supportedEffortLevels`，DeepSeek 使用 `/models` 的 `effort.supported_levels`。未返回可选强度时禁用选择器；“未指定”表示不发送强度参数。更换模型会重新核对强度；官方默认值只在回包提供时使用。测试与脱敏回包见 [推理强度测试记录](test-reports/effort-2026-10-21/README.md)。

Reasoning effort selectors share the exact per-model values and ordering returned by the provider, without translated labels or invented levels. Models without returned effort metadata have a disabled selector. An unspecified effort omits the parameter; defaults are used only when returned by the provider. [Test evidence](test-reports/effort-2026-10-21/README.md) includes sanitized metadata and wire-parameter checks.

调试连接：打开“Agent 管理”，点击对应 Agent 的“调试控制台”。可点击“连接测试”或输入自定义指令；临时模型不修改保存的默认模型或团队配置。最近 8 次调试保存在服务内存中，重启清空，关闭窗口后可重新打开查看运行结果。API 测试会调用对应服务并按服务商规则计费。

合作规则示例：

> 先让两位成员分别提案，不看彼此答复；再互相质疑。出现分歧时，请第三位成员找证据。形成共识后再决定谁实现。监工需要逐项指出哪些要求已经符合、哪些仍待核对。

各协作室的成员、模型与监工设置在下一次开始协作时生效；合作规则修改在当前步骤结束后生效。移出成员只停用，保留连接、密钥、团队模型选择和历史答复，再次加入无需重新输入。允许暂时清空团队，开始任务前需先加入成员。已有协作室会一次性继承迁移时的团队与监工设置，此后互相独立；新建协作室需单独邀请成员。已有加密密钥沿用原文件。

### 能力边界与验证

监工的检查依赖模型判断，不能代替人工核对。完成校验要求存在实际工作者记录、有效证据引用及完整规则检查；最近一次编码失败不能直接宣布交付完成。

ChatGPT 编码使用 workspace-write 沙箱；Claude 使用受限文件工具；API 编码复用有工作区约束的 MCP 执行器。兼容 API 的编码模型还需支持工具调用。连接、模型权限或余额失败会如实显示，不会伪造参与或自动替换身份。

执行契约列明允许文件和验收命令。未运行测试时会明确标记；发现范围外变更会停止协作，但不会自动撤销已经发生的修改。DeepSeek 命令和本机验收会运行项目代码；命令过滤和超时不等于 OS 沙箱，请仅在信任的项目运行自动执行。

已有 45 项 GUI / 调度 / 连接 / 迁移测试和 6 项适配器测试。开发环境中实际验证过监工安排新增 CLI 成员、收到真实答复并核对结束，以及 Claude 对隔离文件的写入。该环境的 ChatGPT 写入曾被本机权限阻断；DeepSeek 和任意外部兼容服务的真实调用仍取决于用户的密钥、权限与服务兼容性。受控替身测试不代表所有服务均已实测。

```powershell
.\.venv\Scripts\python.exe gui\test_gui.py
.\.venv\Scripts\python.exe gui\test_room_settings.py
.\.venv\Scripts\python.exe gui\test_login.py
.\.venv\Scripts\python.exe gui\test_desktop.py
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

Version 0.12 persists workflow facts, numbered evidence, exact proposal versions, user approvals, and pending execution contracts. A proposal change invalidates its old approvals. The supervisor publishes a short public message while control records remain in a separate audit archive. Discussion members still receive original peer messages; the designated executor receives one self-contained contract.

Version 0.12.1 addresses the independent audit: stable fault fingerprints, safe contract continuation after scope restoration, separate button-action provenance, prioritized host evidence with explicit unread segments, calibrated CLI/API budget gates, nested ledger patches, and complete executor handoffs. Finishing a versioned plan requires explicit consensus or a real user decision accepting unresolved votes. See the [audit follow-up](test-reports/token-audit-fixes-2026-10-22/README.md). Full-task token savings remain unverified under the original criteria. The live Claude snapshot probe was blocked by the account's weekly limit; it is recorded as unsuccessful, not a pass.

Version 0.12.2 adds host-generated approval documents with an independent reader, HTML/Markdown downloads and print styles. Approval binds the exact document version and hash. Finished tasks release stale project reservations; when another workflow owns the project, approval is recorded immediately and execution waits in a cancellable queue. See the [approval validation record](test-reports/approval-documents-2026-10-22/README.md).

Version 0.12.3 lets the host initialize a selected project automatically during execution preflight when it is outside any Git repository. The host records the action and rechecks the executor environment. Existing repositories are preserved; nested projects still require their actual root, and damaged metadata or permission errors remain explicit blockers. See the [host Git initialization record](test-reports/host-git-init-2026-10-22/README.md).

Host preflight checks Git, the actual executor configuration, quota availability, and recovery records. Red host errors offer **Recheck and continue**; an unchanged blocker does not trigger another model call for a resume-only request. Room context settings include supervisor state/full mode, editable resume phrases, discussion tolerance, maintenance threshold, and handoff size. Actual delivery intervals and gaps survive retries; private provider histories stay with their original identity. Per-request usage keeps unknown counters unknown and separates cache metrics. Tests and live-call limitations are documented in the [optimization report](test-reports/token-optimization-2026-10-22/README.md).

### Features

- **Two management layers:** a vendor-grouped library retains up to 100 agents and their credentials. Each room independently invites 0–20 members and stores its own models, supervisor, and pause settings. Agent Management maintains connections without membership controls.
- **Shared model directory:** startup and page reload automatically query OpenAI, Claude, Gemini, and DeepSeek metadata in the background. Agent defaults, team members, supervisor, and debug consoles share each connection's directory. Default/debug model pickers are full dropdowns with a separate custom-ID field, without filtering the directory by the current value. Identical connections are coalesced; different credentials stay isolated. Failed refreshes retain the last directory and current choices; other services use manual IDs.
- **Account meters:** team and agent-library cards show ChatGPT / Claude CLI account quota usage, remaining percentages, reset times, and real DeepSeek API balances. Refreshes roughly every minute or on demand, shared by exact account/credential. Gemini and ordinary OpenAI / Claude API credentials without account-balance access show an explicit explanation and official usage/billing links; missing values are never shown as zero.
- **Per-agent debugging:** independent streaming consoles with test prompts, temporary model overrides, visible errors and elapsed time, and cancellation. Agents need not join the team; no project or group-chat context is attached.
- **Multiple transports:** locally authenticated ChatGPT / Claude CLIs, the official DeepSeek API, and custom OpenAI-compatible Chat Completions endpoints.
- **Independent supervisor:** uses any saved agent connection, including agents outside the current team, with its own model; turns your policy into observable checks.
- **Dynamic coordination:** sequential or parallel read-only contributions, independent proposals, critiques, and one writer at a time per project.
- **Live policy changes:** edit the policy or send supplemental instructions to the supervisor. Rechecking occurs after the current step; stale arrangements are discarded.
- **Visible evidence:** actual member responses, model IDs, assignments, policy checks, tool activity, and host-run acceptance command exit codes.
- **Local persistence:** SQLite conversations and events, with saved-agent API credentials encrypted using the current Windows user's DPAPI.
- **Delivery controls:** inspect diffs, execution contracts, and supervisor reports; export chats, stop a run, or pause before file changes.

### Requirements and installation

Windows 10 / 11, **Python 3.12**, Git, and Microsoft Edge WebView2 Runtime are required. Modern Windows installations usually include WebView2. Install and authenticate the relevant official CLI when using ChatGPT or Claude CLI connections. API members require a compatible endpoint, model, and credentials; loopback services can use the no-key option. Editing requires selecting the root of a Git repository. Node.js is also required to run the full test suite.

```powershell
git clone https://github.com/Thodmcniujiao/agent-group-room.git
cd agent-group-room
.\Install.ps1
.\Start-GUI.ps1
```

Use `Install.ps1 -Python 'C:\path\to\python.exe'` if Python 3.12 is not registered with the Windows Python launcher. Installation creates `.venv`, installs pinned dependencies, and adds or updates desktop shortcuts. The shortcut and `Start-GUI.ps1` open an independent pywebview / Windows WebView2 window with a taskbar icon and remembered window size. Repeated launches raise the existing window. The local backend defaults to port 49315, tries subsequent ports when needed, and is reused across launches. External sign-in and billing links open in the default browser.

The internal plugin and state namespace remains `tiered-dev`. User data lives under `~/.local/share/tiered-dev/`, outside the repository. The desktop host reuses existing profiles, encrypted credentials, and SQLite history; its WebView2 profile is stored separately in that directory. Closing the window keeps background tasks running. This version uses the repository's Python environment and is not a portable single-file EXE. For troubleshooting, `Start-GUI.ps1 -Browser` opens the browser UI; native-host logs are in `~/.local/share/tiered-dev/gui/desktop.log`. This migration retains the current interface without adding context menus.

To sign in to ChatGPT or Claude, click its login button in Agent Management and complete authorization in the system browser. The desktop app locates installed CLIs without requiring a terminal first, then automatically updates connection status, models, and account usage after authorization. Missing CLIs, launch failures, authorization errors, and timeouts appear on the corresponding agent card and can be retried.

Existing rooms inherit the previous team and supervisor settings once during migration. Their settings are then independent; newly created rooms require their own invitations.

### Using the room

1. Open Agent Management to save connections, credentials, and default models, grouped by vendor. Every key field names its agent.
2. Create a room and select a local project. New rooms start without invited members.
3. Open Room Info in the chat header. Use ＋ to invite saved agents, − to remove members, and click an avatar to set its model in this room. Membership, models, supervisor, and pause settings are independent between rooms; connections and credentials stay in the shared agent library.
4. Write your collaboration policy and choose the supervisor's connection and model.
5. Start a supervised task, or use free chat to address members directly.

The model directory refreshes automatically on page load and can also be refreshed with the roster's ↻ button. Status and failures appear beside the selectors. Saved choices remain unchanged. ChatGPT and Claude use installed CLI metadata; API connections read official model endpoints without generating a conversation turn. Directory entries do not prove inference access to every model.

To debug a connection, open Agent Management and click the agent's Debug Console. Use Connection Test or enter a prompt. Temporary model choices do not alter library defaults or the current team. The most recent eight tests are held in service memory and cleared on restart; close and reopen the console to view a running test. API tests call the selected service and follow that provider's billing rules.

Example policy:

> Have two members propose solutions independently without seeing each other's replies. Then ask them to critique one another. If they disagree, ask a third member to find evidence. Choose an implementer after agreement. The supervisor must show which requirements are satisfied and which remain unverified.

Roster and model changes apply to the next run; policy changes apply after the current step. Removing a member retains its saved connection, credentials, team model selection, and previous messages. Rejoining needs no credentials. An empty team is allowed during setup; add members before starting a task. Older configurations migrate automatically with a backup, preserving existing encrypted credential files.

### Limits and verification

Supervisor judgments are model-generated and should remain reviewable by a person. Completion checks require real worker records, valid evidence references, and checks for every policy rule. A failed latest implementation cannot be reported as a successful delivery.

ChatGPT writing uses the workspace-write sandbox; Claude writing uses restricted file tools; API writing uses a workspace-scoped MCP executor and requires tool-call support. Connection, model, and quota errors are reported rather than replaced with fabricated participation.

Contracts specify allowed files and acceptance commands. Missing tests are identified as untested. Out-of-scope changes stop collaboration without automatically undoing changes. The API executor and acceptance commands run project code; command filtering and timeouts are not an OS sandbox. Run automatic execution only in trusted projects.

The project has 45 GUI / orchestration / connection / migration tests and 6 adapter tests. Development checks included real supervisor coordination of a newly added CLI member and an isolated Claude file write. ChatGPT writing was blocked by the development machine's permissions. DeepSeek and other remote endpoints still require valid user credentials and compatible services. Controlled fixtures are not evidence that every external provider has been tested.

```powershell
.\.venv\Scripts\python.exe gui\test_gui.py
.\.venv\Scripts\python.exe gui\test_room_settings.py
.\.venv\Scripts\python.exe gui\test_login.py
.\.venv\Scripts\python.exe gui\test_desktop.py
.\.venv\Scripts\python.exe scripts\test_deployment.py
```

The optional Claude Code plugin lives in `plugins/tiered-dev`. Activate `.venv` before starting Claude Code so the plugin's `python` command resolves to the installed environment. The GUI does not require installing the plugin.

### Attribution and licensing

The UI, configurable roster, supervisor orchestration, and Windows adapters are project-specific code. The coding executor reuses [deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent), licensed under MIT and pinned in [sources.lock.json](sources.lock.json).

MCP Python SDK is MIT; OpenAI Python SDK is Apache-2.0; Starlette, Uvicorn, and HTTPX are BSD-3-Clause. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency notices and license texts. Agent Room and codex-review-server were inspected for architecture only; their application implementations are not integrated. Codex CLI and Claude Code are separately installed tools governed by their respective licenses and service terms.

**No open-source license has yet been selected for the project-specific code.** Publishing source on GitHub does not automatically apply an upstream dependency's MIT license to this repository. See [LICENSE_STATUS.md](LICENSE_STATUS.md).


## 上下文与执行者 / Context and executor (v0.10.0)

每个协作室可在“协作室信息 → 此协作室的执行者”中选择执行者，默认优先选 DeepSeek，也能换成其他成员。讨论成员直接接收本群原始消息，并可按消息 ID 读取和搜索旧原文；执行者只接收监工整理的执行契约，保留自己的执行历史。原文完整归档，超出活动预算时明确记录未加载范围，各家会话按其原生协议续接。

点击成员旁的“群原文共享 / 仅监工契约”可查看实际投递记录、执行要求及用量。角色、模型、账户和协作室之间使用独立会话。ChatGPT 编码通过宿主契约文件工具写入，其原生进程保持只读；其他提供方保留各自受限的执行入口。API 预检为估算，不承诺无限上下文或免费历史。

Choose an executor under Room Info. DeepSeek is preferred by default; any room member can be selected. Discussion members receive original room messages directly, with paginated lookup and search. The executor receives only supervisor-authored execution contracts and retains its own execution history. Sessions are isolated by room, identity, role, model and connection. Full public originals are retained independently of the active context budget.

Open each member's context badge to inspect delivery records and execution contracts. ChatGPT writes through host tools that enforce the contract's allowed paths while its native process remains read-only. Context estimates and provider-reported usage are shown separately. Archived tests include isolated live provider calls, fixture screenshots, failures and retests; live checks consume provider usage.

- [上下文调查与计划 / Research and plan](docs/context-sharing-plan.zh-CN.md)
- [测试档案 / Test archive](test-reports/context-2026-10-21/README.md)


## 协作室上下文与头像 / Room context controls and avatars (v0.11.0)

每个协作室顶部新增“上下文设置”，独立配置上下文软预算、输出预留/API 输出上限、安全余量、工具续轮上限、监工轮数和历史溢出处理。完整请求会计入系统提示、JSON 转义与工具定义；未自动装入的群原文仍可按消息 ID 读取。

超过本地预算时，当前请求等待操作。输入框右侧“超预算继续”恢复同一个等待请求，并仅为本次协作忽略本地软上限；调大预算并保存也能恢复符合预算的等待请求。服务重启后，未完成任务按中断处理；再次点击该按钮会让监工核对已有文件和执行记录后继续任务，不会恢复已消失的进程。

用户消息和头像靠右、成员靠左。Agent 管理中的“设置所有头像”支持你、独立监工和每个已保存 Agent，上传 PNG/JPEG/WebP，居中裁切为方形，并可恢复默认。头像按身份保存在本机，与成员入群/退群和密钥分离。

Each room has independent context, output reserve, safety margin, tool-round, supervisor-round and overflow controls. Local budget pauses retain the pending request. The composer’s override button resumes it and bypasses local soft limits for that collaboration only; provider limits still apply. Increasing the room budget can also release a pending request. After a service restart, continuation checks prior work in a new run rather than restoring a lost process.

User bubbles align right and agent bubbles align left. Agent management supports persistent local raster avatars for the user, supervisor and every saved agent, with upload and reset controls.

测试留档 / Test evidence: [room-controls-2026-10-22](test-reports/room-controls-2026-10-22/README.md).


左下角月亮/太阳按钮可一键切换深色与浅色主题；首次跟随系统，手动选择后在本机记住。聊天、侧栏、输入框与设置窗口同步切换。The lower-left moon/sun button toggles light and dark themes and remembers the choice locally. See [theme checks](test-reports/theme-2026-10-22/README.md).
