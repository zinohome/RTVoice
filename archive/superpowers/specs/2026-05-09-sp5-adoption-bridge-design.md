# SP5 Adoption Bridge — Web Demo + CORS + Deployment Friendly + Grafana Unblock Design

**日期**：2026-05-09
**前置**：SP4 (v0.11.0) 已 prod；116 测试 + Python SDK + 3 个 metrics + monitoring profile（Grafana 因 docker.io 镜像拉慢被 blocked）。
**目标版本**：v0.12.0
**作用域**：SP1-SP4 全 platform-side；SP5 是把"platform 已建好"推到"真用户能上手"的 adoption 桥梁。

---

## 1. 目标

SP4 prod 验收暴露三件事：
1. 0 真实用户 —— CozyVoice 等下游还没切 SDK 跑起来
2. 国内部署痛点 —— docker.io 镜像源限速（Grafana A6 blocked）；host pip 缺失
3. 浏览器 client 跨域被 CORS 拦 —— Web demo / 任何浏览器 SDK 不能直连

SP5 解决这三条，含 4 子项：

| 子项 | What | Why |
|---|---|---|
| **W · clients/web/** | 4-tab 纯 HTML/JS demo（STT / TTS / Realtime / Tokens） | 真用户上手第一站；showcase platform 全能力 |
| **C · CORS middleware** | realtime + stt + tts 加 FastAPI CORSMiddleware | 浏览器 client / 跨域 SDK 必需 |
| **D · Deployment friendly** | docker registry mirror docs + image tag/version bump 修正 | 国内部署 unblock + cosmetic 残留清理 |
| **G · Grafana A6 unblock** | mirror 拉镜像 + monitoring profile 启动 + 验证 dashboard | SP4 A6 真正完成 |

---

## 2. 关键决策（D-2026-05-09-C.1~C.7）

| ID | 决策 | 理由 |
|---|---|---|
| **C.1** | `clients/web/` 路径（与 `clients/python/` symmetric），纯 HTML/CSS/ES modules，零 build | 单文件可改可读；不引入 npm/vite 复杂度；与 SP3 静态测试页同款理念 |
| **C.2** | 4 tabs（STT / TTS / Realtime / Tokens）覆盖全 SDK 能力 | "完整 reference consumer"叙事 |
| **C.3** | realtime + stt + tts 加 CORS（不含 token-server） | LiveKit secret 暴露面减少；浏览器一般不直连 token |
| **C.4** | realtime-server image tag `v0.9.0` → `v0.12.0`；FastAPI app version 同步 | SP2 起没改导致 metrics / /info 一直显 0.9.0；SP5 一次性对齐 |
| **C.5** | Grafana A6 unblock 用 daocloud mirror 手动 pull + tag（不改 daemon.json） | 不重启 docker 不影响其他容器；可逆 |
| **C.6** | OPERATIONS.md §6 加国内部署 cookbook（含 mirror config + 单镜像 pull + 排障） | 国内 user 的反复痛点 |
| **C.7** | CORS 默认 `allow_origins=["*"]`, `allow_credentials=False` | demo / 跨平台友好；prod 收紧用 env `RTVOICE_CORS_ORIGINS=...` |

---

## 3. 架构 & 文件布局

```
RTVoice/
├── clients/
│   ├── python/                          (SP4 已建)
│   └── web/                             ← 新建（W）
│       ├── index.html                   入口（4 tab nav + config bar + log panel）
│       ├── styles.css                   minimal CSS
│       ├── README.md                    "open in browser" 用法
│       └── js/
│           ├── app.js                   tab 路由 + 公共工具
│           ├── config.js                base_url / bearer (localStorage)
│           ├── audio.js                 PCMPlayer + recordMic16kPCM
│           ├── stt.js                   STT tab
│           ├── tts.js                   TTS tab
│           ├── realtime.js              Realtime tab（mic + WS + 4 类事件渲染）
│           └── tokens.js                LiveKit Tokens tab
│
├── services/
│   ├── realtime-server/app/main.py      ★ +CORS middleware；version 0.12.0
│   ├── stt-server/app/main.py           ★ +CORS middleware
│   └── tts-server/app/main.py           ★ +CORS middleware
│
├── docker-compose.yml                    ★ realtime-server image tag v0.12.0；
│                                          3 服务 environment 加 RTVOICE_CORS_ORIGINS
├── .env.example                          ★ 顶部加部署提醒；CORS 段
└── OPERATIONS.md                         ★ 加 §6 国内部署 + Grafana 排障
```

**新文件**：~10（全在 clients/web/）；修改：~7；新依赖：0（FastAPI 内置 CORSMiddleware；浏览器侧零 npm）

---

## 4. 子项详细设计

### 4.1 W · clients/web/ HTML/JS demo

**全局 UI**：
```
┌────────────────────────────────────────┐
│ RTVoice Web Demo  [STT][TTS][Realtime][Tokens] │  ← tab nav
├────────────────────────────────────────┤
│ Config: API base / Bearer (localStorage) │
├────────────────────────────────────────┤
│  <selected tab content>                  │
│  Log panel (events stream)              │
└────────────────────────────────────────┘
```

**4 tabs 行为**：

| Tab | 输入 | 输出 |
|---|---|---|
| STT | Mic 录音（or upload wav）→ 16k mono int16 PCM | transcribe text；流式模式显示 partials |
| TTS | text input + voice/speed | Web Audio playback + bytes counter + download link |
| Realtime | 创 session（含 prompt / audit_persist）→ 连 WS → 录音 → EOS | transcript.partial+final / response.text deltas / agent PCM 边播 / session.update voice / memory.clear 按钮 |
| Tokens | identity / room / ttl 表单 | JWT 字符串 + LiveKit URL |

**Web Audio PCM playback (audio.js)** —— 服务器返裸 PCM 24k mono int16，需手构 AudioBuffer：

```javascript
class PCMPlayer {
    constructor(sampleRate=24000) {
        this.ctx = new AudioContext({sampleRate});
        this.queue = []; this.playing = false;
    }
    enqueue(int16Bytes) {
        const i16 = new Int16Array(int16Bytes);
        const f32 = new Float32Array(i16.length);
        for (let i=0; i<i16.length; i++) f32[i] = i16[i] / 0x8000;
        const buf = this.ctx.createBuffer(1, f32.length, this.ctx.sampleRate);
        buf.copyToChannel(f32, 0);
        this.queue.push(buf);
        if (!this.playing) this._drain();
    }
    _drain() {
        if (!this.queue.length) { this.playing = false; return; }
        this.playing = true;
        const buf = this.queue.shift();
        const src = this.ctx.createBufferSource();
        src.buffer = buf;
        src.connect(this.ctx.destination);
        src.onended = () => this._drain();
        src.start();
    }
}
```

**Mic 录音 → 16k PCM (audio.js)** —— ScriptProcessorNode（仍可用，AudioWorklet 留 SP6+）。

**配置持久化 (config.js)** —— `localStorage.rtvoice_base` / `rtvoice_bearer`。

**部署方式**：
- 本地：`cd clients/web/ && python3 -m http.server 8080` → http://localhost:8080
- co-host：通过 realtime-server `/static/` 路由（SP3 已支持，clients/web/ 复制一份过去如需）
- 生产：用户 nginx/Caddy 静态 serve clients/web/，CORS 跨域到后端（C 子项保证可行）

### 4.2 C · CORS Middleware

3 服务（realtime-server / stt-server / tts-server）main.py 顶端加：

```python
from fastapi.middleware.cors import CORSMiddleware

_cors_raw = os.environ.get("RTVOICE_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,         # "*" + credentials=True 浏览器拒
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
    max_age=3600,
)
```

**token-server 不加** —— 减少 LiveKit secret 暴露面。

**docker-compose.yml** 3 服务 environment 加：
```yaml
RTVOICE_CORS_ORIGINS: ${RTVOICE_CORS_ORIGINS:-*}
```

**.env.example**：
```bash
# CORS — 默认 * (dev 友好)；prod 收紧示例：
# RTVOICE_CORS_ORIGINS=https://app.example.com,https://demo.example.com
RTVOICE_CORS_ORIGINS=*
```

### 4.3 D · Deployment Friendly

**realtime-server image tag bump**（docker-compose.yml）：
```yaml
realtime-server:
  image: rtvoice/realtime-server:v0.12.0   # 之前 v0.9.0
```

**FastAPI app version 修正**（services/realtime-server/app/main.py）：
```python
app = FastAPI(
    title="RTVoice Realtime Voice Server",
    version="0.12.0",   # 之前 "0.9.0"
    ...
)
```

`/info` endpoint 返：
```python
"version": "0.12.0",
```

**.env.example 顶部部署提醒**：
```bash
# ============================================================
# 国内部署提示：docker.io 限速，建议配 registry-mirrors
# 详见 OPERATIONS.md §6
# ============================================================
```

**clients/python/README.md** 加 "Try inside RTVoice container" 段（host 没 pip 时）：

```bash
docker cp clients/python rtvoice-realtime:/tmp/sdk
docker exec rtvoice-realtime pip install -e /tmp/sdk
docker exec rtvoice-realtime python3 -c "from rtvoice_client import Client; ..."
```

### 4.4 G · Grafana A6 Unblock + OPERATIONS.md §6

**OPERATIONS.md §6 新章节**：

````markdown
## §6 国内部署：docker registry mirror

### 6.1 全局配（影响所有 docker pull）
```bash
sudo tee /etc/docker/daemon.json << 'EOF'
{"registry-mirrors": ["https://docker.m.daocloud.io", "https://hub.daocloud.io"]}
EOF
sudo systemctl restart docker  # ⚠️ 重启所有容器
```

### 6.2 单镜像手动拉（不重启 docker）
```bash
docker pull docker.m.daocloud.io/prom/prometheus:v3.0.0
docker tag docker.m.daocloud.io/prom/prometheus:v3.0.0 prom/prometheus:v3.0.0
docker pull docker.m.daocloud.io/grafana/grafana:11.4.0
docker tag docker.m.daocloud.io/grafana/grafana:11.4.0 grafana/grafana:11.4.0
docker compose --profile monitoring up -d
```

### 6.3 验证镜像源
```bash
docker info | grep -A 2 "Registry Mirrors"
```

### 6.4 Grafana / Prometheus 启动失败排障
```bash
# 看 prometheus targets 健康
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c "import sys,json; d=json.load(sys.stdin); [print(t['labels']['job'], t['health']) for t in d['data']['activeTargets']]"

# 服务侧 metrics
docker exec rtvoice-realtime curl -s http://127.0.0.1:9000/metrics | head -20

# 触发流量（使 metrics 有值）
docker exec rtvoice-realtime python3 -c "
from rtvoice_client import Client
c = Client(base_url='http://realtime-server:9000')
for _ in range(3): c.realtime.create_session()
c.close()
"

# Grafana 重 reload provisioning
docker exec rtvoice-grafana kill -HUP 1
```
````

**T15 prod 验收**实操（plan 内执行）：
```bash
# 1. mirror pull
ssh root@prod 'docker pull docker.m.daocloud.io/prom/prometheus:v3.0.0 && docker tag ...'

# 2. up monitoring
ssh root@prod 'cd /data/RTVoice && docker compose --profile monitoring up -d'

# 3. 验 prom targets / 跑流量 / 浏览器看 dashboard
```

---

## 5. 测试矩阵

| 类别 | 文件 | # |
|---|---|---|
| CORS preflight - realtime-server | 扩 `services/realtime-server/tests/test_endpoints.py` | +1 |
| CORS preflight - stt-server | 扩 `services/stt-server/tests/test_endpoints.py` | +1 |
| CORS preflight - tts-server | 扩 `services/tts-server/tests/test_endpoints.py` | +1 |
| `/info` version 0.12.0 | 扩 realtime-server `tests/test_endpoints.py` | +1 |
| **新增小计** | | **4** |

clients/web/ 不写自动化测试（HTML/JS demo，无 build chain）；user-participation 验。

总测试 SP4 后 116 → SP5 后 120+。

---

## 6. 验收标准

### 6.1 autonomous（沙盒 + prod）

- A1 3 服务 OPTIONS preflight 返 200 + `access-control-allow-origin` 头
- A2 跨 origin POST/GET 实际请求带 `Origin: http://x.com` 不被服务器拒
- A3 RTVOICE_CORS_ORIGINS 限制后，preflight from 不在列表的 origin → 浏览器拒
- A4 `/info` 返 `version: "0.12.0"`
- A5 `docker-compose.yml` `realtime-server.image: rtvoice/realtime-server:v0.12.0`
- A6 OPERATIONS.md §6 4 子节齐
- A7 clients/web/ 文件结构齐（10 文件）；index.html 浏览器渲染不报错
- A8 prod：daocloud mirror pull + tag → `--profile monitoring up -d` 后 prometheus + grafana 容器 healthy
- A9 prod：prometheus targets API 返 4 services 全 `up`
- A10 prod：Web demo 4 tabs 各对真 prod RTVoice 发请求，CORS 通过

### 6.2 user-participation

- B1 浏览器 Realtime tab：录音对话 → 听 agent 回复 + transcript.partial 流
- B2 浏览器 STT tab：上传 wav 或现场录音 → 看 text
- B3 浏览器 TTS tab：填中文 → 听合成音
- B4 浏览器 Tokens tab：填表单 → 收 JWT
- B5 Grafana http://prod:3000 → RTVoice Overview 8 面板有数据
- B6 CozyVoice：`pip install -e clients/python/` + import Client 跑通 STT/TTS

---

## 7. 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| CORS `*` + `allow_credentials=True` 浏览器拒 | M | 已设 `allow_credentials=False` |
| WebSocket origin 检查未做 | L | SP6+ 加；当前 prod 用 Bearer 鉴权够 |
| ScriptProcessorNode 浏览器 deprecated | L | demo 短期 OK；将来迁 AudioWorklet |
| daocloud mirror 限速 / 关停 | L | 备 hub.daocloud.io / 1panel；OPERATIONS.md 提"可换源" |
| image tag 0.9.0 → 0.12.0 让 prod 拉新镜像 | M | T15 force-recreate；旧镜像保留可回滚 |
| FastAPI version 字段 cosmetic | L | 仅 /info 显示；客户端不应路由依赖 |
| WS demo 与同 origin 限制冲突 | L | demo 文档说明：用 http server / nginx，不要 file:// |

---

## 8. 范围外（明确 NOT in SP5）

- WebSocket origin 校验 / CSRF token —— SP6+
- 多用户 OAuth/JWT —— 等真用户需求
- AudioWorklet 替代 ScriptProcessorNode
- web demo 移动端适配（桌面优先）
- web demo i18n / 暗色主题 / 各种花活
- CozyVoice 这个 user 项目的代码（SP5 只提供 SDK + docs + reference）
- Vite / TS build chain
- 完整告警规则 / OpenTelemetry tracing

---

## 9. 实施切片建议（供 writing-plans 参考）

| Task | 子项 | What | Tests |
|---|---|---|---|
| T1 | C | realtime-server CORS + 1 test | +1 |
| T2 | C | stt-server CORS + 1 test | +1 |
| T3 | C | tts-server CORS + 1 test | +1 |
| T4 | D | realtime-server image tag/version bump 0.12.0 + 1 test | +1 |
| T5 | D | docker-compose.yml + .env.example deployment 提示 | — |
| T6 | W | clients/web/ 骨架（index.html + styles.css + nav + config.js + README） | — |
| T7 | W | js/audio.js（PCMPlayer + recordMic16kPCM） | — |
| T8 | W | js/stt.js + STT tab UI | — |
| T9 | W | js/tts.js + TTS tab UI | — |
| T10 | W | js/realtime.js + Realtime tab UI（最大；含 mic + WS + 4 事件渲染 + session.update） | — |
| T11 | W | js/tokens.js + Tokens tab UI | — |
| T12 | W+D | clients/web/README.md + clients/python/README.md container exec 段 | — |
| T13 | D+G | OPERATIONS.md §6 docker mirror + 6.4 Grafana 排障 | — |
| T14 | release | CHANGELOG v0.12.0 + push | — |
| T15 | prod | autonomous A1-A10 + Grafana A6 mirror unblock + user-participation 通知 | — |

15 任务（中等规模，介于 SP3 12 和 SP4 19 之间）；新增测试 4。

---

## 附录：相关文档

- 前置：[SP4 spec](./2026-05-09-sp4-bridge-bundle-design.md) / [SP4 plan](../plans/2026-05-09-sp4-bridge-bundle.md)
- API：[CONVENTIONS.md](../../api/CONVENTIONS.md)
- SDK README：[clients/python/README.md](../../../clients/python/README.md)
