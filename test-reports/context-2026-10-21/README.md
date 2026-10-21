# 上下文改造测试档案

日期：2026-10-21（Europe/London）。当前状态：验收通过，桌面版 v0.10.0 已部署并重新打开。

此目录保存可复查的原始测试结果，不含 API 密钥或正式群聊内容。真实调用使用临时群与临时项目，界面截图使用明确标记的测试夹具。

## 当前结果

- 自动回归：95 项通过，0 失败、0 错误、0 跳过。[逐项日志](unit-tests.log)、[逐项 JSON 与源码 SHA-256](unit-tests.json)。其中包括原文完整性、角色/群隔离、失败恢复、签名往返，以及宿主文件工具的越界拒绝与精确编辑。
- 四家真实调用：均通过两轮编号回忆，检查讨论原文投递及执行者不接收原文。[结果](live-chat.json)、[原始日志](live-chat.log)。
- DeepSeek / Gemini 执行历史：均通过“读取文件后，启动新的执行进程回忆编号”。[结果](live-coding.json)、[原始日志](live-coding.log)。
- 四家真实写入：最终全部通过，均只修改临时项目的 result.txt，并由宿主运行 node verify.js，退出码均为 0。[最终汇总及各次证据来源](live-write.json)、[首次四家结果](live-write-initial.json)、[ChatGPT 修复后复测](write-chatgpt-final.json)。最终汇总来自注明来源的验证轮次，不冒充同一次运行。

## 失败与修复记录

1. ChatGPT 的原生文件写入两次返回 Failed to write file，未生成目标文件；宿主 Node 验收准确返回 1。模型也报告了 STATUS: BLOCKED，没有被框架当成成功。[首次日志](live-write-initial.log)、[诊断](write-chatgpt-diagnostic.json)。
2. 显式指定原生可写根目录后仍失败，保留[该次复测](write-chatgpt-retest.json)。底层原生工具失败原因没有进一步确认。
3. 最终使用宿主契约文件工具：每次 Write / Edit 都校验允许文件，复用已固定版本的安全文件实现；ChatGPT 原生进程保持 read-only，不改为全盘写入。修复后实际创建文件并通过验收。[最终结果](write-chatgpt-final.json)、[原始日志](write-chatgpt-final.log)。
4. 界面检查发现初次加载和 SSE 重放会重复显示投递记录，现已按 delivery_id 去重；两次投递事件分别表示“准备”和“完成”，不是重复推理。

## 界面证据

- [协作室执行者选择](ui-executor.png)：默认 DeepSeek；可切换到 Gemini，刷新后选择保留。
- [执行契约投递界面](ui-contract.png)：展示执行者收到的契约，群原文数量为零。
- [讨论成员原文界面](ui-originals.png)：直接显示 ChatGPT 原始提案及其消息 ID，不经过监工转述。

界面操作检查：默认 DeepSeek、改选 Gemini、刷新后保留、切回 DeepSeek、查看契约、查看讨论原文及 SSE 去重均通过。截图来自浏览器承载的同一套桌面界面；独立桌面宿主另由回归测试覆盖。部署后已确认桌面宿主进程运行、后端 v0.10.0 健康，启动日志无新增错误。

## 部署与数据保留

[部署核对 JSON](deployment.json) 记录：原有 3 个协作室、29 条消息正文及状态全部保留；各群成员/模型/监工/暂停设置保留，Agent 库与加密凭据字节保持一致；既有群默认执行者为 DeepSeek。源码哈希与自动测试档案一致。

部署前有两次本地备份；最近一次的地址见[备份位置](deployment-backup.txt)。备份内容位于用户本机状态目录，不放入仓库。临时界面服务与浏览器验证会话已清理，正式桌面服务继续运行。

## 复现命令

在项目根目录运行：

~~~powershell
.\.venv\Scripts\python.exe scripts\run_context_checks.py
.\.venv\Scripts\python.exe scripts\smoke_context.py --live --report test-reports/context-2026-10-21/live-chat.json
.\.venv\Scripts\python.exe scripts\smoke_context.py --live --mode coding --report test-reports/context-2026-10-21/live-coding.json
.\.venv\Scripts\python.exe scripts\smoke_context.py --live --mode write --report test-reports/context-2026-10-21/live-write.json
~~~

第一条不调用模型；带 --live 的命令会使用已配置账户进行实际调用并消耗额度。可以加 --agent chatgpt 等参数只测一家。

源码存在此前未提交的本地改动，不能仅用 Git HEAD 指认测试版本；unit-tests.json 记录参与验证文件的 SHA-256。首次失败记录保留，不用成功结果覆盖。

## 验证边界

真实模型测试覆盖短轮次、实际工具调用、跨进程续接和四家写入验收。超过旧截断门槛、跨群隔离、超预算换分支、签名完整性等用离线夹具验证；没有为了压力测试向各家发送百万 token 的长请求。token 预检仍明确标作估算，原生返回的用量单独记录。旧版已经截断的数据无法凭空恢复。
