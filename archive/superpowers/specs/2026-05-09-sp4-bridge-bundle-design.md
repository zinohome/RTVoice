# SP4 Bridge Bundle — Python SDK + SP3 残项 + A-lite 仪表盘 Design

**日期**：2026-05-09
**前置**：SP3 (v0.10.0) 已 prod；52 单元 + 1 prod E2E smoke。
**目标版本**：v0.11.0
**作用域**：把 RTVoice 从"platform 已建好"推进到"platform 已能被用起来 + 看得见"。

---

## 1. 目标

平台 SP1-SP3 全是 server-side 建设；当前 0 真实用户（CozyVoice 等下游手写 httpx）。SP4 是连接 platform 与 client 的 bridge，含三子项：

| 子项 | What | Why |
|---|---|---|
| **I · Python SDK** (`rtvoice-client`) | PyPI 包，4 命名空间 (stt/tts/realtime/tokens)，async + sync 双形态 | CozyVoice 等下游不再 hand-write httpx |
| **K · SP3 残项** | session.update 加 voice/speed 热改 + memory.clear 事件 | 收口 SP3 协议层；让 mid-session 真正可调 |
| **A-lite · 仪表盘** | Prometheus + Grafana docker profile + 单页 dashboard | 上 prod 后能看见 sessions/turns/TTFT/audit |

三子项独立但同一 release（v0.11.0）。

---

## 2. 关键决策（D-2026-05-09-B.1~B.7）

| ID | 决策 | 理由 |
|---|---|---|
| **B.1** | SDK 包名 `rtvoice-client`，仓库 `clients/python/`（monorepo 内） | 避免与未来包名撞；版本同步 monorepo 节奏 |
| **B.2** | async + sync 双形态 (`AsyncClient` + `Client`) | 行业标准（OpenAI / Anthropic 同款）；脚本/Jupyter 友好 |
| **B.3** | 4 命名空间：`stt` / `tts` / `realtime` / `tokens` | 对齐"三对等 offering" + LiveKit token helper |
| **B.4** | PyPI 公开发布 | "platform 给所有人用"叙事一致；强制规范化 |
| **B.5** | Primitives + 高层 helper 双 API | 90% 用户用 helper（`conversation()`），高级用户用 primitives |
| **B.6** | 单页 RTVoice overview dashboard | 维护成本低；6 service 关键指标聚集 |
| **B.7** | K = voice/speed 热改 + memory.clear（无 transcript stable） | 无 STT 依赖；transcript stable 等 STT API 调研 |

---

## 3. 架构 & 文件布局

```
RTVoice/
├── clients/                                ← 新建
│   └── python/                             ← rtvoice-client SDK
│       ├── pyproject.toml                  hatchling backend
│       ├── README.md                       pip install + 5 quick start
│       ├── LICENSE                         Apache 2.0
│       ├── CHANGELOG.md
│       ├── src/rtvoice_client/
│       │   ├── __init__.py                 export Client / AsyncClient / errors
│       │   ├── _base.py                    BaseClient + Bearer + 错误分类
│       │   ├── stt.py                      STT namespace
│       │   ├── tts.py                      TTS namespace
│       │   ├── realtime.py                 Realtime（含 conversation helper）
│       │   ├── tokens.py                   LiveKit tokens
│       │   ├── errors.py                   RTVoiceError + 9 子类
│       │   ├── models.py                   Pydantic v2 models
│       │   └── py.typed                    typed marker
│       └── tests/
│           ├── test_stt.py / test_tts.py / test_realtime.py
│           ├── test_tokens.py / test_errors.py / test_sync.py
│           └── test_e2e_smoke.py           真 prod 跑（pytest.mark.e2e）
│
├── services/realtime-server/app/
│   ├── main.py                             ★ session.update 白名单 + memory.clear
│   ├── pipeline.py                         ★ tts_client_dirty 重建逻辑
│   ├── memory.py                           ★ +clear() 方法
│   └── (新)metrics.py                      Counter/Gauge for SP4 仪表盘
│
├── monitoring/                             ← 新建
│   ├── prometheus.yml                      6 services scrape
│   └── grafana/
│       ├── datasources.yml
│       └── dashboards/rtvoice-overview.json
│
└── docker-compose.yml                      ★ +prometheus +grafana (profile=monitoring)
```

