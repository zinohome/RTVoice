# SP5 Adoption Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RTVoice 从"platform 已建好"推到"真用户能上手"——4 子项一个 v0.12.0 release：clients/web/ 4-tab demo + 3 服务 CORS + 部署友好性补丁 + Grafana A6 unblock。

**Architecture:** Web demo 是纯 HTML/CSS/ES modules（零 build），所有 tab HTML 静态写在 `index.html` 里，JS 只 wire 事件 handlers（避免 innerHTML 安全风险）。CORS middleware 加进 realtime/stt/tts 三服务（FastAPI 内置）。Deployment 友好性是 docker mirror docs + image tag/version 修正。Grafana unblock 通过 daocloud mirror 手动 pull。

**Tech Stack:** FastAPI CORSMiddleware / 浏览器原生 Web Audio API + WebSocket / docker registry mirror

**Spec:** [docs/superpowers/specs/2026-05-09-sp5-adoption-bridge-design.md](../specs/2026-05-09-sp5-adoption-bridge-design.md)

---

## Task 1: realtime-server CORS Middleware + 测试

**Files:**
- Modify: `services/realtime-server/app/main.py`
- Modify: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: 在 `services/realtime-server/tests/test_endpoints.py` 末尾追加 2 测试**

```python
def test_cors_preflight_returns_acao(client):
    """OPTIONS preflight 返 ACAO 头（默认 *）"""
    r = client.options("/v1/sessions", headers={
        "Origin": "http://example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization,Content-Type",
    })
    assert r.status_code == 200
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert "access-control-allow-origin" in headers
    assert headers["access-control-allow-origin"] in ("*", "http://example.com")


def test_cors_actual_request_has_acao_header(client):
    """实际 GET 请求带 Origin → response 含 ACAO"""
    r = client.get("/info", headers={"Origin": "http://example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in r.headers}
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "cors" -v
```

Expected: 2 fail。

- [ ] **Step 3: 改 `services/realtime-server/app/main.py`**

3a. 在 `from fastapi import (...)` 后追加：

```python
from fastapi.middleware.cors import CORSMiddleware
```

3b. 在 `app = FastAPI(...)` 之后、`app.add_exception_handler(...)` 之前插入：

```python
_cors_raw = os.environ.get("RTVOICE_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
    max_age=3600,
)
```

注：main.py 顶部已 `import os`（SP3 起）。

- [ ] **Step 4: 跑测试 + 全套**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "cors" -v
python3 -m pytest tests/ -v 2>&1 | tail -10
```

Expected: 全过。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/main.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): CORS middleware (T1)

- FastAPI CORSMiddleware；env RTVOICE_CORS_ORIGINS（默认 *，prod 收紧）
- allow_credentials=False（避免 * + credentials 浏览器拒）
- +2 单元测试

per spec D-2026-05-09-C.3"
```

---

## Task 2: stt-server CORS Middleware

**Files:**
- Modify: `services/stt-server/app/main.py`

注：stt-server 仓库内**无单元测试目录**；CORS 验证留 T15 prod E2E（不新建 tests dir，避免 scope creep）。

- [ ] **Step 1: 改 `services/stt-server/app/main.py`**

1a. 找 import 段顶部 `from fastapi import ...` 后追加：

```python
from fastapi.middleware.cors import CORSMiddleware
```

1b. 找 `app = FastAPI(...)` 实例化行**之后**插入：

```python
_cors_raw = os.environ.get("RTVOICE_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
    max_age=3600,
)
```

注：如顶部缺 `import os`，加（Python stdlib）。

- [ ] **Step 2: syntax check**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "import ast; ast.parse(open('services/stt-server/app/main.py').read())" && echo OK
```

Expected: `OK`。

- [ ] **Step 3: Commit**

```bash
git add services/stt-server/app/main.py
git commit -m "feat(stt-server): CORS middleware (T2)

- FastAPI CORSMiddleware；同 realtime-server 配置
- 沙盒无 tests dir；CORS 验证留 prod T15 E2E

per spec D-2026-05-09-C.3"
```

---

## Task 3: tts-server CORS Middleware（3 entry points 都加）

**Files:**
- Modify: `services/tts-server/app/main.py`
- Modify: `services/tts-server/app/main_cosyvoice.py`
- Modify: `services/tts-server/app/main_cosyvoice3.py`

注：tts-server 有 3 个 entry points（Kokoro / CosyVoice2 / CosyVoice3，看 Dockerfile.* CMD）。3 个都加保持一致。

- [ ] **Step 1: 改 `services/tts-server/app/main.py`**

同 T2 模式：在 `from fastapi import ...` 后追加 `from fastapi.middleware.cors import CORSMiddleware`；在 `app = FastAPI(...)` 之后插入：

```python
_cors_raw = os.environ.get("RTVOICE_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
    max_age=3600,
)
```

如缺 `import os` 则加。

- [ ] **Step 2: 同样改 `services/tts-server/app/main_cosyvoice.py`**

- [ ] **Step 3: 同样改 `services/tts-server/app/main_cosyvoice3.py`**

- [ ] **Step 4: syntax check 3 文件**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in services/tts-server/app/main.py services/tts-server/app/main_cosyvoice.py services/tts-server/app/main_cosyvoice3.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK $f" || echo "FAIL $f"
done
```

Expected: 3 OK。

- [ ] **Step 5: Commit**

```bash
git add services/tts-server/app/main.py services/tts-server/app/main_cosyvoice.py services/tts-server/app/main_cosyvoice3.py
git commit -m "feat(tts-server): CORS middleware (T3)

- 3 个 entry (main.py Kokoro / main_cosyvoice / main_cosyvoice3) 都加
- 配置同 realtime/stt-server
- 沙盒无 tests dir；CORS 验证留 prod T15 E2E

per spec D-2026-05-09-C.3"
```

---

## Task 4: realtime-server image tag/version bump v0.9.0 → v0.12.0

**Files:**
- Modify: `services/realtime-server/app/main.py`
- Modify: `docker-compose.yml`
- Modify: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: 写新测试**

在 `services/realtime-server/tests/test_endpoints.py` 末尾追加：

```python
def test_info_version_is_0_12_0(client):
    r = client.get("/info")
    assert r.status_code == 200
    assert r.json()["version"] == "0.12.0"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "info_version" -v
```

Expected: FAIL（assert "0.9.0" == "0.12.0"）。

- [ ] **Step 3: 改 main.py**

3a. 在 `app = FastAPI(...)` 调用中：
- 找：`version="0.9.0",`
- 改为：`version="0.12.0",`

3b. 在 `info()` 函数返回字典中：
- 找：`"version": "0.9.0",`
- 改为：`"version": "0.12.0",`

- [ ] **Step 4: 改 docker-compose.yml**

定位 `realtime-server:` service block 内 `image:` 行：

```yaml
    image: rtvoice/realtime-server:v0.9.0
```

改为：

```yaml
    image: rtvoice/realtime-server:v0.12.0
```

- [ ] **Step 5: 跑测试 + compose 验证**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v 2>&1 | tail -10

cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "
import yaml
d = yaml.safe_load(open('docker-compose.yml'))
assert d['services']['realtime-server']['image'] == 'rtvoice/realtime-server:v0.12.0'
print('image tag OK')
"
```

Expected: 全过；image tag OK。

- [ ] **Step 6: Commit**

```bash
git add services/realtime-server/app/main.py docker-compose.yml services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): image tag/version bump 0.9.0 → 0.12.0 (T4)

