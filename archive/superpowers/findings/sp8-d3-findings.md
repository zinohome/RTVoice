# SP8 D3 — 合成负载 + Metrics G3 缺口

**Date**: 2026-05-12
**Window**: T0 = 2026-05-12T06:38:57Z → T1 = 2026-05-12T06:39:36Z (≈39 s)
**Prod**: `192.168.66.163` (token :8000, realtime :9000, prom :9090, stt/tts host-内网 only)
**Artifacts**: `/tmp/sp8-d3/{baseline,after}-*.{txt,json}`, `/tmp/sp8-d3/load-bc.{py,log}`

---

## 总结

| scenario | total | concurrency | elapsed | rps | 状态分布 | p50 | p95 | max |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| A — TTS `/v1/tts/synthesize` | 0 | — | — | — | **未跑** | — | — | — |
| B — token `POST /v1/tokens` | 200 | 10 | 0.79 s | 252 | `{200: 200}` | 33.5 ms | 65.1 ms | 71.4 ms |
| C — realtime `POST /v1/sessions` | 20 | 5 | 0.14 s | 144 | `{201: 4, 503: 16}` | 25.0 ms | 62.7 ms | 62.7 ms |

> Scenario A 阻塞原因：tts-server 容器 `:9880` 不暴露 host 端口，prod SSH 公钥/密码两条路径都被拒（`ubuntu@192.168.66.163: Permission denied (publickey,password)`）。当前 sandbox 内无法直接打 TTS HTTP。可通过 realtime session WS（间接驱动 TTS 引擎）补做，但已超出 D3 时间盒，**留给 D5/SP9 落地**。详见末尾"副产物 finding S1"。
> Scenario C 503 占 16/20：realtime 服务硬上限 `max_concurrent_sessions=5`（来自 `/info`），session 创建后立刻 close 不是 D3 路径——server 端只接收 HTTP 201，session 直到 WS 接入或 idle timeout 才释放，所以 concurrency=5 + 立刻打 20 次必触满容量。**这暴露了一个独立 bug：HTTP 路径无办法显式释放未连接的 session**（finding S2）。

### G3 done 5 条对照

| # | 硬标准 | 评级 | 证据 |
|---|---|---|---|
| 1 | 5 个 metric 全部含 `key_id` label | ❌ | `grep key_id /tmp/sp8-d3/{baseline,after}-*` → **0 命中**（8 个文件全 0）。Prom `sum by(key_id)(rate(rtvoice_tokens_issued_total[1m]))` 返回空 metric set `{}` |
| 2 | `key_id` 用 `Key.id` 不用原始 token；匿名→`"anonymous"` | ❌ | label 字段不存在，无值可校验 |
| 3 | 基数控制（仅 Key.id ∪ {anonymous, internal}） | ❌ | label 不存在；**反而**发现 `rtvoice_tokens_issued_total` 有 `room` 自由文本 label，跑负载后 series 从 5 涨到 10（每个 `sp8d3-N` 新增 1 series），无上限。**这是反例：现状已经有失控基数风险，加 `key_id` 时务必当心** |
| 4 | Grafana 按 key 维度 top-N 用量 panel | ❌ | label 不存在，panel 无法构建；Grafana 状态 D3 未巡检（host :3000 暴露但本任务范围外） |
| 5 | 3 service 单测各 ≥1 metric 含 key_id | ❌ | 仓库 grep（D1/D2 已确认无此覆盖） |

---

## 场景 A（未跑）

- 设计：10 并发 × 100 次 `POST /v1/tts/synthesize`
- 跑前发现 tts-server 不暴露 → 改走 ssh + docker exec → 公钥被拒
- 决策：跳过，把诊断焦点压到 B + C 已足够证明 G3 缺口（per-key label 在 4 个 service 全缺，再多打 1 个 service 不增加论证）
- 注：tts-server prod metrics 通过 Prom 间接抓取（`/tmp/sp8-d3/{baseline,after}-tts-server.json`），其 series 集合在 D3 窗口内**无变化**——印证 D3 没驱动 TTS 流量

## 场景 B — tokens

