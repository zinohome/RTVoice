# RTVoice WebSocket Protocol Contract

OpenAPI 3.x **不覆盖** WebSocket（只能描述 HTTP）。本文档作为 WS 端点契约权威来源，与 G4 done 标准 #2 "STT schema 空壳" 互补。

## 通用规则

### 鉴权（SP6 + SP9 T1）

三路 Bearer，任一即可（按优先级）：

1. **`Sec-WebSocket-Protocol: bearer.<token>`** 子协议（浏览器首选）
   - 服务器 **101 响应必须 echo 同字面**（RFC 6455 §4.2.2，由 SP9 T1 修复并 chromium 守门）
2. **`Authorization: Bearer <token>`** header（server-to-server 首选）
3. **`?token=<token>`** query 参数（URL log 风险，fallback only）

### 关闭码

| Close code | 含义 |
|---|---|
| 1000 | 正常结束 |
| 1011 | 服务器内部错（attach failed 等） |
| 1013 | 临时不可用（模型未加载） |
| 4001 | 客户端协议错（无效 metadata 等） |
| 4400 | metadata 帧超时 / 解析失败 |
| 4401 | 鉴权失败（缺 Bearer / token 无效 / scope 不足） |
| 4403 | 鉴权通过但非 session owner |
| 4404 | session_id 不存在 |
| 4408 | session idle timeout |
| 4410 | session 过期 / WS 4410 = expired |

---

## STT WebSocket `/v1/asr`

**Scope**: `stt`
**Service**: stt-server (host port 9090/9190)

### 上行（client → server）

| 帧类型 | 内容 | 含义 |
|---|---|---|
| binary | PCM int16 LE 16kHz mono samples | 任意长度音频帧（建议 20-100ms 一帧） |
| text `EOS` | — | 声明本轮 utterance 结束，等 final |
| text `RESET` | — | 丢弃当前 stream 状态 |

### 下行（server → client）

```json
{ "type": "partial", "text": "..." }
{ "type": "final",   "text": "..." }
{ "type": "error",   "message": "..." }
```

---

## TTS WebSocket `/v1/tts/stream_ws` （v0.7+ Fun-CosyVoice 3）

**Scope**: `tts`
**Service**: tts-server (host port 9880)

### 上行

1. **首帧 metadata**（JSON text）：
   ```json
   {
     "voice": "default_zh_female",
     "speed": 1.0,
     "sample_rate": 24000
   }
   ```
2. **后续文本帧**：text deltas（增量 LLM token 输出）
3. **结束**：text `"EOS"`

### 下行

- binary 帧：PCM int16 LE 24kHz mono samples
- text `done`：合成完毕
- text JSON error：`{"type":"error","message":"..."}`

---

## Realtime Voice WebSocket `/v1/realtime/{session_id}`

**Scope**: `realtime`
**Service**: realtime-server (host port 9000)
**前提**：HTTP `POST /v1/sessions` 拿到 `session_id` 后再连 WS。

### 上行

| 帧类型 | 内容 |
|---|---|
| binary | PCM int16 LE 16kHz mono 用户麦克风音频 |
| text JSON | 控制事件（如 `{"type":"session.update","voice":"..."}`，`{"type":"session.end"}`） |

### 下行

```json
{ "type": "session.created", ... }
{ "type": "transcript.partial", "text": "..." }
{ "type": "transcript.final", "text": "..." }
{ "type": "response.text", "delta": "..." }
{ "type": "response.audio", ... }       // 同时配 binary 帧
{ "type": "session.ended", "reason": "..." }
{ "type": "error", "code": "...", "message": "..." }
```

---

## 测试 / 守门

- **真浏览器回归测试**：`tests/e2e/test_browser_ws_subprotocol.py`（headless chromium 强制 RFC 6455）
- CI 工作流 `.github/workflows/browser-e2e.yml`：4 个 WS endpoint 改动触发

---

## 为啥不进 OpenAPI

OpenAPI 3.x 没 WS 类型描述。AsyncAPI 是替代方案但 RTVoice 当前不引入（多余的工具链负担）。本文档充当机器可读契约的人工等价物。如未来生态有变（GraphQL Subscriptions over WS / AsyncAPI 主流化），重新评估。
