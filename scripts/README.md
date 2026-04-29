# scripts/

运维脚本。每个脚本必须：
- 顶部带说明（用途、风险、回滚）
- 危险操作前 `read -p` 二次确认
- 用 `set -euo pipefail`
- 永不带 `--volumes` flag（除非脚本名带 `wipe`）

**计划脚本**：

| 脚本 | 用途 | 风险 |
|---|---|---|
| `dev-up.sh` | 一键启开发栈 | 无（仅本机 docker） |
| `dev-down.sh` | 停开发栈（不删卷） | 无 |
| `prod-deploy.sh` | 生产部署，含 pre-check + backup + rolling update | 中（动生产，每步确认） |
| `backup-volumes.sh` | 打包所有 named volume 为 tar.gz | 无（只读） |
| `restore-volume.sh` | 从 tarball 恢复指定卷 | 高（覆盖数据，需确认） |

**待实现**：v0.1（dev-up/down），v0.5+（prod 部署）