- 脚本：`/tmp/sp8-d3/load-bc.py`（asyncio + aiohttp，Semaphore=10）
- payload：`{"room": f"sp8d3-{i%5}", "identity": f"u{i}"}`，Bearer = `/tmp/sp8-d3/secret.txt`
- 完美 200/200，p50=33.5 ms p95=65.1 ms
- **metrics diff**：
  ```
  rtvoice_tokens_issued_total: 5 series → 10 series
    新增 room="sp8d3-{0..4}" 各 40.0
    旧 series 值不变
  rtvoice_token_auth_failures_total: 无变化（{missing:1, scope:1}）
  http_requests_total{handler="/v1/tokens",method="POST",status="2xx"} +200
  ```
- 200 次请求按 5 个 room 均分（`i%5` mod 后每组 40），与 PromQL `topk(5, sum by(room)(...))` 一致

## 场景 C — realtime sessions

- payload：`{"voice":"default_zh_female","speed":1.0}`，Bearer 同 B
- 命中 `max_concurrent_sessions=5` 硬限：5 个 201 + 16 个 503（实际 4 进 + 1 race，metrics 显示 status=2xx +5）
- **metrics diff**：
  ```
  rtvoice_realtime_sessions_active: 0 → 5 (gauge, 未释放)
  http_requests_total{handler="/v1/sessions",method="POST"}:
    2xx: 0 → 5
    5xx: 0 → 16
    4xx: 1 (不变)
  ```
- 注：active=5 在测试结束 30 s（session_idle_timeout）后才会回落，本快照里仍处于卡满状态。**finding S2**

---

## G3 缺口具体长相（重点）

### 当前 prod 可用 label（全量盘点 rtvoice_* + http_requests_total）

```
rtvoice_stt_decode_seconds_bucket     : le
rtvoice_stt_events_total              : type        ∈ {final_eos, partial}
rtvoice_stt_ws_connections_total      : (none)
rtvoice_tts_phrases_total             : (none)
rtvoice_tts_failures_total            : (none)
rtvoice_tts_ttfb_seconds_bucket       : le
rtvoice_tts_phrase_rtf_bucket         : le
rtvoice_realtime_sessions_active      : (none)
rtvoice_realtime_audit_queue_depth    : (none)
rtvoice_tokens_issued_total           : room        ← 自由文本，已含 10+ 不同值
rtvoice_token_auth_failures_total     : reason      ∈ {missing, scope}
http_requests_total (4 service 一致)  : handler, method, status
```

所有 metric **共享的隐含维度**只有 `job` / `instance`（service 维度），别的什么都没有。

### 缺的 label（按业务问题倒推）

| 业务问题 | 现状答得出吗 | 缺什么 |
|---|---|---|
| "key_CzClq1YYH9ze11_e 这小时跑了多少 TTS 字符" | ❌ 完全不能 | `rtvoice_tts_chars_total{key_id}` 整个 metric 都没（G3 done #1 第 4 条） |
| "哪个 key 触发 401 最多" | ❌ 只能按 reason 聚合，不知道是谁 | `rtvoice_token_auth_failures_total` 加 `key_id` |
| "key X 这小时打了多少 token 请求" | ❌ 只能按 room 聚合（且 room 是用户自填，不是 key） | `rtvoice_tokens_issued_total` 加 `key_id`（同时考虑去掉 `room` 或限制基数） |
| "STT 总音频时长 top-10 用户" | ❌ metric 完全不存在 | `rtvoice_stt_audio_seconds_total{key_id}`（G3 done #1 第 3 条） |
| "realtime 累计 session 时长 top-10 用户" | ❌ 现仅 active gauge | `rtvoice_realtime_session_duration_seconds{key_id}` histogram |
| "key X 这小时 p95 延迟" | ❌ 仅有 service 维度 | `rtvoice_request_duration_seconds{service,endpoint,key_id}` histogram |
| "service X 总 RPS" | ✅ `sum by(job)(rate(http_requests_total[1m]))` 可跑 | — |
| "限流命中分组" | ⚠️ 仅有 `reason="scope"` 聚合 1，看不到限流主体 | `key_id` + 区分 `concurrent` vs `per_hour` |

### 实施建议（按 G3 done #1 的 5 个 metric 来）

