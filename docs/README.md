# docs/

RTVoice 文档目录。

## 核心文档

| 文档 | 说明 |
|------|------|
| [使用说明](quickstart.md) | 快速上手、Admin Console、API 调用示例、常见问题 |
| [../deployment/README.md](../deployment/README.md) | 一键部署指南（面向运维） |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | 系统架构设计 |
| [../SECURITY.md](../SECURITY.md) | 安全设计 |

## API 接口文档

| 文档 | 说明 |
|------|------|
| [api/sessions.md](api/sessions.md) | Realtime Voice Session API（创建 session、WebSocket 协议） |
| [api/tts.md](api/tts.md) | TTS API（语音合成、音色注册，含 v0.20.1 自动规范化说明） |
| [api/stt.md](api/stt.md) | STT API（实时语音识别） |
| [api/admin.md](api/admin.md) | Admin API（API Key 生命周期管理） |
| [api/websocket-protocol.md](api/websocket-protocol.md) | WebSocket 协议详细规范（鉴权、关闭码、帧格式） |
| [api/CONVENTIONS.md](api/CONVENTIONS.md) | API 设计规范（错误格式、鉴权约定） |

## 当前部署信息

- **服务器**：`192.168.66.163`（GPU 服务器，RTX 3060 12GB）
- **Admin Console**：`https://192.168.66.163/admin-v2/`（admin / RTVoice@2026）
- **TTS 版本**：`rtvoice/tts-server-cosyvoice3:v0.20.1`
- **主分支**：`main`，最新 merge commit `302f000`

## 补充文档

- `QUICKSTART-TOPOLOGY.md` — 服务拓扑快速参考
- `benchmarks/` — 性能基准测试记录
- `v0.x-validation.md` — 各版本验收记录