新文件：~25（SDK 占 19）。修改文件：5。新依赖：SDK 内 httpx + websockets + pydantic（已是 services 间共有依赖）。

**SP3 兼容**：API 增量；session.update 旧客户端仅传 prompt 仍工作；老客户端不发 memory.clear 没影响。

---

## 4. 子项详细设计

### 4.1 SDK API surface

```python
from rtvoice_client import Client, AsyncClient
from rtvoice_client.errors import (
    RTVoiceError, AuthError, ValidationError, PromptTooLong,
    CapacityFull, SessionNotFound, SessionExpired, SessionUnauthorized,
    TurnTimeout, TurnInProgress, STTError, LLMError, TTSError, ServerError,
)

# 构造（默认单 base_url，假设反向代理；高级用法每 service 独立 URL）
c = Client(api_key="bear-32-...", base_url="https://rtvoice.your-domain.com")
# OR
c = Client(api_key="...", stt_url="...", tts_url="...", realtime_url="...", tokens_url="...")

# STT
text: str = c.stt.transcribe(pcm_bytes, sample_rate=16000)
async with c.stt.stream() as s:
    await s.feed(chunk); final = await s.request_final(timeout=5.0)

# TTS
pcm: bytes = c.tts.synthesize("你好", voice="default_zh_female", speed=1.0)
for chunk in c.tts.stream("你好", voice=..., speed=...):
    play(chunk)

# Realtime — primitives
sess: SessionCreateResponse = c.realtime.create_session(prompt="...", audit_persist=True)
async with c.realtime.connect(sess) as ws:
    await ws.feed(pcm); await ws.eos()
    async for evt in ws.events():  # typed RealtimeEvent union
        ...
    await ws.update_prompt("新")
    await ws.update_voice("alice")     # SP4 K
    await ws.update_speed(1.5)         # SP4 K
    await ws.clear_memory()            # SP4 K

# Realtime — 高层 helper
async for evt in c.realtime.conversation(audio_iter, prompt=..., audit_persist=False):
    if isinstance(evt, TranscriptPartial): ...
    elif isinstance(evt, ResponseText): ...
    elif isinstance(evt, ResponsePCM): play(evt.data)
    elif isinstance(evt, ResponseDone): break

# Tokens
tok: TokenResponse = c.tokens.livekit(identity="alice", room="rtvoice-test", ttl_minutes=10)
```

### 4.2 错误层级（typed exceptions）

```
RTVoiceError(code, message, request_id, http_status)
├── AuthError                       # auth.* (401)
├── ValidationError                 # validation.invalid_request (422)
├── PromptTooLong                   # prompt.too_long (422)
├── SessionError
│   ├── CapacityFull                # session.capacity_full (503)
│   ├── SessionNotFound             # session.not_found (WS 4404)
│   ├── SessionExpired              # session.expired (WS 4410)
│   └── SessionUnauthorized         # session.unauthorized (WS 4403)
├── TurnError
│   ├── TurnTimeout                 # turn.timeout
│   └── TurnInProgress              # turn.in_progress
├── STTError                        # stt.empty / stt.timeout / stt.failed
├── LLMError                        # llm.failed
├── TTSError                        # tts.failed
└── ServerError                     # 5xx / internal.unknown 兜底
```

`_base.py:_raise_for_code(body, http_status)` 把 server 返 `{"type":"error","code":"..."}` 映射对应类。

### 4.3 Pydantic v2 models