| 新 metric | 谁打点 | 取值口 | 备注 |
|---|---|---|---|
| `rtvoice_requests_total{service,endpoint,key_id,status}` | 中间件（推荐 `services/_shared/auth.py` 解析后挂到 request.state） | `Key.id` 或 `"anonymous"` | 替换或并行于现 `http_requests_total`；`endpoint` 取 FastAPI `route.path`（不要原始 URL，否则基数爆炸） |
| `rtvoice_request_duration_seconds{service,endpoint,key_id}` | 同上中间件 | 同上 | histogram；和上一条共用 label set |
| `rtvoice_stt_audio_seconds_total{key_id}` | stt-server WS handler 收到 final_eos 时累加 | `Key.id`；guest WS 暂归 `"anonymous"` | 注意 sherpa-onnx 计时已存在（`stt_decode_seconds`），但缺 key 维度 |
| `rtvoice_tts_chars_total{key_id}` | tts-server `/v1/tts/synthesize` 完成时累加 `len(text)` | 同上 | 现 `rtvoice_tts_phrases_total` 没 char 计数也没 key 维度 |
| `rtvoice_realtime_session_duration_seconds{key_id}` | realtime-server session close 时 observe（histogram） | 同上 | gauge `rtvoice_realtime_sessions_active` 保留，但**应加 `{key_id}` label**（active concurrent per-key） |

### 基数控制实施细节

- 中间件落 label 前断言：`value in (set_of_active_key_ids ∪ {"anonymous", "internal"})`，否则 fallback 到 `"anonymous"` 并记 WARN log
- 同步**做掉现存的 `room` 自由文本基数风险**：要么白名单化要么剥掉 → 否则 G3 加了 key_id，老坑还在
- prom 端加 `metric_relabel_configs` drop 兜底（最后一道防线）

---

## 副产物 finding（非 G3）

### S1 — TTS HTTP 直测路径在沙盒不可达
- tts-server 容器 9880 仅集群内可达；prod ssh 拒绝沙盒 user
- 影响：D3 / 未来 dogfooding 都无法直接打 TTS 同步端点
- 建议：(a) 在 prod compose 暴露 tts 内网端口（开发机 only），或 (b) 在 token-server 加一个 `/v1/dogfood/tts` proxy，或 (c) 全部通过 realtime session 间接驱动（但失去 isolation）

### S2 — realtime session HTTP 创建后无法显式释放
- `POST /v1/sessions` 返 201 + ws_url，但若客户端不接 WS，session 占用 `sessions_active` 直到 `session_idle_timeout_s=30`
- D3 实测：5 并发 POST 后 16 个 503 capacity full，活动 session 5 卡满
- 建议：(a) 加 `DELETE /v1/sessions/{id}` cancel endpoint，或 (b) 给"已发 ws_url 但未接入"的 session 设更短的 `pre_connect_timeout`（如 5 s）

### S3 — `rtvoice_tokens_issued_total` 已经存在高基数风险
- `room` 是 client 任意字符串（正则 `[A-Za-z0-9_-]{1,64}`），单次 D3 跑就新增了 5 个 series
- prod 已有 10+ 个 room series，长期增长无上限
- 这是 G3 实施前**必须先治理**的隐患：加 `key_id` 时如果不顺手治 `room`，基数雪球更大

### S4 — token-server 缺 `/info` 端点（D2 已记录，D3 印证）
- D2 finding F1 已说过；D3 confirm：`http://192.168.66.163:8000/info` 404，OpenAPI paths 仅 `/metrics /health /v1/tokens`
- 与 G4 关联，但 G3 的 capability discovery 也受影响——客户端无法自描述查"我有 per-key metrics 吗"

### S5 — token-server 接收的 Bearer 是单一全局 secret，**而不是按 key_id 区分的 Bearer**
- payload schema 中无 `key_id`，仅 `room` / `identity`
- 现 D3 任务给的 `key_CzClq1YYH9ze11_e` / `scopes` / 配额，**在 token-server 当前实现里似乎并未关联**（grep auth_failures 仅看 `reason`，无 key 维度）
- 这意味着 G3 加 `key_id` label 前，**必须先确认 auth 中间件确实能 resolve 到 `Key.id`**——D3 范围外，但 SP8 落地前需要 D4/D5 验证

### S6 — PromQL `sum by(key_id)(rate(rtvoice_requests_total[1m]))` 报"metric not found"
- 不是 zero vector，而是该 metric **整个不存在**
- 这是 G3 done 标准 #1 的直接 negative evidence，可以直接贴进 SP8 落地 PR 的 "before" 截图
