# RTVoice API Conventions

本文档是 RTVoice 平台 API 的**规范**：路径风格、版本、错误格式、鉴权、headers、capability discovery、deprecation 流程。
所有 service（STT / TTS / token-server / Realtime Voice）必须遵守。

> **快速速查**：[§2 速查表](#速查表)

---

## §1 基础原则

- 数据面 endpoints 用 `/v1/<resource>` 前缀
- 运维面 (`/health`、`/metrics`、`/info`、`/openapi.json`) **不**加版本前缀
- HTTP + WS 错误格式同形态：`{"type":"error","code":"...","message":"...","request_id":"..."}`
- 客户端 Bearer token 鉴权；admin 端单独 key
- breaking change 走 soft deprecation（≥1 release + Deprecation/Sunset headers）

## §2 速查表

| 项 | 规则 |
|---|---|
| URL 前缀 | 数据面 `/v1/`，运维面无版本 |
| 资源命名 | 复数名词（`/voices`、`/sessions`），snake_case 路径参数（`{spk_id}`）|
| 动词 | URL 不放动词，用 HTTP method 语义 |
| 错误格式 | `{type:"error", code:"<service>.<reason>", message, request_id}` |
| 鉴权 | HTTP `Authorization: Bearer`；WS 三路（header / subprotocol / query）|
| Status Code | 200/201/204 成功；400/401/403/404/409/410/422/429/503 各定语义 |
| Request ID | 客户端可传 `X-Request-ID`，server 必返 |
| Pagination | cursor + limit |

---

## §3 URL 与命名

- 资源用复数：`/voices`（collection），`/voices/{spk_id}`（item）
- snake_case for 多词资源和路径参数：`/v1/tts/stream`、`/v1/tts/stream_ws`、`{spk_id}`、`{session_id}`
- **禁止动词在 URL 中**：`POST /voices/add` ❌ → `POST /voices` ✓（创建动作由 POST method 表达）
- 子资源用斜杠分隔：`/v1/sessions/{id}/transcript`

## §4 HTTP 方法语义

| 方法 | 语义 | 幂等 |
|---|---|---|
| GET | 读取，无副作用 | ✓ |
| POST | 创建 / 触发动作 | ✗ |
| PUT | 全量替换 | ✓ |
| PATCH | 部分更新 | ✗ |
| DELETE | 删除 | ✓ |

## §5 HTTP Status Codes

| Code | 用途 |
|---|---|
| 200 | 同步成功 |
| 201 | 创建成功（POST 返新资源）|
| 204 | 成功无 body（DELETE）|
| 400 | 参数错误（schema 不对）|
| 401 | 鉴权失败 / 缺 Bearer |
| 403 | Bearer 对但无权限 |
| 404 | 资源不存在 |
| 409 | 冲突（如 spk_id 重复）|
| 410 | 资源永久删除（deprecated endpoint sunset 后）|
| 422 | 业务校验失败（如 wav 格式不对）|
| 429 | rate limit |
| 503 | 服务未就绪（model loading）|

## §6 错误格式

所有 4xx/5xx 响应 body：

```json
{
  "type": "error",
  "code": "tts.voice_not_found",
  "message": "voice 'unknown' 不存在",
  "request_id": "req_abc123"
}
```

`code` 用 `<service>.<reason>` 分层 snake_case：

| 范例 | 含义 |
|---|---|
| `stt.invalid_audio` | STT 收到非 PCM int16 LE 16kHz |
| `tts.voice_not_found` | TTS 未知 spk_id |
| `tts.voice_already_exists` | 重复注册音色 |
| `tts.wav_too_large` | 上传 wav > 5MB |
| `auth.invalid_token` | Bearer 不对 |
| `auth.missing_token` | 没传 Bearer |
| `session.not_found` | session_id 不存在 |
| `session.capacity_full` | server 超并发上限 |
| `session.unauthorized` | Bearer 不匹配 creator |
| `session.expired` | session 超 max lifetime |
| `session.idle_timeout` | ws idle 超时 |
| `turn.timeout` | 单 turn 处理超时 |
| `turn.in_progress` | 前 turn 未结束就发新 audio.eos |
| `stt.empty` | STT final 为空（无有效语音）|
| `internal.upstream_closed` | 上游 service WS 断 |
| `internal.unknown` | 兜底（不详的内部异常）|
| `prompt.too_long` | POST/WS prompt 字符数超 PROMPT_MAX_CHARS |
| `session.update.invalid` | WS session.update 字段不在白名单 |
| `audit.write_failed` | 服务端落盘异常（不发 client，仅 log） |

WS 错误事件用同 schema：`{"type":"error","code":"...","message":"..."}`。

## §7 鉴权

### HTTP

```
Authorization: Bearer <RTVOICE_API_KEY>
```

`RTVOICE_API_KEY` 留空时 service 在 dev 模式跳过鉴权（仅 dev compose profile）。

### WebSocket（三路任一即可）

| 方式 | 写法 | 适用 |
|---|---|---|
| HTTP header | `Authorization: Bearer <KEY>` | server-to-server |
| subprotocol | `Sec-WebSocket-Protocol: bearer.<KEY>` | 浏览器（标准 ws subprotocol）|
| query | `?token=<KEY>` | 兜底 |

鉴权失败：HTTP 401 / WS close code `4401`。

### Admin endpoints

`POST /v1/voices`、`DELETE /v1/voices/{id}` 用单独的 `TTS_ADMIN_API_KEY`（不是 RTVOICE_API_KEY），避免 client key 被滥用为管理权限。

## §8 通用 Headers

| Header | 方向 | 用途 |
|---|---|---|
| `Authorization` | request | Bearer token |
| `Content-Type` | request | `application/json` 默认 |
| `X-Request-ID` | request/response | 客户端可传，server 必返；用于排障关联 |
| `Deprecation` | response | `true` if endpoint 即将下线 |
| `Sunset` | response | RFC 7231 HTTP date，endpoint 下线日期 |
| `RateLimit-Limit` | response | 限流上限（每分钟）|
| `RateLimit-Remaining` | response | 当前窗口剩余 |

## §9 Pagination

未来 list endpoints 用 cursor 分页：

```
GET /v1/voices?cursor=<opaque>&limit=50
→ {"data": [...], "next_cursor": "..." | null}
```

理由：offset pagination 在 high-throughput insert 下结果不稳；cursor 提前定，避免后续 v2 时再换。

## §10 Capability Discovery

每个 service 暴露：

- `GET /info` — 简单 JSON `{name, version, backend, capabilities: {...}}`
- `GET /openapi.json` — FastAPI auto-generated OpenAPI 3.0 schema

WS endpoints 在 `docs/api/{service}.md` 用 markdown 描述（OpenAPI 3.0 对 WS 支持弱，未来如必要再考虑 AsyncAPI）。

## §11 OpenAPI 自动生成约定

每 endpoint 必须：

1. 用 Pydantic models 描述 request body / response
2. `summary` + `description` + `tags`
3. `responses={200: ..., 400: ..., 401: ..., ...}` 标注错误返回（用本规范的 `ErrorResponse` model）

例:

```python
@app.post(
    "/v1/voices",
    summary="Register a new voice clone",
    description="Upload 16kHz mono wav (3-30s) + reference text. Persists.",
    tags=["voices"],
    response_model=VoiceCreated,
    status_code=201,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Auth failed"},
        409: {"model": ErrorResponse, "description": "spk_id exists"},
    },
)
async def add_voice(...): ...
```

## §12 Deprecation 流程（未来 breaking change 模板）

1. 新 endpoint 上线（如 `/v2/foo`）
2. 老 endpoint `/v1/foo` 仍工作，response 加：
   - `Deprecation: true`
   - `Sunset: Thu, 31 Dec 2026 23:59:59 GMT`
3. CHANGELOG 公告 + 文档加 `**deprecated**` 标记
4. ≥ 1 个 release 周期后，老 endpoint 返 410 Gone：
   ```json
   {"type":"error","code":"deprecated","message":"endpoint moved to /v2/foo, see Sunset header"}
   ```

## §13 现有 endpoints 迁移表（v0.7 → v0.8 hard cutover）

| 老路径 | 新路径 | 备注 |
|---|---|---|
| `WS /asr` | `WS /v1/asr` | STT |
| `POST /tts/stream` | `POST /v1/tts/stream` | TTS HTTP |
| `WS /tts/stream_ws` | `WS /v1/tts/stream_ws` | TTS WS |
| `GET /voices` | `GET /v1/voices` | TTS list |
| `POST /voices/add` | `POST /v1/voices` | **方法换 POST + 去掉 /add**（不放动词在 URL）|
| `DELETE /voices/{spk_id}` | `DELETE /v1/voices/{spk_id}` | TTS delete |
| `POST /token` | `POST /v1/tokens` | token-server，**复数化** |
| 新增 | `POST /v1/sessions` | Realtime Voice 创建 session（v0.9.0 已实现）|
| 新增 | `WS /v1/realtime/{session_id}` | Realtime Voice 数据面（v0.9.0 已实现）|

**保留不动**（运维 / 内部测试页）：

- `GET /health` 所有 service
- `GET /metrics` 所有 service
- `GET /info` stt-server, tts-server
- `GET /openapi.json` 所有 FastAPI app（auto）
- token-server `GET /` 测试页（dev 用）