```python
# models.py
class SessionCreateRequest(BaseModel):
    voice: str | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    prompt: str | None = None
    audit_persist: bool = False

class SessionCreateResponse(BaseModel):
    session_id: str; ws_url: str; expires_at: str
    voice: str; speed: float; prompt: str; audit_persist: bool

class TranscriptPartial(BaseModel):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str; stable: bool = False

class TranscriptFinal(BaseModel):
    type: Literal["transcript.final"] = "transcript.final"
    text: str

class ResponseText(BaseModel):
    type: Literal["response.text"] = "response.text"
    text: str

class ResponseDone(BaseModel):
    type: Literal["response.done"] = "response.done"
    text: str = ""

class ResponsePCM(BaseModel):
    """非 server 事件；SDK 高层 helper 把 binary frame 包装成 typed."""
    type: Literal["response.pcm"] = "response.pcm"
    data: bytes

class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str; message: str; request_id: str | None = None

RealtimeEvent = TranscriptPartial | TranscriptFinal | ResponseText | ResponseDone | ResponsePCM | ErrorEvent
```

### 4.4 K · SP3 残项

**main.py session.update 白名单扩展**：
```python
allowed = {"type", "prompt", "voice", "speed"}
extra = set(ev.keys()) - allowed
if extra:
    emit error session.update.invalid; continue
if "prompt" in ev:
    if len > MAX: emit prompt.too_long
    else: sess.prompt = ev["prompt"]
if "voice" in ev:
    sess.voice = str(ev["voice"]); sess.tts_client_dirty = True
if "speed" in ev:
    s = float(ev["speed"])
    if not 0.5 <= s <= 2.0: emit error validation.invalid_request; continue
    sess.speed = s; sess.tts_client_dirty = True
```

**Session dataclass 加字段**：
```python
tts_client_dirty: bool = False  # voice/speed 改后 pipeline 要重建 tts_client
```

**pipeline.run_turn 开头加重建逻辑**：
```python
if sess.tts_client_dirty:
    try: await sess.tts_client.close()
    except: pass
    sess.tts_client = TTSClient(
        base_url=config.TTS_BASE_URL,
        voice=sess.voice, speed=sess.speed,
        api_key=config.RTVOICE_API_KEY or None,
    )
    sess.tts_client_dirty = False
    log.info("session %s rebuilt tts_client (voice=%s speed=%.2f)",
             sess.id, sess.voice, sess.speed)
```

**memory.clear 事件**：
```python
elif ev.get("type") == "memory.clear":
    sess.memory.clear()
    if sess.audit_writer:
        await sess.audit_writer.write({"event": "memory.clear"})
```

**memory.py 加方法**：
```python
def clear(self) -> None:
    """清空当前历史；prompt 不动."""
    self._buf.clear()
```

### 4.5 A-lite 仪表盘

**docker-compose 加 monitoring profile**：
```yaml
  prometheus:
    image: prom/prometheus:v3.0.0
    profiles: ["monitoring"]
    networks: [rtvoice_net]
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - rtvoice_prom_data:/prometheus
    ports: ["${BIND_HOST:-127.0.0.1}:${PROMETHEUS_PORT:-9090}:9090"]
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d

  grafana:
    image: grafana/grafana:11.4.0
    profiles: ["monitoring"]
    networks: [rtvoice_net]
    volumes:
      - ./monitoring/grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - rtvoice_grafana_data:/var/lib/grafana
    ports: ["${BIND_HOST:-127.0.0.1}:${GRAFANA_PORT:-3000}:3000"]
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}

volumes:
  rtvoice_prom_data: { driver: local }
  rtvoice_grafana_data: { driver: local }
```

启用：`docker compose --profile prod --profile monitoring up -d`

**新增 metrics（realtime-server `app/metrics.py`）**：
```python
from prometheus_client import Counter, Gauge

SESSIONS_ACTIVE = Gauge("rtvoice_realtime_sessions_active",
                        "current active session count")
TURNS_TOTAL = Counter("rtvoice_realtime_turns_total",
                      "total run_turn invocations", ["status"])
AUDIT_QUEUE_DEPTH = Gauge("rtvoice_realtime_audit_queue_depth",
                          "sum of all session audit queue sizes (no label to avoid cardinality blowup)")
```

