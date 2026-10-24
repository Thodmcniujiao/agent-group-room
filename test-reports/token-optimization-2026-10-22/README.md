# Token 优化实施与测试留档

本目录对应用户批准的 `plan.md`（现保存在 `Plan_documents/Agent Group Room token 优化实施方案.md`）。原批准文件的副本为 [approved-plan.md](approved-plan.md)，两份文件的 SHA-256 一致。应用版本为 **0.12.0**。

## 实现范围

| 方案项 | 已实现行为 | 主要模块 |
| --- | --- | --- |
| A1 | 每次服务商请求记录用量；Codex 累计计数取增量，重复通知不重复计费统计；失败、缺失值保持未知；缓存和推理另列 | `usage.py`、`provider_sessions.py`、`execution_context.py`、`store.py` |
| A2 | 持久目标、规则、证据、版本、批准、待执行契约；并发更新检查 revision；旧协作室证据编号连续 | `workflow_state.py`、`coordination.py`、`store.py` |
| A3 | 宿主与执行子环境分别检查 Git，真实加载执行器配置和恢复记录；同一阻断仅复查时不调用模型；有效原契约可续接 | `preflight.py`、`coordination.py` |
| B1 | 群聊仅发布监工公开安排，控制 JSON 单独归档；完整原始返回可审计 | `supervision.py`、`provider_sessions.py`、界面控制记录 |
| B2 | 成员原文入方案库，用户原文或批准按钮产生真实批准，绑定方案版本和哈希 | `workflow_state.py`、`store.py`、批准接口 |
| B3 | 宿主展开自足执行契约一次；执行者不收群聊、没有群消息工具 | `orchestrator.py`、`context.py` |
| B4 | 提案、评审、表态、澄清按需组包；表态不附带项目快照 | `coordination.py` |
| B5 | Git、文件、执行器启动及已批准验收由宿主检查；验收序号指向原始命令，不重写引号 | `coordination.py`、`host_commands.py` |
| B6 | 明确格式表态、按版本记录、最多补问一次、讨论容忍度；连接启动失败不消耗讨论轮数 | `coordination.py`、`workflow_state.py` |
| C1 | 监工账本和增量补丁；所有协作室缺省 `state`，可切换 `full` | `supervision.py`、`room_settings.py` |
| C2 | 实际投递区间、缺口和分段覆盖；完整读完才标记已投递 | `delivery.py`、`context.py`、`room_tools.py` |
| C3 | 稳定角色指令每分支发送一次；指令哈希改变触发交接 | `context.py`、`provider_sessions.py` |
| D1 | 原生压缩或公开交接包；维护阈值、近期原文条数可配置；私有历史留在原分支 | `context.py`、`provider_sessions.py` |
| D2 | 中文估算与影子校准；连续 20 次误差不超过 20% 才影响预算拦截 | `calibration.py` |
| D3 | 流式增量更新当前消息及内存事件，终态再形成持久事件 | `store.py`、SSE 接口 |

新增设置在协作室顶部的“上下文设置”；宿主错误显示红色消息和“复查并继续”；批准绑定当前版本；审计入口为“变更与报告 → 工作流 / 用量 / 原始控制返回”。

## 回归结果

- 修改前实际基线：[baseline/unit-tests.json](baseline/unit-tests.json)，130 项，其中 128 通过、2 跳过。
- 最终发布验证：[release/unit-tests.json](release/unit-tests.json)，**162 项，其中 160 通过、2 跳过，0 失败、0 错误**；[完整日志](release/unit-tests.log)及源码 SHA-256 清单随结果保存。
- `node --check` 检查两个修改的 JavaScript 模块；Python `compileall` 与 `git diff --check` 均通过。
- 两项跳过均需要一个在当前机器上确实触发 Git dubious ownership 的仓库。本机没有满足该条件的夹具；普通 Git/执行子环境检查另已真实通过，不将跳过项记作通过。
- 早期集成失败、修复前结果和最终结果分别保留在 `integration-*`、`final*`、`release` 中。最后一次回归还修复了旧测试子进程在 Windows 上按 GBK 解码 UTF-8 输出的问题，`release` 运行无该线程异常。

新增用例覆盖相同阻断零模型调用、修复后原契约续接、跨重启证据编号、并发状态保存、版本/批准失效、公开控制分离、成员原文和执行者隔离、投递缺口及分段读取、指令变更交接、用量增量/失败/重放、校准门槛、流事件、CSRF、过期批准按钮和契约外变更阻断。

执行器异常不能被改成成功：只有宿主**实际跑完该契约全部已批准验收**、全部退出码为 0，且方案和文件哈希仍相同时，才能产生独立 `verifies_execution` 证据；部分测试、旧代码测试或尚未恢复的契约外变更均不能结束任务。

## 真实运行与效果边界

### 同条件监工对比

[paired-supervisor/comparison.json](paired-supervisor/comparison.json) 保存了同任务、同 Gemini 适配器、同模型、同 `high` 推理强度下的 **5 轮只读监工决策**：

