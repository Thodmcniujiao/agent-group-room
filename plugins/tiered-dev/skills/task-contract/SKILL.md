---
name: task-contract
description: 编写 tiered-dev 的 DeepSeek 执行契约，明确任务范围、允许路径、验收命令和简短回报。
---

将下面模板写入 `.tiered/tasks/T-NN.md`，填入实际值。四个必需标题的内容不能为空。每份契约一个可验证目标，尽量不超过 15 个文件；不确定需要扩大范围时停止并回报。只读任务也保留“允许修改”，内容写“无；只读”。不要贴密钥或无关源码。

```markdown
# T-01 具体任务
## 目标
明确行为、输入、输出和边界条件。
## 背景
相关路径、依赖、接口结论和必要项目规则。
## 允许修改
- src/example.py
- tests/test_example.py
不得修改其他文件；需要扩范围则返回 BLOCKED。
## 验收命令
- python -m unittest discover -s tests
每条必须退出 0。Windows 的 DeepSeek Bash 实际使用 cmd.exe；命令须适合该环境，或显式调用 powershell.exe -NoProfile -File 项目脚本。
## 禁止
不安装依赖，不改代理/密钥/模型配置，不 commit/push/发布，不访问工作区外路径。
## 回传格式
不超过 20 行：
STATUS: DONE | BLOCKED
FILES: 修改路径
TESTS: 实际命令及退出码；未跑则写明
DECISIONS: 最多 3 条
QUESTIONS: none 或需要上游决定的事项
```