`SessionManager.create/cleanup` 调 `SESSIONS_ACTIVE.set(self.active_count())`。
`pipeline.run_turn` 末调 `TURNS_TOTAL.labels(status="ok"|"error").inc()`。

**dashboard panels**（rtvoice-overview.json）：
| Panel | PromQL |
|---|---|
| Service Health | `up{job=~".+-server"}` |
| Active Sessions | `rtvoice_realtime_sessions_active` |
| Turns / min | `60 * rate(rtvoice_realtime_turns_total[1m])` |
| Turn Error Rate | `rate(rtvoice_realtime_turns_total{status="error"}[5m]) / rate(rtvoice_realtime_turns_total[5m])` |
| HTTP P95 latency by service | `histogram_quantile(0.95, sum by (job, le) (rate(http_request_duration_seconds_bucket[5m])))` |
| HTTP req rate | `sum by (job) (rate(http_requests_total[1m]))` |
| Tokens issued | `rate(rtvoice_tokens_issued_total[5m])` |
| Audit queue depth (sum) | `sum(rtvoice_realtime_audit_queue_depth)` |

---

## 5. 测试矩阵

| 类别 | 文件 | # |
|---|---|---|
| SDK errors（mock httpx 401/422/503/4xx → typed exception 映射） | `clients/python/tests/test_errors.py` | 6 |
| SDK STT（mock httpx）transcribe + stream | `tests/test_stt.py` | 4 |
| SDK TTS（mock httpx）synthesize + stream | `tests/test_tts.py` | 4 |
| SDK Realtime（mock httpx + websockets）create_session / connect / conversation / update_*/clear_memory | `tests/test_realtime.py` | 8 |
| SDK Tokens | `tests/test_tokens.py` | 2 |
| SDK sync wrapper smoke | `tests/test_sync.py` | 2 |
| K：memory.py.clear() | 扩 test_memory.py | +2 |
| K：pipeline tts_client_dirty 重建 | 扩 test_pipeline_mock.py | +2 |
| K：main.py session.update voice/speed/memory.clear 路径 | 扩 test_endpoints.py | +3 |
| metrics：3 个新 gauge/counter 在 /metrics | 扩 test_endpoints.py | +1 |
| **新增小计** | | **34** |

总测试 SP3 后 52 → SP4 后 86+。

---

## 6. 验收标准

### 6.1 autonomous（沙盒 + prod）

- A1 `pip install -e clients/python/` → `from rtvoice_client import Client` 不抛
- A2 SDK：`Client(...).tts.synthesize("你好", ...)` 返回非空 bytes（mock）
- A3 SDK：超长 prompt → 抛 `PromptTooLong`
- A4 SDK：`async for evt in client.realtime.conversation(...)` mock 收到 4+ 类事件
- A5 K：WS `{type:"session.update","voice":"alice"}` → 下一 turn TTSClient 用新 voice（看 log）
- A6 K：WS `{type:"session.update","speed":3.0}` → 收 `validation.invalid_request` error
- A7 K：WS `{type:"memory.clear"}` → memory 清空，prompt 不变
- A8 仪表盘：`docker compose --profile monitoring up -d` 后 Grafana :3000 可访问，dashboard 加载
- A9 仪表盘：跑一 turn 后 `rtvoice_realtime_turns_total` ≥ 1
- A10 仪表盘：`rtvoice_realtime_sessions_active` 与 `/info` 一致

### 6.2 user-participation

- B1 [合 SP2/3 延期] 浏览器多轮对话验 memory 引用前文（真音质，避开合成音回灌识别率坑）
- B2 SDK：CozyVoice `pip install rtvoice-client` 切换调用栈，所有 STT/TTS/Realtime 流程能跑
- B3 仪表盘：登录 Grafana 看 RTVoice overview 全部面板有数据

---

