# Git 通路与额度可见性：测试记录

日期：2026-10-21（Europe/London）。

## 已完成（Linux 隔离环境，非本机 Windows）

用同一份源码和已固定的 deepseek_mcp 上游包（openai / re2 以空壳替代，不发网络请求）运行：

- `gui/test_git_bridge.py`：11 项全部通过。其中 2 项用一个属主为其他账户的真实仓库复现 `dubious ownership`：
  不带宿主配置时 `git status` 退出码 128；经宿主（run_host）、执行者 Git 工具、执行者 Bash 内的 git 均退出码 0；
  另一个未选中的仓库仍被拒绝；用户全局 Git 配置未被修改。
- `test_gui.py` 中的 WorkflowTests / SupervisorValidationTests / RecoveryTests：全部通过（含真实 git 与验收命令的监工全流程）。
- 其余失败均为该环境缺少 Windows 依赖（DPAPI、pywin32、真实 httpx 等），与本次改动无关，需在本机复核。

## 待在本机 Windows 复核

双击或在“运行”中执行本目录的 `run.cmd`。它会写入：

- `git-version.txt`：Git 版本（需 ≥ 2.35.2）
- `plain-git-new-folder.txt`：未经宿主时 `New folder` 的原始 Git 报错
- `git-bridge.log`：新测试（含对 `New folder` 的只读真实验证）
- `unit-tests.log` / `unit-tests.json` / `full-suite.txt`：完整回归