- main.py FastAPI app version + /info 同步 0.12.0
- docker-compose.yml image tag bump

注：SP2 起 image tag 一直没改；SP3/SP4 build 都覆盖同 tag 导致版本字段误导。
SP5 一次性对齐。

+1 单元测试。
per spec D-2026-05-09-C.4"
```

---

## Task 5: docker-compose.yml CORS env + .env.example 部署提醒

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: docker-compose.yml 3 服务 environment 加 CORS env**

在 `realtime-server`、`stt-server`、`tts-server` 三个 service 的 `environment:` 段，各追加一行：

```yaml
      RTVOICE_CORS_ORIGINS: ${RTVOICE_CORS_ORIGINS:-*}
```

- [ ] **Step 2: .env.example 改动**

2a. 在文件**最顶部**插入部署提醒：

```bash
# ============================================================
# 国内部署提示：docker.io 限速，建议配 registry-mirrors
# 详见 OPERATIONS.md §6
# ============================================================

```

2b. 在文件中合适位置（如 SP4 段后）追加 CORS 段：

```bash
# ============================================================
# CORS (SP5)
# ============================================================
# 默认 * 是 demo/dev 友好；prod 收紧示例：
# RTVOICE_CORS_ORIGINS=https://app.example.com,https://demo.example.com
RTVOICE_CORS_ORIGINS=*
```

- [ ] **Step 3: 验证 compose YAML**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "
import yaml
d = yaml.safe_load(open('docker-compose.yml'))
for svc in ('realtime-server', 'stt-server', 'tts-server'):
    env = d['services'][svc].get('environment', {})
    assert 'RTVOICE_CORS_ORIGINS' in env, f'{svc} missing RTVOICE_CORS_ORIGINS'
    print(f'{svc}: OK')
"
```

Expected: 3 OK。

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(compose): RTVOICE_CORS_ORIGINS env + 部署提醒 (T5)

- 3 服务 environment 加 RTVOICE_CORS_ORIGINS
- .env.example 顶部部署提醒；CORS 段

per spec §4.2 + 4.3"
```

---

## Task 6: clients/web/ 骨架 — index.html 含 4 tabs 静态 HTML + 共用 JS 框架

**Files:**
- Create: `clients/web/index.html`
- Create: `clients/web/styles.css`
- Create: `clients/web/README.md`
- Create: `clients/web/js/app.js`
- Create: `clients/web/js/config.js`

**关键设计**：4 tab 的所有 HTML 内容**直接写在 `index.html` 里**（4 个 `<section>`），JS 只用 `getElementById` + `addEventListener` 接事件。**不用 innerHTML**（避免 XSS 风险 + 通过安全检查）。

- [ ] **Step 1: 创建目录**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p clients/web/js
```

- [ ] **Step 2: 写 `clients/web/index.html`（含 4 tab 完整静态 HTML）**

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>RTVoice Web Demo</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header>
    <h1>RTVoice Web Demo</h1>
    <nav>
      <button class="tab" data-tab="stt">STT</button>
      <button class="tab" data-tab="tts">TTS</button>
      <button class="tab" data-tab="realtime">Realtime</button>
      <button class="tab" data-tab="tokens">Tokens</button>
    </nav>
  </header>

  <section id="config">
    <label>API base: <input id="cfg-base" size="40"></label>
    <label>Bearer (空=dev): <input id="cfg-bearer" size="40" type="password"></label>
    <button id="cfg-save">Save</button>
    <span id="cfg-status"></span>
  </section>

  <main id="content">
    <!-- ============== STT tab ============== -->
    <section id="tab-stt" class="tab-content">
      <h2>STT — Speech to Text</h2>
      <p>录一段中文音频 → 调 POST /v1/asr 拿识别 text</p>
      <div class="row">
        <button id="stt-record">🎙️ 开始录音</button>
        <button id="stt-stop" disabled>⏹ 结束并识别</button>
        <span id="stt-status" class="muted"></span>
      </div>
      <div class="row">
        <label>结果：</label>
        <input id="stt-result" type="text" readonly placeholder="(结果会显示在这里)" />
      </div>
    </section>

    <!-- ============== TTS tab ============== -->
    <section id="tab-tts" class="tab-content">
      <h2>TTS — Text to Speech</h2>
      <p>填文字 → POST /v1/tts/stream → Web Audio 流式播放（24k mono int16）</p>
      <div class="row">
        <textarea id="tts-text" rows="3" placeholder="输入要合成的文字...">你好世界，欢迎使用 RTVoice 平台。</textarea>
      </div>
      <div class="row">
        <label>Voice: <input id="tts-voice" type="text" value="default_zh_female" size="20"></label>
        <label>Speed: <input id="tts-speed" type="number" value="1.0" step="0.1" min="0.5" max="2.0" style="width: 70px"></label>
        <label>Lang: <input id="tts-lang" type="text" value="cmn" size="6"></label>
      </div>
      <div class="row">
        <button id="tts-go">▶ 合成 + 播放</button>
        <button id="tts-stop-btn" disabled>⏹ 停止</button>
        <span id="tts-status" class="muted"></span>
      </div>
      <div class="row" id="tts-meta" class="muted"></div>
    </section>

    <!-- ============== Realtime tab ============== -->
    <section id="tab-realtime" class="tab-content">
      <h2>Realtime — 完整对话</h2>
      <p>POST /v1/sessions 创会话 → WS /v1/realtime/{id} → mic 录音 → audio.eos → 听 agent 回复</p>

      <fieldset>
        <legend>1. 创建 session</legend>
        <div class="row">
          <label>Prompt:</label>
          <textarea id="rt-prompt" rows="2" placeholder="(空 = server default)"></textarea>
        </div>
        <div class="row">
          <label>Voice: <input id="rt-voice" type="text" value="default_zh_female" size="20"></label>
          <label>Speed: <input id="rt-speed" type="number" value="1.0" step="0.1" min="0.5" max="2.0" style="width:70px"></label>
          <label><input type="checkbox" id="rt-audit"> audit_persist</label>
        </div>
        <div class="row">
          <button id="rt-create">1) 创建</button>
          <span id="rt-session-info" class="muted"></span>
        </div>
      </fieldset>

      <fieldset>
        <legend>2. 连接 + 录音</legend>
        <div class="row">
          <button id="rt-connect" disabled>2) 连 WS</button>
          <button id="rt-record" disabled>3) 开始录音</button>
          <button id="rt-eos" disabled>4) 结束 turn (EOS)</button>
          <button id="rt-disconnect" disabled>断开</button>
        </div>
      </fieldset>

      <fieldset>
        <legend>3. 中途控制</legend>
        <div class="row">
          <button id="rt-update-prompt" disabled>改 prompt</button>
          <button id="rt-update-voice" disabled>改 voice</button>
          <button id="rt-clear-memory" disabled>memory.clear</button>
        </div>
      </fieldset>

      <fieldset>
        <legend>4. 流式显示</legend>
        <div id="rt-transcript" class="rt-stream user-color"></div>
        <div id="rt-response" class="rt-stream agent-color"></div>
      </fieldset>
    </section>

    <!-- ============== Tokens tab ============== -->
    <section id="tab-tokens" class="tab-content">
      <h2>Tokens — LiveKit JWT</h2>
      <p>POST /v1/tokens（token-server :8000）申请 LiveKit room JWT</p>
      <div class="row">
        <label>Identity: <input id="tk-identity" type="text" value="alice"></label>
        <label>Room: <input id="tk-room" type="text" value="rtvoice-test"></label>
        <label>TTL minutes: <input id="tk-ttl" type="number" value="10" min="1" max="1440" style="width:80px"></label>
      </div>
      <div class="row">
        <button id="tk-go">申请 token</button>
        <span id="tk-status" class="muted"></span>
      </div>
      <div class="row">
        <label>Token:</label>
        <textarea id="tk-token" rows="3" readonly placeholder="(JWT 显示在这里)"></textarea>
      </div>
      <div class="row">
        <label>LiveKit URL:</label>
        <input id="tk-url" type="text" readonly>
      </div>
    </section>
  </main>

  <aside id="log-panel">
    <h3>Event Log</h3>
    <div id="log"></div>
  </aside>

  <script type="module" src="js/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: 写 `clients/web/styles.css`**

