# 代理协作室 · Agent Group Room（便携版 / Portable）

解压整个文件夹，双击 `AgentGroupRoom.exe` 即可运行，无需安装 Python。

- **数据都在本文件夹的 `data/` 里**：协作室、群聊、偏好、头像和日志。复制或备份整个文件夹即可迁移；删除文件夹即完全移除。
- **密钥**仍用 Windows 当前用户加密（DPAPI）。换电脑或换 Windows 用户后，需要在“设置 › Agent 管理”重新填写 API 密钥。
- **ChatGPT / Claude 成员**依赖本机已安装并登录的 Codex / Claude CLI，登录信息保存在这些 CLI 自己的用户目录，不在便携包里。修改项目文件需要本机安装 Git。
- 桌面窗口使用 Microsoft Edge WebView2（Windows 10/11 通常已自带）。`AgentGroupRoom.exe --browser` 改用系统浏览器打开。
- 便携版使用 49335 起的本机端口，和安装版（49315 起）互不干扰，可以同时运行。
- 放在需要管理员权限的目录（如 `C:\Program Files`）会无法写入 `data/`，请放在桌面、文档或 U 盘等可写位置。

Extract the whole folder and run `AgentGroupRoom.exe`. All app data lives in `data/` beside it. API keys are encrypted for the current Windows user, so re-enter them after moving to another machine or account. ChatGPT / Claude members need the Codex / Claude CLIs installed and signed in on this machine.

第三方组件及许可见 `app/THIRD_PARTY_NOTICES.md` 与 `app/third_party/licenses/`；Python 运行时许可见 `runtime/LICENSE.txt`。
