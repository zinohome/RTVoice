# livekit/

LiveKit server 配置文件目录。

**计划文件**：
- `livekit.dev.yaml` — 开发配置（绑 127.0.0.1，无 TLS，简单 keys）
- `livekit.prod.yaml` — 生产配置（用户决定绑定，可选 TLS）

**镜像**：`livekit/livekit-server:v1.7.2`（pin 版本，见 [SECURITY.md §2.2](../SECURITY.md)）

**待实现**：v0.1
