# DeepSeek 配置所有者修复验证

验证时间：2026-10-22（Europe/London；JSON 中保留 UTC 时间）。

## 问题与修复

执行器读取 `C:\Users\Thodmc\.deepseek-mcp\config.json` 时被原有安全检查拒绝，提示 `Windows path has an unsafe owner or DACL`。

修复前，该目录和文件的所有者 SID 都缺少当前账户的 `-1001` 后缀，与当前用户 SID 不一致。原有 DACL 仅授予当前用户、SYSTEM 和 Administrators 完全控制，未发现额外写入授权。

仅将上述目录和配置文件的所有者改为当前用户。没有递归修改其他文件，没有禁用安全校验；DACL、继承状态和配置文件 SHA-256 均保持原样。

## 验证结果

| 检查 | 修复前 | 修复后 |
| --- | --- | --- |
| 原版安全目录检查 | 拒绝：所有者不匹配 | 通过 |
| 原版安全文件检查 | 拒绝：所有者不匹配 | 通过 |
| 原版配置安全读取及 JSON 解析 | 拒绝 | 通过 |
| 应用执行器实际配置加载 | 未运行 | 通过，使用已保存凭据和原项目路径 |
| 应用实际 MCP 启动、工具发现与 ping | 未运行 | 通过，发现 11 个工具，mode=auto，无 NOT_CONFIGURED |

共 5 项修复后检查通过。MCP 使用应用中的 `engines.deepseek_session` 启动，仅调用 `ping`；未派发任务或调用模型生成，因此这些验证不消耗生成 token。此记录证明权限与启动故障已消除，不代表原有编程任务已经完成。

## 留档与复查

- [修复前验证](before.json)、[修复后验证](after.json)：真实检查结果。
- [修复前 ACL](acl-before.json)、[修复后 ACL](acl-after.json)：所有者、DACL 和配置哈希对照，不包含配置内容或密钥。
- [验证脚本](check_config.py)：只检查配置和 MCP 健康状态，不执行编码任务。
- 原始权限备份：`C:\Users\Thodmc\.local\share\tiered-dev\backups\deepseek-config-owner-20261021-233103Z\acl-before.json`。

运行下列命令可以复查当前状态；它会更新 `after.json`，不会再次修改 ACL：

```powershell
.venv\Scripts\python.exe test-reports\deepseek-config-acl-2026-10-22\check_config.py --phase after --workspace 'C:\Users\Thodmc\OneDrive\Desktop\New folder'
```

修复立即对新启动的执行器生效，应用后端保持运行。原协作中的历史错误提示会保留，可在原协作发送“继续执行”重新发起执行。