```css
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  margin: 0; padding: 0;
  display: grid;
  grid-template-columns: 1fr 360px;
  grid-template-rows: auto auto 1fr;
  min-height: 100vh;
}
header {
  grid-column: 1 / 3;
  padding: 1em;
  background: #1f2937; color: #fff;
  display: flex; align-items: center; gap: 1em;
}
header h1 { margin: 0; font-size: 1.2em; }
nav { display: flex; gap: 0.3em; }
nav .tab {
  padding: 0.5em 1em;
  background: #374151; color: #fff; border: none;
  cursor: pointer; border-radius: 4px;
}
nav .tab.active { background: #2563eb; }
#config {
  grid-column: 1 / 3;
  padding: 0.5em 1em;
  background: #f3f4f6;
  display: flex; gap: 1em; align-items: center;
  border-bottom: 1px solid #e5e7eb;
}
#config input { font-family: monospace; font-size: 12px; padding: 4px; }
#cfg-status { color: #059669; font-size: 12px; }
main { padding: 1em; overflow-y: auto; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.tab-content h2 { margin-top: 0; }
button { font-size: 14px; padding: 6px 12px; cursor: pointer; }
input[type=text], input[type=number], input[type=password], textarea {
  font-size: 14px; padding: 6px; width: 100%; max-width: 400px;
}
.row { margin: 0.5em 0; display: flex; gap: 0.5em; align-items: center; flex-wrap: wrap; }
.muted { color: #6b7280; font-size: 12px; }
fieldset { margin: 1em 0; padding: 0.5em 1em; border: 1px solid #e5e7eb; border-radius: 4px; }
legend { font-weight: bold; padding: 0 0.5em; }
.rt-stream {
  font-family: monospace; font-size: 13px;
  border: 1px solid #e5e7eb; padding: 0.5em;
  min-height: 30px; margin-top: 0.5em;
  white-space: pre-wrap; word-break: break-word;
}
.user-color { color: #059669; }
.agent-color { color: #2563eb; }
#log-panel {
  background: #f9fafb; border-left: 1px solid #e5e7eb;
  padding: 1em; overflow-y: auto;
  height: calc(100vh - 130px);
}
#log-panel h3 { margin-top: 0; font-size: 0.9em; color: #6b7280; }
#log { font-family: monospace; font-size: 11px; white-space: pre-wrap; }
#log .user { color: #059669; }
#log .agent { color: #2563eb; }
#log .evt { color: #6b7280; }
#log .err { color: #dc2626; }
```

- [ ] **Step 4: 写 `clients/web/js/config.js`**

```javascript
const cfg = {
  get base() { return localStorage.getItem("rtvoice_base") || "http://127.0.0.1:9000"; },
  set base(v) { localStorage.setItem("rtvoice_base", v); },
  get bearer() { return localStorage.getItem("rtvoice_bearer") || ""; },
  set bearer(v) { localStorage.setItem("rtvoice_bearer", v); },
  authHeaders() {
    return this.bearer ? { Authorization: `Bearer ${this.bearer}` } : {};
  },
};
export default cfg;
```

- [ ] **Step 5: 写 `clients/web/js/app.js`**

```javascript
import cfg from "./config.js";
import { setupSTT } from "./stt.js";
import { setupTTS } from "./tts.js";
import { setupRealtime } from "./realtime.js";
import { setupTokens } from "./tokens.js";

export function log(cls, msg) {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = msg;
  const panel = document.getElementById("log");
  panel.appendChild(d);
  panel.scrollTop = panel.scrollHeight;
}

const baseInput = document.getElementById("cfg-base");
const bearerInput = document.getElementById("cfg-bearer");
const saveBtn = document.getElementById("cfg-save");
const status = document.getElementById("cfg-status");

baseInput.value = cfg.base;
bearerInput.value = cfg.bearer;
saveBtn.addEventListener("click", () => {
  cfg.base = baseInput.value.trim();
  cfg.bearer = bearerInput.value.trim();
  status.textContent = "saved ✓";
  setTimeout(() => (status.textContent = ""), 1500);
  log("evt", `config saved: base=${cfg.base}`);
});

const tabs = document.querySelectorAll("nav .tab");
const contents = document.querySelectorAll(".tab-content");
tabs.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    contents.forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

setupSTT();
setupTTS();
setupRealtime();
setupTokens();

tabs[0].click();
log("evt", "RTVoice Web Demo loaded");
```

- [ ] **Step 6: 写 `clients/web/README.md`**

```markdown
# RTVoice Web Demo

纯 HTML/CSS/ES modules，零 build 链。

## 运行

### 本地（推荐）
```bash
cd clients/web/
python3 -m http.server 8080
# 浏览器开 http://localhost:8080/
```

### 通过 nginx 静态部署
将整个 `clients/web/` 目录拷到 nginx web root。

### 通过 RTVoice realtime-server co-host
```bash
docker cp clients/web rtvoice-realtime:/app/static/web
# 浏览器 http://${host}:9000/static/web/
```

## 配置

页面顶部 config bar：

- **API base**: `http://your-rtvoice-host:9000`（默认 `http://127.0.0.1:9000`）
- **Bearer**: dev 模式留空；prod 填 `RTVOICE_API_KEY`

存 `localStorage.rtvoice_base` / `localStorage.rtvoice_bearer`。

## 4 Tabs

| Tab | 演示 |
|---|---|
| STT | Mic 录音 → /v1/asr 一次性识别 |
| TTS | 中文 → /v1/tts/stream → Web Audio 播放 |
| Realtime | 完整对话流（含 transcript.partial / response.text 流式渲染 + session.update + memory.clear） |
| Tokens | LiveKit token 申请 |

## 浏览器要求

- Chrome / Edge / Firefox / Safari ≥2024
- HTTPS 或 localhost（mic 权限必需）
- WebSocket / Web Audio API 支持

## CORS

