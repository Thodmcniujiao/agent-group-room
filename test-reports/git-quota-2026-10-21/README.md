# Git 通路与额度可见性：测试记录

日期：2026-10-21（Europe/London）。

## 已完成（Linux 隔离环境，非本机 Windows）

用同一份源码和已固定的 deepseek_mcp 上游包（openai / re2 以空壳替代，不发网络请求）运行：

- `gui/test_git_bridge.py`：11 项全部通过。其中 2 项用一个属主为其他账户的真实仓库复现 `dubious ownership`：
  不带宿主配置时 `git status` 退出码 128；经宿主（run_host）、执行者 Git 工具、执行者 Bash 内的 git 均退出码 0；
  另一个未选中的仓库仍被拒绝；用户全局 Git 配置未被修改。
- `test_gui.py` 中的 WorkflowTests / SupervisorValidationTests / RecoveryTests：全部通过（含真实 git 与验收命令的监工全流程）。
- 其余失败均为该环境缺少 Windows 依赖（DPAPI、pywin32、真实 httpx 等），与本次改动无关，需在本机复核。

## 本机 Windows 结果（2026-10-21 17:46 UTC）

- Git：`git version 2.52.0.windows.1`（支持宿主信任文件）。
- 完整回归（`unit-tests.log` / `unit-tests.json`）：117 项中 116 项通过。唯一失败的是
  `test_real_dubious_ownership_repository_is_trusted_only_through_the_host`：它要求 `New folder`
  先被普通 `git status` 拒绝，但这次普通 Git 已经正常（`plain-git-new-folder.txt` 只有 `?? .tiered/`），前提不成立。
  该测试已改为前提不满足时跳过；在 Linux 环境用真实的所有权冲突仓库复测仍通过。
- 执行者 Bash 内的 git、Git 工具在 `New folder` 上均通过（`git-bridge.log`）。

重新运行：执行本目录的 `run.cmd`。

## 第三次本机运行（2026-10-21 22:39 UTC）

- 完整回归：118 项，0 失败，1 项跳过（`New folder` 已无所有权冲突，前提不成立）。
- Codex 0.160 真实沙箱（`codex sandbox -- git rev-parse --show-toplevel`，受限令牌，不调用模型）：
  不带信任、带环境变量信任、带 `shell_environment_policy.set` 三种情况均退出码 0，输出 `C:/Users/Thodmc/OneDrive/Desktop/New folder`。
  即：在本脚本的环境中，协作室截图里的 dubious ownership 已无法复现；宿主内的实际表现需在重启后的协作室中确认。