| 指标 | 原组包 | 优化组包 |
| --- | ---: | ---: |
| 输入 token | 34,144 | 16,647 |
| 输出 token | 1,856 | 1,388 |
| 协议核验 | 通过 | 通过 |

该局部对比的输入减少 **51.2%**。这是当时源码快照的监工协议/组包实验，不是完整任务节省率，也不证明每个模型或每种任务都能达到此比例。缓存用量单列，不合并为输入节省。

### 四方完整隔离任务

[acceptance/live-task.json](acceptance/live-task.json) 保存原 New folder 需求在临时 Git 项目里的真实运行。ChatGPT、Claude、Gemini、DeepSeek 使用保存的实际模型和 `high` 强度。测试用户的所有确认/修复提示均标为 `fixture_only`，未用于真实用户协作室的批准。

实际产物包含四个约定源文件。宿主完整验收证据 **E15**：

1. `cmd.exe /d /c DesktopIconToggle\build.cmd`，退出码 0，实际生成可执行文件。
2. `DesktopIconToggle\bin\DesktopIconToggle.exe --check`，退出码 0，返回 `OK (read-only, nothing modified)`。

最后一次 DeepSeek 调用因工具轮数及待审写入异常退出，**E13 仍为 failed**。夹具核对并归档最终文件及恢复记录后，宿主 E15 独立核实当前交付，工作流结束；没有为获得一句口头成功再运行执行者。早期契约外 `_probe.cmd`、输出截断、批处理故障、引号错误和多次恢复均保留记录。

早期报告判断器只接受执行器正常退出，错误地把这次宿主独立验收判为失败；已按真实证据修正，原判断保留在 `live-task-before-evaluator-fix.json`，没有为修正报告追加模型调用。

这个完整任务包含开发期间多次修复、重启和恢复，累计用量**不低于历史基线，不能作为节省证明**。方案中“完整任务输入、输出及无效请求均低于同任务基线”的效果目标尚未得到同条件实验验证；本次只能确认流程、原始失败保留与实际构建/只读检查证据。未在真实系统安装该测试工具、注册自启动或切换桌面图标。

### 本机连接与预检

- [claude-live/result.json](claude-live/result.json)：实际 Claude CLI 联通并回传用量。使用已安装 CLI `--help` 返回的原生 autocompact 下限 100,000（100k–1M），没有编造低于该能力的阈值；阈值不足时改用公开交接。
- [live-preflight-final.json](live-preflight-final.json)：原 New folder 的宿主 Git、执行子环境 Git、实际执行器配置、恢复记录均通过；输出上限实际为 16,384。额度没有读数时明确保留未知，不当作耗尽。预检无模型生成调用。
- [executor-state-acl.json](executor-state-acl.json)：修复了 5 个已有执行状态路径的旧 owner SID，保留原 DACL 与文件字节；没有广泛重设权限，也没有绕过安全检查。

服务商字段核对使用 [Gemini Interactions API](https://ai.google.dev/api/interactions-api-v1) 与 [Anthropic Messages API](https://platform.claude.com/docs/en/api/http/messages)。缺失计数保留 `null`，原生私有签名及思考块不导出到公共测试报告。

## 界面验证

[ui-verification.json](ui-verification.json) 为独立 UI 夹具的实际 DOM 检查：完整状态模式、续接短语、容忍度 4、维护阈值 0.75、交接原文 4 条保存后仍存在；重复环境故障保持零模型调用；红色错误样式、独立控制记录、无水平溢出。

- [深色设置](settings-dark.png) / [浅色设置](settings-light.png)
- [深色宿主错误](host-error-dark.png) / [浅色宿主错误](host-error-light.png)

## 本机更新状态

源码已更新为 0.12.0，原桌面快捷方式仍指向本仓库的 `gui/launch.py`。[deployment.json](deployment.json) 留存了启动前备份位置、5 个原协作室、133 条原消息及配置/加密凭据的哈希；备份在用户数据目录，不在仓库。

**桌面启动尚待手动完成**：本次命令启动被自动审批拒绝，只返回 `blocked by policy`，未给出具体原因。未尝试绕过该限制，不能声称新版已在桌面运行。请双击“代理协作室”快捷方式加载新版。此次没有提交 Git commit，也没有推送 GitHub。

## 复现与隐私

```powershell
.venv\Scripts\python.exe scripts/run_context_checks.py --report-dir test-reports/my-regression
```

普通回归不调用模型。`benchmark_supervisor_payload.py --live` 和 `smoke_token_optimization.py --live` 为显式真实调用，会使用账户额度，不应为了重复查看结果而重新运行。

包含原生工具历史、私有推理/签名的 `state/`、`live-state/`、SQLite、JSONL 和日志由 `.gitignore` 排除。保留的公共 JSON 仅含公开消息、控制事实、测试证据及用量元数据；没有导出 API 密钥。测试报告不是保证未来任务成功或固定节省量的承诺。