后端默认 `*`（dev 友好）；prod 收紧用 env `RTVOICE_CORS_ORIGINS`。
```

- [ ] **Step 7: 写占位 4 个 tab JS（防 import error；T8-T11 实现）**

`clients/web/js/stt.js`:
```javascript
export function setupSTT() {
  // implemented in T8
}
```

`clients/web/js/tts.js`:
```javascript
export function setupTTS() {
  // implemented in T9
}
```

`clients/web/js/realtime.js`:
```javascript
export function setupRealtime() {
  // implemented in T10
}
```

`clients/web/js/tokens.js`:
```javascript
export function setupTokens() {
  // implemented in T11
}
```

- [ ] **Step 8: 浏览器自检 — Python http server 起一下看渲染**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/web/
( python3 -m http.server 8080 & ) 2>/dev/null
sleep 1
curl -sI http://localhost:8080/index.html | head -3
curl -s http://localhost:8080/index.html | grep -c 'tab-stt' && echo "stt tab section present"
curl -s http://localhost:8080/index.html | grep -c 'tab-tokens' && echo "tokens tab section present"
pkill -f "http.server 8080" 2>/dev/null
```

Expected: HTTP/1.0 200；section 节点 ≥1。

- [ ] **Step 9: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/
git commit -m "feat(web): clients/web/ 骨架 + 4 tab 静态 HTML (T6)

- index.html: 4 tab 完整静态结构（STT/TTS/Realtime/Tokens）+ config bar + log panel
  所有 HTML 直接写在 index.html，避免 JS innerHTML（XSS 风险）
- styles.css: minimal grid layout + tab nav + monospace log
- js/app.js: tab 路由 + log 工具 + config localStorage（addEventListener）
- js/config.js: cfg.base/bearer + authHeaders()
- js/{stt,tts,realtime,tokens}.js: 占位 setup* 函数（T8-T11 实现）
- README.md: 3 种部署方式 + 浏览器要求

per spec §4.1 + D-2026-05-09-C.1"
```

---

## Task 7: js/audio.js — Web Audio PCM Player + Mic Recorder

**Files:**
- Create: `clients/web/js/audio.js`

- [ ] **Step 1: 写 `clients/web/js/audio.js`**

```javascript
// Web Audio helpers: PCM playback + mic recording

export class PCMPlayer {
  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
    this.ctx = null;
    this.queue = [];
    this.playing = false;
  }

  ensureCtx() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: this.sampleRate,
      });
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  }

  enqueue(int16ArrayBuffer) {
    this.ensureCtx();
    const i16 = new Int16Array(int16ArrayBuffer);
    if (i16.length === 0) return;
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
    const buf = this.ctx.createBuffer(1, f32.length, this.sampleRate);
    buf.copyToChannel(f32, 0);
    this.queue.push(buf);
    if (!this.playing) this._drain();
  }

  _drain() {
    if (!this.queue.length) {
      this.playing = false;
      return;
    }
    this.playing = true;
    const buf = this.queue.shift();
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    src.onended = () => this._drain();
    src.start();
  }

  reset() {
    this.queue = [];
    this.playing = false;
  }

  async close() {
    this.reset();
    if (this.ctx) {
      try { await this.ctx.close(); } catch {}
      this.ctx = null;
    }
  }
}


// Mic 录音 → 16k mono int16 PCM chunks（流式回调）
// 返 stop 函数。
export async function recordMic16kPCM(onChunk) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  proc.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = v * 0x7FFF;
    }
    onChunk(i16.buffer);
  };
  src.connect(proc);
  proc.connect(ctx.destination);

  return async () => {
    try { proc.disconnect(); } catch {}
    try { src.disconnect(); } catch {}
    stream.getTracks().forEach((t) => t.stop());
    try { await ctx.close(); } catch {}
  };
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/js/audio.js
git commit -m "feat(web): audio.js (PCMPlayer + recordMic16kPCM) (T7)

- PCMPlayer: 24k mono int16 流式顺序播放（lazy AudioContext）
- recordMic16kPCM: 16k mono int16 PCM via getUserMedia + ScriptProcessor

per spec §4.1（Web Audio playback / mic recording）"
```

---

## Task 8: js/stt.js + STT tab 事件 wiring

**Files:**
- Modify: `clients/web/js/stt.js`

- [ ] **Step 1: 重写 `clients/web/js/stt.js`**

注：HTML 已在 T6 index.html 内；本步只写 event wiring。

```javascript
import cfg from "./config.js";
import { log } from "./app.js";
import { recordMic16kPCM } from "./audio.js";

