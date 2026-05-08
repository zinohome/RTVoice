# Realtime Voice Service API

> 实时语音对话。WebSocket gateway 默认（OpenAI Realtime 风格）；LiveKit 高级模式可选。
> **状态：v0.9.0 已实现**（SP3 加 prompt + memory + transcript 流式）。

## Endpoints 速查

| 用途 | 方法 | 路径 | 鉴权 | 状态 |
|---|---|---|---|---|
| 创建 session | POST | `/v1/sessions` | Bearer | ✓（v0.9）|
| WS 数据面 | WS | `/v1/realtime/{session_id}` | session_id | ✓（v0.9）|
| LiveKit token（高级模式）| POST | `/v1/tokens` | APP_API_KEY | ✓（v0.7）|
| 健康检查 | GET | `/health` | 无 | ✓ |
| 服务信息 | GET | `/info` | 无 | ✓ |

## POST /v1/sessions

创建一个 Realtime Voice session：分配 worker、初始化 memory、返回 session_id + ws_url。

### Request

```json
{
  "voice": "default_zh_female",
  "speed": 1.0,
  "prompt": "你是 IT 客服，用中文简短回答",
  "audit_persist": false
}
```

### Response (201 Created)

```json
{
  "session_id": "sess_abc123",
  "ws_url": "ws://localhost:9000/v1/realtime/sess_abc123",
  "expires_at": "2026-05-08T16:30:00Z"
}
```

## WS /v1/realtime/{session_id}

双向音频/事件 stream。基于 OpenAI Realtime API events 风格。

### Client → Server

| Type | Payload | 何时 |
|---|---|---|
| text JSON | `{"type":"session.update","instructions":"...","voice":"..."}` | 热改 session config（可选）|
| binary | PCM int16 LE 16kHz mono | 用户音频 |
| text `"audio.eos"` | — | 用户发言结束 |

### Server → Client

| Type | Payload | 时机 |
|---|---|---|
| text | `{"type":"transcript.partial","text":"..."}` | STT partial |
| text | `{"type":"transcript.final","text":"..."}` | STT final |
| text | `{"type":"response.text","text":"..."}` | agent 回复文本（流式）|
| binary | PCM int16 LE 24kHz mono | agent 回复音频 |
| text | `{"type":"response.done"}` | 本轮回复结束 |
| text | `{"type":"error","code":"...","message":"..."}` | 失败 |

详细 session 生命周期 / memory 管理 / prompt 透传规则 → SP3 设计文档（SP3 启动时创建）。

## POST /v1/tokens（高级模式 LiveKit）

发 LiveKit room JWT。仅 LiveKit advanced mode 用；默认 WS gateway 模式不需要。

### Request

```json
{
  "identity": "user-alice",
  "room": "rtvoice-test",
  "ttl_minutes": 10
}
```

### Response

```json
{
  "token": "eyJ...",
  "url": "ws://localhost:7880",
  "room": "rtvoice-test",
  "identity": "user-alice"
}
```

### Error codes

| Code | HTTP | 含义 |
|---|---|---|
| `auth.missing_token` | 401 | 缺 Authorization header |
| `auth.invalid_token` | 401 | APP_API_KEY 不对 |
| `auth.rate_limit` | 429 | 超 rate limit（默认 30/min/IP）|

## GET /info

```json
{
  "name": "token-server",
  "version": "0.8.0",
  "capabilities": {
    "livekit_token": true,
    "rate_limit_per_minute": 30
  }
}
```

## 高级模式 LiveKit 说明

参见 [ARCHITECTURE.md §4](../../ARCHITECTURE.md) 的"LiveKit 高级模式数据流图"。客户端用 [LiveKit 官方 SDK](https://docs.livekit.io/) 而非裸 WebSocket。
