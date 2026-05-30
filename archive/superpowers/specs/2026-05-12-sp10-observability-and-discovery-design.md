# SP10 — G3 (per-key metrics) + G4 (真 OpenAPI) 落地

**Why**：05-12 复盘把 G3/G4 偷偷降级成"部分 ✅"被点破，今早写了 done 硬标准 spec
(`docs/superpowers/specs/2026-05-12-g3-g4-done-criteria.md`)，SP8-D2/D3 dogfood 拿到具体缺口证据。本 SP **严格按 done spec 落地**。

**Why 捆绑**：G3+G4 都是"schema/instrumentation"性质改动（4 service 各动 main.py + 测试），review/CI/部署 pattern 同构，不像 SP4 / SP7 那种混不同性质。用户 2026-05-12 明确选 G3+G4 一起。

## G3 — Per-API-Key Metrics

### 落地内容（对应 done spec 5 条）

1. **核心 metrics 加 `key_id` label**：
   - `rtvoice_requests_total{service,endpoint,key_id,status}` — 新增
   - `rtvoice_request_duration_seconds{service,endpoint,key_id}` — 新增（FastAPI 中间件级）
   - `rtvoice_stt_audio_seconds_total{key_id}` — 新增
   - `rtvoice_tts_chars_total{key_id}` — 新增
   - `rtvoice_realtime_session_duration_seconds{key_id}` — 新增
2. **key_id 取值规则**（`services/common/rtvoice_auth/metrics_labels.py`）：
   - 已鉴权 → `Key.id`（不是 secret，避免泄漏）
   - 未鉴权 / 鉴权失败 → `"anonymous"`
   - 内部探针（如 healthcheck） → `"internal"`
3. **基数控制**：
   - 注册 helper 把任意 key_id 输入归一化到 known-set；unknown → "unknown_<hash8>"（极端 fallback，防 unbounded growth）
   - 治 SP9-D3-S3：`rtvoice_tokens_issued_total{room=...}` 自由文本 `room` label → 改为 `room_hash`（SHA-256 前 8 字符）或干脆删 `room` label
4. **Grafana 面板**：加 1 个 `Top-N users (last 5min)` 面板按 `key_id` 维度切片
5. **测试**：单测覆盖 3 service 各至少 1 metric 含正确 key_id label

## G4 — 真 OpenAPI Schema

### 落地内容（对应 done spec 5 条）

1. **4 service `/openapi.json` 暴露 + audit**（FastAPI 默认，但需 audit）：
   - 检查 `/v1/` 端点完整
   - 排除 `/metrics`（D2-finding `/metrics` 泄进 client SDK 表面）
2. **schema 完整**：
   - 全端点的 request / response Pydantic schema
   - `ErrorResponse` 作为 401 / 403 / 422 / 500 / 503 默认 response model
   - **`components.securitySchemes.rtvoice_auth: { type: http, scheme: bearer }`**（D2 头号 finding 全 4 service 缺）
3. **`/info` JSON 化升级**：
   - 4 service `/info` 统一返：`service / version / capabilities / models`
   - token-server 加 `/info`（D2 + SP9 烟测发现没这端点）
   - stt/tts /info 加 `version` 字段（同步 0.15.0）
4. **客户端可消费**：`npx openapi-typescript-codegen` 跑 4 service 出 client 不报错 + Bearer 友好（有 `OpenAPI.TOKEN` 全局字段）
5. **CI 守护**：`tests/contract/test_openapi_snapshot.py` —— 每 service `/openapi.json` 与 `tests/contract/golden/<svc>.json` snapshot 比对，schema 变化必须显式 update golden

### TTS response_model 补全（D2 finding）

- `POST /v1/voices`、`DELETE /v1/voices/{id}`、`POST /v1/tts/stream`、`POST /v1/voices` 都加 Pydantic response_model
- `POST /v1/tts/stream` 因返 binary audio，用 `responses=` 显式声明 `application/octet-stream`

### WS protocol 单独契约化（D2 finding）

- 新文件 `docs/api/websocket-protocol.md`
- 显式承认 OpenAPI 不覆盖 WS（RFC 6455 vs OpenAPI 3.x 限制）
- 4 WS endpoint 各列：URL / 鉴权方式（含 Sec-WebSocket-Protocol echo, SP9 T1）/ message schema / close code 字典
- G4 done 标准 #2 对 STT 永远不可达 — 此文档把它从"❌"改"接受 + 替代契约 ✅"

## 不做

- 不动 LiveKit metric（第三方，单独整合）
- 不做 admin UI（SP12 候选）
- 不发 PyPI rtvoice-client（看用户决定）

## Done 标准

按 G3/G4 done spec **逐条**审 + 加 SP10 CHANGELOG v0.16.0。

类 B prod 验收：
- npx codegen 4 service 都过 + 生成 client 含 `OpenAPI.TOKEN`
- Prometheus query `sum by(key_id)(rate(rtvoice_requests_total[1m]))` 返非空
- snapshot test 通过

类 A 待用户：见 MANUAL_VALIDATION_QUEUE.md

## 估时

G3 ≈ 8 T / G4 ≈ 6 T / 总 14 T / 2-2.5 天
