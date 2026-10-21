# 深浅主题切换检查

2026-10-22（Europe/London）。已更新桌面界面，后端保持运行。本次只修改前端主题与左下角按钮，没有请求真实模型生成。

- 左下角显示月亮或太阳。图标表示点击后的目标主题；按钮的辅助名称、提示和按下状态一起更新。
- 鼠标点击、键盘 Enter 与 Space 均可切换。深浅两种选择分别检查了刷新后保留：[浅色](light-after-reload.json)、[深色](dark-after-reload.json)、[Space 切换](keyboard-space-state.json)。首次没有手动选择时跟随系统主题；手动选择保存在本机浏览器/桌面 WebView 的本地存储中。
- 主题在样式加载前恢复；聊天、侧栏、输入框、模型选择器和弹窗共用主题颜色。检查了上下文设置和 Agent 管理，并读取输入框、下拉框的实际 `color-scheme`：[弹窗颜色](dialog-colors.json)。
- 检查的深色正文、成员标题和说明文字对比度最低 **7.56:1**，见 [采样结果](dark-contrast.json)。这是抽样检查，不代表对整页做了无障碍认证。
- 在桌面最小宽度 1024 px 核对左下角按钮位置、图标显隐和横向宽度，没有横向溢出：[布局记录](footer-layout.json)。浏览器未报告脚本错误。
- `node --check gui/web/theme.js` 通过。纯前端变更采用实际浏览器交互与渲染检查，没有重复执行无关的后端回归用例。
- 部署只重新打开桌面窗口，没有重启后端。后端 PID 前后一致，原有 **4 个协作室、102 条消息**及状态未变；配置、头像和加密密钥文件哈希一致。线上资源字节与本地文件一致，检查及源码哈希见 [verification.json](verification.json)。

截图：[深色群聊](dark-room.png)、[浅色群聊](light-room.png)、[深色上下文设置](dark-context-settings.png)、[深色 Agent 管理](dark-agent-management.png)。图片与交互检查均使用隔离数据目录，未记录实际用户聊天或头像。
