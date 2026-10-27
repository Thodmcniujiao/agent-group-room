# 更新日志

## 未发布

- 重新整理界面：右上角只保留成员头像、变更与报告和“⋯”菜单；协作室信息、合作规则、上下文合并为带标签页的“协作室设置”；导出移入“变更与报告”；Agent 管理、Google 渠道与余额、头像设置收进左下角“设置”。左侧新增协作分组（自定义名称与协作文件夹）、归档与删除，统一右键菜单支持键盘与拖拽移动。
- 恢复检查可按当前用户批准范围及上次执行的文件哈希，确认已成功提交的写入；不再让可核实记录永久阻挡复查。PowerShell 文件在原子写入前统一为 UTF-8 BOM，兼容 Windows PowerShell 5.1；已有用户批准的方案不会在恢复后被讨论上限逻辑重新送审。

- 余额查询失败时复用同账号上次成功缓存，保留真实确认时间；超过 30 分钟在应用内提醒。无可用缓存时由宿主等待，不再消耗监工轮数；部分加载的账单记录不会刷新旧缓存时间，实际到期和新确认的零余额仍会生效。
- 达到讨论容忍度上限后，宿主强制由最后明确反对的在场成员提交完整最终方案，直接生成审批文档交用户决定；不再开启额外争论，不自动批准。连接故障会暂停等待，重启可复用已完成的最终方案。

## v0.1.2 · 2026-10-24

### 主要变化

- **独立 Agent 实例**：同一个 Agent 可重复加入协作室；每个实例可分别设置名字、头像、模型和推理强度，拥有独立会话，共用来源账户连接与余额。
- **Gemini / Vertex 双渠道**：整合两种调用方式，按可用赠金和预付余额选择渠道；配套浏览器扩展同步 Google Cloud 与 AI Studio 账单页读数。
- **持久协作与审批**：保存方案版本、投票、批准和执行契约；加入正式审批文档、项目排队、宿主 Git 初始化及故障复查。
- **上下文与界面**：增加可选摘要、工具输出归档与回取、上下文预算、协作日志、账户读数、主题切换和头像设置。
- **可选语音工作台**：发布 Voice Studio 源码、角色预设、示范音频和部署脚本；Edge TTS 需联网，GPT-SoVITS 引擎与模型另行下载。

### 引用项目

- [deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent)：编码执行器，固定版本见 `sources.lock.json`。
- [pywebview](https://github.com/r0x0r/pywebview)：Windows 桌面窗口。
- [edge-tts](https://github.com/rany2/edge-tts)、[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)：可选语音模块的在线 TTS 与本机推理引擎。
- [codex-review-server](https://github.com/bdouble/codex-review-server)、[Agent Room](https://github.com/agent-room-alkl/agent-room)：审阅桥接与协作架构参考；引用范围及许可记录见 [第三方说明](THIRD_PARTY_NOTICES.md)。

### 验证

完整离线回归 **284 项通过、2 项跳过，0 失败**，余额解析另有 13 项断言通过，见 [发布测试](test-reports/release-0.1.2/unit-tests.json)。Agent 实例已验证重复添加、独立改名和头像、刷新保留及单独移除；Google 双渠道与真实余额同步的验证范围见 [记录](test-reports/google-billing-2026-10-24/README.md)。账单读数存在延迟，不构成严格扣费上限；语音模型不包含在主应用的离线回归中。