## 7. 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| PyPI 包名 `rtvoice-client` 被抢注 | M | T1 立即 reserve（即便先 0.0.0 占名）|
| SDK API 设计错（用户用起来别扭） | H | semver 0.1.x alpha 起；CozyVoice 首批使用反馈快迭代 |
| Voice 切换 mid-turn 导致音色断层 | L | 已决：仅下一 turn 起生效；当前 turn TTS WS 不打断 |
| Grafana dashboard JSON 跟不上 metric schema | M | provisioning 文件 + reload 间隔 10s |
| Prometheus 数据卷膨胀 | L | retention=15d 配置 |
| Anonymous Grafana 暴露 | M | 默认 BIND_HOST=127.0.0.1；上公网必须加 reverse proxy auth（OPERATIONS.md 警告）|
| LLMClient 改签名后旧 e2e 测在 dev 跑不通 | L | mock 优先；`pytest.mark.e2e` 仅 prod 跑 |

---

## 8. 范围外（明确 NOT in SP4）

- 多用户认证 / OAuth / JWT —— 等真用户出现再设计（避免错抽象）
- RAG / 工具调用 —— SP5+ 范畴
- WebRTC / LiveKit 复活 —— agent-worker 维护模式继续
- Grafana 告警规则 / OpenTelemetry tracing —— 仅"看见"，不"叫醒"
- SDK Node/Go/Rust 版本 —— Python 优先验抽象
- session 持久化（Redis）—— 当前重启丢对话可接受
- transcript stable=true 标记 —— 等 STT API 调研

---

## 9. 实施切片建议（供 writing-plans 参考）

| Task | 子项 | 文件 | 测试 |
|---|---|---|---|
| T1 | SDK | `clients/python/` 骨架 + pyproject + LICENSE + README + py.typed | 0 |
| T2 | SDK | errors.py + 9 子类 + `_raise_for_code` 映射 | 6 |
| T3 | SDK | models.py + Pydantic v2 + RealtimeEvent union | 0 |
| T4 | SDK | _base.py + Bearer + per-service URL 解析 + httpx config | 0 |
| T5 | SDK | stt.py（mock httpx 测） | 4 |
| T6 | SDK | tts.py | 4 |
| T7 | SDK | realtime.py primitives + conversation helper | 8 |
| T8 | SDK | tokens.py | 2 |
| T9 | SDK | sync wrapper (`Client` 包 `AsyncClient` via `asyncio.run`) | 2 |
| T10 | SDK | tests/test_e2e_smoke.py（pytest.mark.e2e；真 prod） | 0 (CI optional) |
| T11 | K | memory.clear() + tests | +2 |
| T12 | K | session.update voice/speed + tts_client_dirty + main.py | +3 |
| T13 | K | pipeline 重建 TTSClient + tests | +2 |
| T14 | A | metrics.py + 3 个 metric + 接 SessionManager / pipeline | +1 |
| T15 | A | monitoring/prometheus.yml + grafana/datasources.yml + rtvoice-overview.json | 0 |
| T16 | A | docker-compose.yml + monitoring profile | 0 |
| T17 | docs | README / OPERATIONS / CONVENTIONS / sessions.md / SDK README | 0 |
| T18 | release | CHANGELOG v0.11.0 + push + （可选）PyPI publish | 0 |
| T19 | prod | 部署 + autonomous 验收 + user-participation checkpoint | 0 |

---

## 附录：相关文档

- 前置：[SP3 spec](./2026-05-09-sp3-realtime-memory-design.md) / [SP3 plan](../plans/2026-05-09-sp3-realtime-memory.md)
- API：[CONVENTIONS.md](../../api/CONVENTIONS.md) / [sessions.md](../../api/sessions.md) / [stt.md](../../api/stt.md) / [tts.md](../../api/tts.md)
- 平台：[platform_vision memory](/home/ubuntu/.claude/projects/-home-ubuntu-CozyProjects-RTVoice/memory/project_platform_vision.md)