export function setupSTT() {
  const recBtn = document.getElementById("stt-record");
  const stopBtn = document.getElementById("stt-stop");
  const status = document.getElementById("stt-status");
  const resultIn = document.getElementById("stt-result");

  let stopRec = null;
  let chunks = [];

  recBtn.addEventListener("click", async () => {
    chunks = [];
    try {
      stopRec = await recordMic16kPCM((buf) => {
        chunks.push(new Int16Array(buf));
      });
      recBtn.disabled = true;
      stopBtn.disabled = false;
      status.textContent = "录音中…";
      log("evt", "STT: recording started");
    } catch (e) {
      log("err", `STT: mic 失败: ${e.message}`);
    }
  });

  stopBtn.addEventListener("click", async () => {
    if (stopRec) await stopRec();
    stopRec = null;
    recBtn.disabled = false;
    stopBtn.disabled = true;
    status.textContent = "识别中…";

    const total = chunks.reduce((s, c) => s + c.length, 0);
    const merged = new Int16Array(total);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }
    log("evt", `STT: captured ${merged.byteLength} bytes`);

    try {
      const r = await fetch(`${cfg.base.replace(/:9000$/, ":9090")}/v1/asr?sample_rate=16000`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          ...cfg.authHeaders(),
        },
        body: merged.buffer,
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `STT: HTTP ${r.status} ${body.slice(0, 100)}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      const j = await r.json();
      resultIn.value = j.text || "";
      log("user", `STT: ${j.text}`);
      status.textContent = "完成";
    } catch (e) {
      log("err", `STT: ${e.message}`);
      status.textContent = "失败";
    }
  });
}
```

注：`cfg.base.replace(/:9000$/, ":9090")` 是简陋的「从 realtime port 推断 stt port」启发。用户 prod 反代时直接填 STT base（YAGNI 暂不加单独配置）。

- [ ] **Step 2: 浏览器自检（HTML + JS 加载）**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/web/
( python3 -m http.server 8080 & ) 2>/dev/null
sleep 1
curl -s http://localhost:8080/js/stt.js | grep -c "addEventListener" && echo "STT JS has listeners"
pkill -f "http.server 8080" 2>/dev/null
```

Expected: ≥2 行 addEventListener。

- [ ] **Step 3: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/js/stt.js
git commit -m "feat(web): STT tab event wiring (T8)

- 录音 → 16k int16 PCM merged → POST /v1/asr → 显示 text
- STT base 推断 :9000 → :9090（dev 沙盒约定）

per spec §4.1"
```

---

## Task 9: js/tts.js + TTS tab 事件 wiring

**Files:**
- Modify: `clients/web/js/tts.js`

- [ ] **Step 1: 重写 `clients/web/js/tts.js`**

```javascript
import cfg from "./config.js";
import { log } from "./app.js";
import { PCMPlayer } from "./audio.js";

export function setupTTS() {
  const goBtn = document.getElementById("tts-go");
  const stopBtn = document.getElementById("tts-stop-btn");
  const status = document.getElementById("tts-status");
  const meta = document.getElementById("tts-meta");

  let player = null;
  let abortCtl = null;

  goBtn.addEventListener("click", async () => {
    const text = document.getElementById("tts-text").value;
    const voice = document.getElementById("tts-voice").value;
    const speed = parseFloat(document.getElementById("tts-speed").value);
    const lang = document.getElementById("tts-lang").value;
    if (!text.trim()) {
      log("err", "TTS: empty text");
      return;
    }
    goBtn.disabled = true;
    stopBtn.disabled = false;
    status.textContent = "请求中…";
    meta.textContent = "";
    if (player) await player.close();
    player = new PCMPlayer(24000);
    abortCtl = new AbortController();

    let totalBytes = 0;
    const startedAt = Date.now();
    try {
      const r = await fetch(`${cfg.base.replace(/:9000$/, ":9880")}/v1/tts/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...cfg.authHeaders(),
        },
        body: JSON.stringify({ text, voice, speed, lang }),
        signal: abortCtl.signal,
      });
      if (!r.ok) {
        log("err", `TTS: HTTP ${r.status}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      log("evt", "TTS: stream open");
      const reader = r.body.getReader();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value && value.byteLength) {
          player.enqueue(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
          totalBytes += value.byteLength;
          status.textContent = `播放中… (${totalBytes} bytes)`;
        }
      }
      const elapsed = ((Date.now() - startedAt) / 1000).toFixed(2);
      meta.textContent = `共 ${totalBytes} bytes，stream 用时 ${elapsed}s`;
      log("agent", `TTS done: ${totalBytes} bytes`);
      status.textContent = "完成（播放中）";
    } catch (e) {
      if (e.name === "AbortError") {
        log("evt", "TTS: aborted by user");
        status.textContent = "已停止";
      } else {
        log("err", `TTS: ${e.message}`);
        status.textContent = "失败";
      }
    } finally {
      goBtn.disabled = false;
      stopBtn.disabled = true;
    }
  });

  stopBtn.addEventListener("click", () => {
    if (abortCtl) abortCtl.abort();
    if (player) player.reset();
  });
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/js/tts.js
git commit -m "feat(web): TTS tab event wiring (T9)

- 文本 + voice/speed/lang → POST /v1/tts/stream
- ReadableStream chunks → PCMPlayer 边收边播
- AbortController 中途停止
- 显示总字节数 + stream 时长

per spec §4.1"
```

---

## Task 10: js/realtime.js + Realtime tab 事件 wiring（最大）

**Files:**
- Modify: `clients/web/js/realtime.js`

- [ ] **Step 1: 重写 `clients/web/js/realtime.js`**

```javascript
import cfg from "./config.js";
import { log } from "./app.js";
import { PCMPlayer, recordMic16kPCM } from "./audio.js";

export function setupRealtime() {
  let session = null;
  let ws = null;
  let stopRec = null;
  let player = null;

  const $ = (id) => document.getElementById(id);
  const tx = $("rt-transcript");
  const rx = $("rt-response");

  $("rt-create").addEventListener("click", async () => {
    const promptVal = $("rt-prompt").value.trim() || undefined;
    const voiceVal = $("rt-voice").value.trim() || undefined;
    const speedVal = parseFloat($("rt-speed").value);
    const auditVal = $("rt-audit").checked;
    try {
      const r = await fetch(`${cfg.base}/v1/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...cfg.authHeaders() },
        body: JSON.stringify({ prompt: promptVal, voice: voiceVal, speed: speedVal, audit_persist: auditVal }),
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `create_session: HTTP ${r.status} ${body.slice(0, 200)}`);
        return;
      }
      session = await r.json();
      $("rt-session-info").textContent = `session=${session.session_id} | audit=${session.audit_persist}`;
      log("evt", `session created: ${session.session_id}`);
      $("rt-connect").disabled = false;
    } catch (e) {
      log("err", `create_session: ${e.message}`);
    }
  });

  $("rt-connect").addEventListener("click", () => {
    let wsUrl = session.ws_url;
    try {
      const cfgU = new URL(cfg.base);
      const wsU = new URL(wsUrl);
      wsU.protocol = cfgU.protocol === "https:" ? "wss:" : "ws:";
      wsU.hostname = cfgU.hostname;
      wsU.port = cfgU.port || (cfgU.protocol === "https:" ? "443" : "80");
      wsUrl = wsU.toString();
    } catch (e) {
      log("err", `ws_url 解析失败: ${e.message}`);
      return;
    }
    const protocols = cfg.bearer ? [`bearer.${cfg.bearer}`] : [];
    ws = new WebSocket(wsUrl, protocols);
    ws.binaryType = "arraybuffer";

    if (player) player.close();
    player = new PCMPlayer(24000);

    ws.onopen = () => {
      log("evt", "ws open");
      tx.textContent = ""; rx.textContent = "";
      ["rt-record", "rt-eos", "rt-disconnect", "rt-update-prompt", "rt-update-voice", "rt-clear-memory"].forEach(
        (id) => ($(id).disabled = false)
      );
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        let ev;
        try { ev = JSON.parse(e.data); } catch { log("err", `non-json: ${e.data.slice(0, 80)}`); return; }
        const t = ev.type;
        if (t === "transcript.partial") tx.textContent = `[partial] ${ev.text}`;
        else if (t === "transcript.final") { tx.textContent = `[final] ${ev.text}`; log("user", `你: ${ev.text}`); }
        else if (t === "response.text") rx.textContent += ev.text;
        else if (t === "response.done") { log("agent", `agent: ${(ev.text || "").slice(0, 80)}`); rx.textContent = `[done] ${ev.text || ""}`; }
        else if (t === "error") log("err", `error: ${ev.code} - ${ev.message}`);
        else log("evt", `evt: ${JSON.stringify(ev).slice(0, 100)}`);
      } else {
        player.enqueue(e.data);
      }
    };
    ws.onclose = (e) => {
      log("evt", `ws close ${e.code} ${e.reason || ""}`);
      ["rt-record", "rt-eos", "rt-disconnect", "rt-update-prompt", "rt-update-voice", "rt-clear-memory"].forEach(
        (id) => ($(id).disabled = true)
      );
    };
    ws.onerror = () => log("err", "ws err");
  });

  $("rt-record").addEventListener("click", async () => {
    try {
      stopRec = await recordMic16kPCM((buf) => {
        if (ws && ws.readyState === 1) ws.send(buf);
      });
      $("rt-record").disabled = true;
      log("evt", "录音中…");
    } catch (e) {
      log("err", `mic: ${e.message}`);
    }
  });

  $("rt-eos").addEventListener("click", async () => {
    if (stopRec) { await stopRec(); stopRec = null; }
    if (ws && ws.readyState === 1) {
      ws.send("audio.eos");
      log("evt", "EOS sent");
    }
    $("rt-record").disabled = false;
  });

  $("rt-disconnect").addEventListener("click", async () => {
    if (stopRec) { await stopRec(); stopRec = null; }
    if (ws) ws.close(1000);
    if (player) await player.close();
  });

  $("rt-update-prompt").addEventListener("click", () => {
    const p = window.prompt("新 prompt:", session?.prompt || "");
    if (!p || !ws) return;
    ws.send(JSON.stringify({ type: "session.update", prompt: p }));
    log("evt", `session.update prompt → "${p.slice(0, 30)}..."`);
  });

  $("rt-update-voice").addEventListener("click", () => {
    const v = window.prompt("新 voice:", session?.voice || "default_zh_female");
    if (!v || !ws) return;
    ws.send(JSON.stringify({ type: "session.update", voice: v }));
    log("evt", `session.update voice → ${v}`);
  });

  $("rt-clear-memory").addEventListener("click", () => {
    if (!ws) return;
    ws.send(JSON.stringify({ type: "memory.clear" }));
    log("evt", "memory.clear sent");
  });
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/js/realtime.js
git commit -m "feat(web): Realtime tab 完整对话流 (T10)

