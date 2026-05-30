# SP10 实施 plan — G3 (per-key metrics) + G4 (真 OpenAPI)

## G3 部分

### T1 — 共享 helper `services/common/rtvoice_auth/metrics_labels.py`
- `safe_key_id(key) -> str`：Key 对象 → key.id；None → "anonymous"；internal probe → "internal"
- `cardinality_cap` 注册 + unknown fallback "unknown_<hash8>"
- 单测

### T2 — 5 个新核心 metrics 定义 `services/common/rtvoice_auth/metrics.py`
- `rtvoice_requests_total` Counter[service,endpoint,key_id,status]
- `rtvoice_request_duration_seconds` Histogram[service,endpoint,key_id]
- `rtvoice_stt_audio_seconds_total` Counter[key_id]
- `rtvoice_tts_chars_total` Counter[key_id]
- `rtvoice_realtime_session_duration_seconds` Histogram[key_id]

### T3 — FastAPI middleware `services/common/rtvoice_auth/instrumentation.py`
- record_request_metric middleware：on response 拿 request.state.key_id（auth 依赖里塞）+ endpoint path + status
- 单测

### T4 — 4 service 集成
- realtime-server / stt-server / tts-server / token-server `main.py` lifespan 装 middleware
- 鉴权依赖里 `request.state.key_id = key.id`
- 单测：post /v1/tokens 后 metrics 含 key_id label

### T5 — STT 业务 metric: audio_seconds_total
- stt-server `/v1/asr` WS handler 累计接到的 PCM 时长 → counter inc(key_id)

### T6 — TTS 业务 metric: chars_total
- tts-server 3 个 endpoint 合成完累计 chars → counter inc(key_id)

### T7 — Realtime session duration
- session_manager cleanup 时 observe(duration, key_id)

### T8 — 治 room 自由文本 label（D3-S3）
- `rtvoice_tokens_issued_total{room}` 删 `room` label 或改 hash
- 选 hash：保留可观测性，限制基数

## G4 部分

### T9 — 4 service securitySchemes 注入
- `services/common/rtvoice_auth/openapi.py`：函数 `add_bearer_security_scheme(app)`
- 4 service main.py 调用一次
- 单测 grep `/openapi.json` 含 `components.securitySchemes.rtvoice_auth`

### T10 — TTS response_model 补全
- 3 个 endpoint 加 Pydantic response_model（GET /v1/voices / POST /v1/voices / DELETE /v1/voices/{id}）
- POST /v1/tts/stream 加 `responses={"200": {"content": {"application/octet-stream"}}}`

### T11 — /info 标准化
- 4 service `/info` 返 `service/version/capabilities/models` 4 字段
- token-server 加 `/info` (现在 404)
- stt/tts /info 加 `version: "0.16.0"` 字段

### T12 — `docs/api/websocket-protocol.md`
- 4 WS endpoint 协议契约
- close code 字典
- Sec-WebSocket-Protocol echo 规则（SP9 T1）

### T13 — OpenAPI snapshot test
- `tests/contract/test_openapi_snapshot.py`
- 抓 4 service `/openapi.json` 与 golden 比；不同需 `--update-snapshot`
- 加 `.github/workflows/contract-snapshot.yml` 触发

### T14 — Grafana 面板
- prometheus/grafana provisioning 加 `top-keys-5min` 面板
- `sum by(key_id)(rate(rtvoice_requests_total[5m]))` 排序

## Done 标准 + commit + push + prod redeploy

按 SP10 spec done 章节逐条核对。
CHANGELOG v0.16.0 段。
prod build + recreate + 类 B 烟测（codegen + PromQL key_id query）。