- POST /v1/sessions（prompt + voice/speed/audit_persist 表单）
- WS /v1/realtime/{id} 连（subprotocol bearer.{api_key}）
- mic 录音 → 16k int16 PCM 流式 send；EOS 触 turn
- 收事件分流：transcript.partial/final / response.text / response.done / error
- binary PCM frame → PCMPlayer 边收边播
- 中途控制：session.update prompt/voice + memory.clear
- ws_url hostname 用 cfg.base 重写（解决 docker hostname 浏览器不可达）

per spec §4.1（最复杂的 tab）"
```

---

## Task 11: js/tokens.js + Tokens tab 事件 wiring

**Files:**
- Modify: `clients/web/js/tokens.js`

- [ ] **Step 1: 重写 `clients/web/js/tokens.js`**

```javascript
import cfg from "./config.js";
import { log } from "./app.js";

export function setupTokens() {
  document.getElementById("tk-go").addEventListener("click", async () => {
    const identity = document.getElementById("tk-identity").value.trim();
    const room = document.getElementById("tk-room").value.trim();
    const ttl = parseInt(document.getElementById("tk-ttl").value);
    const status = document.getElementById("tk-status");

    if (!identity || !room) {
      log("err", "tokens: identity/room required");
      return;
    }
    status.textContent = "请求中…";
    try {
      const u = new URL(cfg.base);
      u.port = "8000";
      const r = await fetch(`${u.toString().replace(/\/$/, "")}/v1/tokens`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...cfg.authHeaders() },
        body: JSON.stringify({ identity, room, ttl_minutes: ttl }),
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `tokens: HTTP ${r.status} ${body.slice(0, 100)}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      const j = await r.json();
      document.getElementById("tk-token").value = j.token;
      document.getElementById("tk-url").value = j.url;
      log("evt", `token issued: identity=${j.identity} room=${j.room}`);
      status.textContent = "完成";
    } catch (e) {
      log("err", `tokens: ${e.message}`);
      status.textContent = "失败";
    }
  });
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/js/tokens.js
git commit -m "feat(web): Tokens tab event wiring (T11)

- identity/room/ttl 表单 → POST /v1/tokens (token-server :8000)
- 显示 JWT + LiveKit URL
- 注：token-server 默认无 CORS（per spec C.3，避免 LiveKit secret 暴露）；
  浏览器直接调可能被拦——README 里有提

per spec §4.1"
```

---

## Task 12: clients/web/README.md 完善 + clients/python/README.md 容器 exec 段

**Files:**
- Modify: `clients/web/README.md`
- Modify: `clients/python/README.md`

- [ ] **Step 1: clients/web/README.md 加「故障排查」段**

读 `clients/web/README.md`，在末尾追加：

````markdown

## 故障排查

### 浏览器 console 报 CORS 错

- 检查后端服务是否启 CORS：`curl -i -X OPTIONS http://your-host:9000/v1/sessions -H "Origin: http://localhost:8080"` 返 `access-control-allow-origin` 头即 ok
- 后端默认 `RTVOICE_CORS_ORIGINS=*`；prod 收紧后浏览器 origin 必须在列表内

### Tokens tab 调用 token-server 被 CORS 拦

token-server 默认**不**加 CORS（避免 LiveKit secret 暴露面）。
浏览器测试 token API 时，临时给 token-server 加 CORS（仅 dev）：

修改 `services/token-server/app/main.py` 同款加 `CORSMiddleware`；或走前端代理。

### Mic 权限拒绝

- 必须 `localhost`（http 也行）或 HTTPS 域名才允许 mic
- 浏览器地址栏点小锁 → 「网站设置」→ 麦克风「允许」

### 远程访问 RTVoice prod

- API base 填公网/内网地址：`http://192.168.66.163:9000`
- WS URL 自动用 cfg.base 的 hostname 重写
````

- [ ] **Step 2: clients/python/README.md 加「容器内试用」段**

读 `clients/python/README.md`，在 `## Status` 段之前插入：

````markdown
## Try inside RTVoice container（host 没 pip 时）

如果你的 host 没装 pip 或 python 环境受限（SP4 prod 实测），最快的体验路径是 **在 `rtvoice-realtime` 容器内跑 SDK**（容器自带 pip + Python 3.11 + httpx + websockets + pydantic）：

```bash
# 1. 把 SDK 源码拷进容器
docker cp clients/python rtvoice-realtime:/tmp/sdk

# 2. 容器内 install
docker exec rtvoice-realtime pip install -e /tmp/sdk --force-reinstall

# 3. 试用
docker exec rtvoice-realtime python3 -c "
from rtvoice_client import Client
c = Client(base_url='http://realtime-server:9000')
sess = c.realtime.create_session(prompt='hi')
print('session:', sess.session_id)
c.close()
"
```

适用场景：prod 端验证 SDK，不愿污染 host Python 环境。

````

- [ ] **Step 3: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/web/README.md clients/python/README.md
git commit -m "docs(clients): web README CORS 排障 + python README 容器 exec 段 (T12)

- web/README.md: '故障排查'（CORS / token-server / mic / 远程）
- python/README.md: 'Try inside container'（SP4 prod 实测痛点）"
```

---

## Task 13: OPERATIONS.md §6 docker mirror + Grafana 排障

**Files:**
- Modify: `OPERATIONS.md`

- [ ] **Step 1: 在 OPERATIONS.md §5 monitoring 段尾部之后插入 §6**

注：SP4 T17 已加 §5 monitoring；本步加 §6。读 `OPERATIONS.md` 找到 §5 末尾后插入：

````markdown

## §6 国内部署：docker registry mirror

国内服务器拉 `docker.io/*` 镜像（如 `prom/prometheus`、`grafana/grafana`）容易卡死。
推荐配 registry-mirrors 解决（SP4 prod 验收实测）。

### 6.1 全局配（影响所有 docker pull）

```bash
sudo tee /etc/docker/daemon.json << 'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io", "https://hub.daocloud.io"]
}
EOF
sudo systemctl restart docker  # ⚠️ 重启所有容器（生产慎用）
```

### 6.2 单镜像手动拉（不重启 docker，推荐）

```bash
docker pull docker.m.daocloud.io/prom/prometheus:v3.0.0
docker tag docker.m.daocloud.io/prom/prometheus:v3.0.0 prom/prometheus:v3.0.0

docker pull docker.m.daocloud.io/grafana/grafana:11.4.0
docker tag docker.m.daocloud.io/grafana/grafana:11.4.0 grafana/grafana:11.4.0

# 之后 docker compose 用本地 tag 不再去 docker.io 拉
docker compose --profile monitoring up -d
```

### 6.3 验证镜像源生效

```bash
docker info | grep -A 2 "Registry Mirrors"
```

预期：
```
 Registry Mirrors:
  https://docker.m.daocloud.io/
  https://hub.daocloud.io/
```

### 6.4 Grafana / Prometheus 启动失败排障

#### 现象：`docker compose --profile monitoring up -d` 卡 image pull
按 §6.1 / §6.2 配镜像源后重试。

#### 现象：Grafana 启动但 dashboard "No Data"

```bash
# 1. 看 prometheus targets 健康
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(t['labels']['job'], t['health'], t.get('lastError', ''))
"

# 2. 服务侧 metrics 验证暴露
docker exec rtvoice-realtime curl -s http://127.0.0.1:9000/metrics | head -20

# 3. 触发流量让 metric 有值
docker exec rtvoice-realtime python3 -c "
from rtvoice_client import Client
c = Client(base_url='http://realtime-server:9000')
for _ in range(3): c.realtime.create_session()
c.close()
"

# 4. Grafana 重 reload provisioning
docker exec rtvoice-grafana kill -HUP 1
```

#### 现象：dashboard JSON 改了但 Grafana 不刷新
`monitoring/grafana/dashboards/dashboards.yml` 内 `updateIntervalSeconds: 10` 已开 hot reload。
若仍不刷新：`docker compose restart grafana`。
````

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add OPERATIONS.md
git commit -m "docs(operations): §6 国内部署 docker mirror + Grafana 排障 (T13)

- §6.1 daemon.json 全局配
- §6.2 单镜像 manual pull（推荐，不重启 docker）
- §6.3 docker info 验证
- §6.4 Grafana 'No Data' 排障 4 步法

per spec §4.4 + SP4 prod 验收 A6 实测"
```

---

## Task 14: CHANGELOG v0.12.0 + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 插入 v0.12.0 entry**

定位 `## [Unreleased]` ... `## [0.11.0]`，中间插入：

```markdown
## [0.12.0] — 2026-05-09 — SP5 Adoption Bridge

平台化重构第六阶段：SP1-SP4 全 platform-side；SP5 是把 platform 推到"真用户能上手"的桥梁。

### Added

- **`clients/web/`** — 4-tab 纯 HTML/JS demo（STT / TTS / Realtime / Tokens）
  - 零 build 链；4 tab HTML 完全静态写在 `index.html`（无 JS innerHTML）；JS 仅 wire 事件
  - Web Audio API 流式 PCM 播放（24k mono int16）
  - getUserMedia + ScriptProcessor 录 16k mono int16 PCM
  - Realtime tab 完整对话流（transcript.partial / response.text / session.update / memory.clear）
  - 配置 localStorage 持久化（API base + Bearer）
- **CORS Middleware** — realtime/stt/tts 三服务加 FastAPI CORSMiddleware
  - env `RTVOICE_CORS_ORIGINS` 默认 `*`，prod 收紧示例：`https://app.com,https://demo.com`
  - allow_credentials=False
  - token-server **不**加（减少 LiveKit secret 暴露面）

### Changed

- `realtime-server` image tag `v0.9.0` → `v0.12.0`（SP2 起一直没改导致 metrics/info 误导）
- `realtime-server` FastAPI app version + `/info.version` 同步 0.12.0
- 3 服务 docker-compose environment 加 `RTVOICE_CORS_ORIGINS`
- `.env.example` 顶部加国内部署提醒；CORS 段
- `OPERATIONS.md` §6 加国内 docker mirror cookbook + Grafana 排障 4 步法
- `clients/python/README.md` 加 "Try inside container" 段（host 无 pip 时）

### 验证（autonomous）

- ✅ realtime-server CORS 2 单元测试 + version 1 测试
- ✅ stt/tts CORS middleware 加载（沙盒无 tests dir，prod E2E 验证）
- ✅ docker-compose validate / YAML lint
- ✅ web demo 文件结构齐 + Python http server serve
- ⏳ prod 集成：Grafana A6 unblock（daocloud mirror）+ 4 tabs 浏览器验收

### 设计决策

- clients/web/ 用纯 HTML/CSS/ES modules：与 SP3 静态测试页同款理念，零依赖
- 4 tab HTML 全静态写在 index.html：避免 JS innerHTML XSS 风险，结构更清晰
- token-server 不加 CORS：浏览器一般不直连 LiveKit token；server-to-server 不受 CORS 影响
- daocloud mirror 单镜像 pull + tag（不改 daemon.json）：可逆，不影响其他容器
- Web Audio ScriptProcessorNode 仍用（deprecated 但 ubiquitous）；AudioWorklet 等 SP6+

详见 [SP5 设计](./docs/superpowers/specs/2026-05-09-sp5-adoption-bridge-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-09-sp5-adoption-bridge.md)。

---
```

- [ ] **Step 2: 文档链接 lint**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in README.md ARCHITECTURE.md DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/CONVENTIONS.md docs/api/stt.md docs/api/tts.md docs/api/sessions.md clients/python/README.md clients/web/README.md; do
    [ -e "$f" ] || continue
    echo "--- $f ---"
    grep -oE '\]\(\./[^)#]+' "$f" | sed 's/](\.\///' | sort -u | while read p; do
        [ -e "$p" ] && echo "  [ok] $p" || echo "  [FAIL] $p"
    done
done
```

Expected: 全 [ok]。

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): v0.12.0 — SP5 Adoption Bridge (T14)

- Added: clients/web/ 4-tab HTML demo + CORS middleware (3 svc)
- Changed: realtime-server image tag/version 0.12.0；deployment 提醒；OPERATIONS §6
- 3 新单元测试；prod 验收 + Grafana A6 unblock 待 T15"

git push origin main 2>&1 | tail -5
```

---

## Task 15: prod 部署 + autonomous 验收 + Grafana A6 unblock + user 通知

**Files:** 无（read-only verification + remote ops）

- [ ] **Step 1: prod 端 git pull + build + force recreate 3 服务**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -5
  echo
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server stt-server tts-server 2>&1 | tail -10
  echo
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d --force-recreate realtime-server stt-server tts-server 2>&1 | tail -10
  echo
  for i in $(seq 1 15); do
    s1=$(docker inspect rtvoice-realtime --format "{{.State.Health.Status}}" 2>/dev/null)
    s2=$(docker inspect rtvoice-stt --format "{{.State.Health.Status}}" 2>/dev/null)
    s3=$(docker inspect rtvoice-tts --format "{{.State.Health.Status}}" 2>/dev/null)
    echo "[$i] realtime=$s1 stt=$s2 tts=$s3"
    [ "$s1" = "healthy" ] && [ "$s2" = "healthy" ] && [ "$s3" = "healthy" ] && break
    sleep 5
  done
}'
```

Expected: 3 服务 healthy。

- [ ] **Step 2: prod autonomous A1-A5（CORS + version）**

```bash
ssh root@192.168.66.163 '
echo "=== A1: realtime CORS preflight ==="
curl -i -s -X OPTIONS http://192.168.66.163:9000/v1/sessions \
  -H "Origin: http://example.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" 2>&1 | grep -iE "HTTP|access-control" | head -5

echo
echo "=== A2: stt CORS preflight ==="
docker exec rtvoice-agent curl -i -s -X OPTIONS http://stt-server:9090/v1/asr \
  -H "Origin: http://example.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" 2>&1 | grep -iE "HTTP|access-control" | head -5

echo
echo "=== A3: tts CORS preflight ==="
docker exec rtvoice-agent curl -i -s -X OPTIONS http://tts-server:9880/v1/tts/stream \
  -H "Origin: http://example.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" 2>&1 | grep -iE "HTTP|access-control" | head -5

echo
echo "=== A4: /info version 0.12.0 ==="
docker exec rtvoice-agent curl -s http://realtime-server:9000/info | python3 -c "
import sys, json
d = json.load(sys.stdin)
v = d.get(\"version\", \"?\")
print(\"version:\", v)
assert v == \"0.12.0\"
print(\"\u2713 A4\")
"

echo
echo "=== A5: docker compose realtime-server image tag ==="
cd /data/RTVoice && docker compose -f docker-compose.yml config 2>/dev/null | grep -A 1 "realtime-server:" | grep image
'
```

Expected: A1-A3 各返 HTTP/1.1 200 + access-control-allow-origin；A4 v0.12.0；A5 image v0.12.0。

- [ ] **Step 3: Grafana A6 unblock — daocloud mirror pull + tag + monitoring up**

```bash
ssh root@192.168.66.163 '
echo "=== mirror pull prometheus + grafana ==="
docker pull docker.m.daocloud.io/prom/prometheus:v3.0.0 2>&1 | tail -3
docker tag docker.m.daocloud.io/prom/prometheus:v3.0.0 prom/prometheus:v3.0.0
docker pull docker.m.daocloud.io/grafana/grafana:11.4.0 2>&1 | tail -3
docker tag docker.m.daocloud.io/grafana/grafana:11.4.0 grafana/grafana:11.4.0

echo
echo "=== docker images ==="
docker images | grep -E "prom/prometheus|grafana/grafana"

echo
echo "=== up monitoring profile ==="
cd /data/RTVoice && docker compose --profile monitoring up -d 2>&1 | tail -8

echo
for i in $(seq 1 10); do
  p=$(docker inspect rtvoice-prometheus --format "{{.State.Status}}" 2>/dev/null)
  g=$(docker inspect rtvoice-grafana --format "{{.State.Status}}" 2>/dev/null)
  echo "[$i] prom=$p grafana=$g"
  [ "$p" = "running" ] && [ "$g" = "running" ] && break
  sleep 3
done
'
```

Expected: 两镜像 pulled + tagged；prometheus + grafana running。

- [ ] **Step 4: A8-A9 prometheus targets + 跑 metric 流量**

```bash
ssh root@192.168.66.163 '
echo "=== A8: prometheus targets 健康 ==="
sleep 5
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d[\"data\"][\"activeTargets\"]:
    print(t[\"labels\"][\"job\"], t[\"health\"])
"

echo
echo "=== A9: 跑流量让 metric 有值 ==="
docker cp /data/RTVoice/clients/python rtvoice-realtime:/tmp/sdk2 2>&1 | tail -2
docker exec rtvoice-realtime pip install -e /tmp/sdk2 --force-reinstall 2>&1 | tail -2
docker exec rtvoice-realtime python3 -c "
from rtvoice_client import Client
c = Client(base_url=\"http://realtime-server:9000\")
for i in range(3):
    sess = c.realtime.create_session()
    print(\"sess\", i, sess.session_id)
c.close()
"

echo
echo "=== 验 metrics 已上 prometheus ==="
sleep 20
curl -s "http://127.0.0.1:9090/api/v1/query?query=rtvoice_realtime_sessions_active" | python3 -c "
import sys, json
d = json.load(sys.stdin)
result = d[\"data\"][\"result\"]
if result:
    print(\"\u2713 sessions_active =\", result[0][\"value\"][1])
else:
    print(\"FAIL: no metric data yet\")
"
'
```

Expected: 4 services up；3 sessions 创建；prometheus 抓到 sessions_active。

- [ ] **Step 5: 通知 user user-participation 验收**

```
SP5 沙盒 + autonomous + Grafana A6 unblock 完成。请你做：

1. **Grafana 浏览器**：http://192.168.66.163:3000  
   - RTVoice Overview dashboard（anonymous viewer 默认开）
   - 8 面板都有数据：Service Health 全绿 / Active Sessions / Turns/min 等
   - 跑几个 turn 看 Turns/min 数值（B5）

2. **Web demo 浏览器**：本地启 http server：
   ```bash
   cd RTVoice/clients/web && python3 -m http.server 8080
   ```
   浏览器开 http://localhost:8080/，4 tabs 验：
   - **STT**：录音→识别 text
   - **TTS**：填文字→听合成音
   - **Realtime**：完整对话（最大）
   - **Tokens**：申请 LiveKit token（可能需临时给 token-server 加 CORS）
   
   配置：Top bar API base 填 `http://192.168.66.163:9000`；Bearer 留空（dev mode）

3. **CozyVoice 切 SDK**（你这边的项目）：
   ```bash
   pip install -e /path/to/RTVoice/clients/python/
   ```
   把 hand-write httpx 换成 `Client(base_url="http://192.168.66.163:9000")` + namespace 方法
```

- [ ] **Step 6: User 反馈后标 SP5 完工**

OK → SP5 done。
有问题 → SP5-fix-N。

---

## Self-Review

### 1. Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §3 file layout | T1-T5 services / T6-T11 web/ / T13 OPERATIONS |
| §4.1 W clients/web/ | T6 骨架 + index.html 全静态 / T7 audio.js / T8-T11 4 tabs |
| §4.2 C CORS | T1-T3 + T5 env |
| §4.3 D Deploy friendly | T4 image tag/version + T5 .env + T12 README + T13 OPERATIONS §6 |
| §4.4 G Grafana unblock | T13 OPERATIONS §6.4 + T15 prod 实操 |
| §5 测试矩阵 | T1 +2 / T4 +1 = 3（spec 估 4；stt/tts CORS 沙盒无 tests dir 挪 prod E2E） |
| §6 验收 A1-A10 + B1-B6 | T15 |
| §8 范围外 | 未实施任何 ✓ |

### 2. Placeholder scan

- 每 step 含完整代码或命令
- 无 TBD/TODO
- T6 Step 7 占位 4 个 tab JS（明确指向 T8-T11）
- T1-T3 共享同款 CORS middleware 代码（DRY 取舍：plan 重复 OK 因每 task 独立读）

### 3. Type consistency

- `RTVOICE_CORS_ORIGINS` 在 T1-T3 server / T5 compose / T5 .env 一致
- `cfg.base / cfg.bearer / cfg.authHeaders()` 在 T6 config.js 定义；T8-T11 各 tab 引用一致
- `PCMPlayer / recordMic16kPCM` 在 T7 audio.js 定义；T8-T10 引用一致
- `setupSTT / setupTTS / setupRealtime / setupTokens` 在 T6 占位 + T8-T11 实现，签名一致
- realtime-server image tag `rtvoice/realtime-server:v0.12.0` 在 T4 + T15 一致
- HTML 元素 ID（如 `stt-record`、`rt-create`、`tk-go`）在 T6 index.html 定义；T8-T11 JS 用 `getElementById` 引用一致

无类型/签名漂移。

### 4. innerHTML 风险消除

T6 把所有 4 tab HTML 静态写在 index.html，T8-T11 JS 仅 `getElementById` + `addEventListener`，**无任何 .innerHTML 写入**。比初稿更清晰、避免 XSS 安全告警。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-sp5-adoption-bridge.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + spec/quality 双审；与 SP1-SP4 同流程
2. **Inline Execution** — 本 session 批量执行 + checkpoints

Which approach?
