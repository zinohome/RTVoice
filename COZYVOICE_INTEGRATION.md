# RTVoice → CozyVoice 集成指南

本文档示范如何把 RTVoice 集成到任意客户端项目作为本地后端。CozyVoice 是其一示例；其他场景（Discord bot / 客服系统 / 自动化 / 移动 app）参照同样模式。

**对象读者**：CozyVoice / 任意第三方客户端项目的开发者。前提：RTVoice prod 已部署（按 [DEPLOY.md](./DEPLOY.md)），跑在 **v0.15.0+**（v0.14 加 multi-tenant auth + hot-reload；v0.15 加 prod 端口对外 + WS subprotocol echo 协议层修复）。

---

## 0. 接入拓扑速览

**先读这节再继续**——决定哪些 URL 你能从浏览器 / 三方进程访问。

### 0.1 生产部署接入层（推荐）

生产环境所有服务统一通过 **Caddy 反向代理**（HTTPS/WSS 443 端口）对外暴露，各容器内部端口**不直接对外**。这是外部应用集成的标准路径：

| 用途 | 协议 | Caddy 路径 | 示例 URL（`SERVER_IP=192.168.66.163`） |
|---|---|---|---|
| Admin 管理后台 | HTTPS | `/admin/` | `https://192.168.66.163/admin/` |
| 创建 Realtime 会话 | HTTPS | `POST /v1/sessions` | `https://192.168.66.163/v1/sessions` |
| Realtime 语音 WebSocket | WSS | `/v1/realtime/{session_id}` | `wss://192.168.66.163/v1/realtime/{id}` |
| STT 流式识别 | WSS | `/v1/asr` | `wss://192.168.66.163/v1/asr` |
| TTS 单次合成 | HTTPS | `POST /v1/tts/stream` | `https://192.168.66.163/v1/tts/stream` |
| TTS 双向流式 | WSS | `/v1/tts/stream_ws` | `wss://192.168.66.163/v1/tts/stream_ws` |
| 音色管理 | HTTPS | `/v1/voices` | `https://192.168.66.163/v1/voices` |
| LiveKit JWT | HTTPS | `POST /v1/tokens` | `https://192.168.66.163/v1/tokens` |
| LiveKit SFU（直连）| WS | `:7880` 直连 | `ws://192.168.66.163:7880`（不经 Caddy） |

> **TLS**：Caddy 使用 `tls internal` 自签 CA。客户端需信任该 CA，详见 [QUICKSTART.md §1](./QUICKSTART.md)。

### 0.2 同机 docker 内部直连（可选）

若你的应用与 RTVoice **部署在同一台机器同一 docker 网络**（`1panel-network`），可直接用容器名访问，无需 TLS：

| Service | 容器名 | 内部地址 | 用途 |
|---|---|---|---|
| realtime-server | `rtvoice-realtime` | `http://realtime-server:9000` | sessions / realtime WS |
| stt-server | `rtvoice-stt` | `ws://stt-server:9090/v1/asr` | STT |
| tts-server | `rtvoice-tts` | `http://tts-server:9880` | TTS |
| token-server | `rtvoice-token` | `http://token-server:8000` | tokens |
| livekit-server | `rtvoice-livekit` | `ws://livekit-server:7880` | LiveKit SFU |

这是拓扑 A（§1 中的最高推荐拓扑）的使用方式。docker 外部进程不能使用此方式。

---

## 1. 部署拓扑选择

按 CozyVoice 与 RTVoice 的部署位置选一种：

| 拓扑 | 何时用 | 推荐度 | 网络 + 安全 |
|---|---|---|---|
| **A. 同机 docker network 直连** | 同一台 GPU 机跑两套 docker compose | ⭐⭐⭐⭐⭐ | 不暴露端口；外人完全够不到；零 TLS |
| B. 同机宿主→127.0.0.1 | CozyVoice 在宿主跑（非 docker） | ⭐⭐⭐⭐ | 暴露 127.0.0.1 端口，本机进程可达 |
| C. 跨机内网 | CozyVoice 在另一台机器（同 LAN） | ⭐⭐⭐ | Bearer + Caddy `tls internal` 自签 |
| D. 跨机公网 | 多区域 / 需公网域名 | ⭐⭐ | Bearer + Caddy LE 公网 cert |

下文重点是 **A 同机 docker 直连**。其他 3 种切换到 [§7 其他拓扑](#7-其他部署拓扑)。

---

## 2. 准备：RTVoice 端配置

### 2.1 给你的应用颁一把 API key（v0.14 新做法）

**v0.14 之前**（已弃用）：单 `RTVOICE_API_KEY` env + rebuild 三个服务。
**v0.14+ 现做法**：multi-tenant admin CLI 颁 key，**热加载**，不重启服务。

```bash
ssh root@192.168.66.163
cd /data/RTVoice

# 在 realtime-server 容器里跑 admin CLI 创建一把 key
docker exec rtvoice-realtime rtvoice-admin create \
  --name cozyvoice-app \
  --scopes stt,tts,tokens,realtime \
  --sessions-concurrent 10 \
  --sessions-per-hour 1000 \
  --notes "CozyVoice 应用集成"

# 输出形如：
# {"id": "key_xxxxx", "secret": "BASE64-RANDOM-43-CHARS", ...}
# ⚠️ secret 仅显示这一次，立刻保存到 CozyVoice 的 .env 作 RTVOICE_API_KEY
```

**热加载验证**：写完后**约 100ms** 4 个 service 的 watcher 自动 reload keys.yaml；立刻 curl 就能用，**不需 rebuild / restart**。

**Scopes 含义**（v0.14 新增——D1 finding F3）：
| scope | 允许调用 |
|---|---|
| `stt` | stt-server `/v1/asr` |
| `tts` | tts-server `/v1/voices` (GET) / `/v1/tts/stream` / `/v1/tts/stream_ws` |
| `tokens` | token-server `/v1/tokens` |
| `realtime` | realtime-server `/v1/sessions` + WS |

key 创建时必须含所有要用的 scope；少一个就 403 `auth.scope_denied`。

**验证**：`curl -i http://192.168.66.163:9090/v1/asr`（无 token）→ 401 / unauthorized。
带 token：`curl -H "Authorization: Bearer $RTVOICE_API_KEY" http://192.168.66.163:9090/info` → 200。

### 2.1b 三种凭据分层（D1 finding F2 新增）

RTVoice 全栈实际有**三种**凭据，别混淆：

| 凭据 | 颁发方 | 作用 | 用在哪 |
|---|---|---|---|
| **API key (Bearer)** | admin CLI 颁 (本节) | 长期有效的应用身份 | 所有 RTVoice service 调用 `Authorization: Bearer <secret>` |
| **LiveKit JWT** | token-server `/v1/tokens` 现场颁 | 短期（≤24h）的房间访问凭证 | 客户端连 LiveKit room 用 |
| **session_id** | realtime-server `/v1/sessions` 现场颁 | 单次 Realtime Voice 对话 token | WS `/v1/realtime/{session_id}` URL 拼进 |

**链路**：你拿 API key → 调 `/v1/tokens` 拿 LiveKit JWT 给浏览器 → 浏览器连 LiveKit room。
或：你拿 API key → 调 `/v1/sessions` 拿 session_id → WS 连 realtime → server 用 session_id 路由对话。

### 2.2 留一个 docker network 名（CozyVoice 要 join）

RTVoice 启动后会创建 docker network `rtvoice_rtvoice_net`（命名规则 `<project>_<service-network>`）。
CozyVoice 的 compose 通过 `external: true` 引用此网络即可。

```bash
docker network ls | grep rtvoice
# rtvoice_rtvoice_net   bridge   local
```

---

## 3. CozyVoice 端：docker network 直连

### 3.1 CozyVoice 的 docker-compose.yml 关键片段

```yaml
networks:
  rtvoice_net:
    external: true
    name: rtvoice_rtvoice_net   # RTVoice 创建的实际网络名

services:
  cozyvoice-app:
    image: <你的 cozyvoice 镜像>
    networks:
      - default
      - rtvoice_net      # 加入 RTVoice network
    environment:
      RTVOICE_STT_URL: ws://stt-server:9090/v1/asr
      RTVOICE_TTS_URL: http://tts-server:9880
      RTVOICE_API_KEY: ${RTVOICE_API_KEY}
      # 默认音色（可改）
      RTVOICE_TTS_VOICE: default_zh_female
      RTVOICE_TTS_LANG: cmn
```

### 3.2 CozyVoice 的 .env

```bash
# 与 RTVoice 的 .env 里的 RTVOICE_API_KEY 完全相同
RTVOICE_API_KEY=<上一步生成的 32 字符 key>
```

### 3.3 启动验证

```bash
docker compose up -d cozyvoice-app
docker exec <cozyvoice 容器> python3 -c "
import urllib.request, os
# urllib 不支持 urlopen(headers=...)，要用 Request 对象（D1-F8 修正）
req = urllib.request.Request(
    'http://tts-server:9880/info',
    headers={'Authorization': f'Bearer {os.environ[\"RTVOICE_API_KEY\"]}'},
)
print(urllib.request.urlopen(req, timeout=5).read().decode())
"
# 期望：{"backend":"cosyvoice3","model":"Fun-CosyVoice3-0.5B-2512", ...}
```

---

## 4. Endpoints 参考

所有路径均通过 Caddy 对外（`https://<SERVER_IP>/...` 或 `wss://<SERVER_IP>/...`）。

### Realtime Voice

| 用途 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| 创建会话 | POST | `/v1/sessions` | Bearer |
| 查询会话 | GET | `/v1/sessions/{id}` | Bearer |
| 关闭会话 | DELETE | `/v1/sessions/{id}` | Bearer |
| 实时语音 WebSocket | WS | `/v1/realtime/{session_id}` | Bearer |
| 更新会话配置（WS 内） | — | `{"type":"session.update","prompt":"..."}` | — |
| 打断当前回复（WS 内） | — | `{"type":"interrupt"}` | — |

### STT

| 用途 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| 流式语音识别 | WS | `/v1/asr` | Bearer |

### TTS

| 用途 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| TTS 单次合成 | POST | `/v1/tts/stream` | Bearer |
| **TTS 双向流式** | **WS** | **`/v1/tts/stream_ws`** | **Bearer** |
| 列出音色 | GET | `/v1/voices` | Bearer |
| 注册音色（admin） | POST | `/v1/voices` | TTS_ADMIN_API_KEY |
| 删除音色（admin） | DELETE | `/v1/voices/{id}` | TTS_ADMIN_API_KEY |

### Token Server

| 用途 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| 签发 LiveKit JWT | POST | `/v1/tokens` | Bearer |

### 鉴权方式

**HTTP**：`Authorization: Bearer <RTVOICE_API_KEY>`

**WebSocket**（任选一种）:
1. `Authorization: Bearer <KEY>` header（推荐 server-to-server）
2. `Sec-WebSocket-Protocol: bearer.<KEY>`（浏览器场景，标准 subprotocol）
3. `?token=<KEY>` query param（兜底，会进 access log）

### WS 事件类型（Realtime Voice）

| 方向 | 类型 | 含义 |
|---|---|---|
| 客户端 → 服务端 | `bytes` | PCM int16 LE 16kHz mono 音频帧 |
| 客户端 → 服务端 | `"audio.eos"` | 音频结束（可选） |
| 客户端 → 服务端 | `{"type":"interrupt"}` | 打断当前 AI 回复 |
| 客户端 → 服务端 | `{"type":"session.update","prompt":"..."}` | 中途更换 system prompt |
| 服务端 → 客户端 | `bytes` | TTS 音频帧（PCM int16 LE 24kHz mono） |
| 服务端 → 客户端 | `{"type":"transcript.partial","text":"..."}` | 实时识别中间结果 |
| 服务端 → 客户端 | `{"type":"transcript.final","text":"..."}` | 识别最终结果 |
| 服务端 → 客户端 | `{"type":"response.text","delta":"..."}` | LLM 回复文本 delta |
| 服务端 → 客户端 | `{"type":"response.done"}` | 本轮回复完成 |
| 服务端 → 客户端 | `{"type":"interrupted","cancelled":true}` | 打断确认 |
| 服务端 → 客户端 | `{"type":"error","message":"..."}` | 错误 |

---

## §5.0 Recommended: 用 rtvoice-client SDK

**当前状态 (v0.15)**：SDK 源码在仓库 `clients/python/`，**未发布到 PyPI**。

安装方式（任选其一）：

```bash
# 方式 1：从 git URL pip install（推荐）
pip install "git+https://github.com/zinohome/RTVoice.git#subdirectory=clients/python"

# 方式 2：本地 clone 后 editable install
git clone https://github.com/zinohome/RTVoice
pip install -e ./RTVoice/clients/python
```

PyPI 发布在后续 release 计划中——届时这一段会改回 `pip install rtvoice-client`。

```python
from rtvoice_client import Client

c = Client(api_key=os.environ["RTVOICE_API_KEY"],
           base_url=os.environ["RTVOICE_BASE_URL"])

# STT
text = c.stt.transcribe(pcm_int16le_16k_mono, sample_rate=16000)

# TTS
pcm = c.tts.synthesize("你好世界", voice="default_zh_female", speed=1.0)

# Realtime — 高层 helper
async def cozyvoice_chat(audio_iter):
    async for evt in c.realtime.conversation(audio_iter, prompt="..."):
        if evt.type == "response.text":
            yield evt.text  # text delta 给 UI
        elif evt.type == "response.pcm":
            yield evt.data   # PCM bytes 给 audio sink
```

错误处理用 typed exceptions：

```python
from rtvoice_client.errors import CapacityFull, PromptTooLong, RTVoiceError

try:
    sess = c.realtime.create_session(prompt="...")
except CapacityFull:
    show_user("服务繁忙，稍后重试")
except PromptTooLong:
    show_user("system prompt 太长（>2000 字符）")
except RTVoiceError as e:
    log.error("rtvoice error: %s", e)
```

---

## 5. Python SDK 示例

### 5.1 STT WS 客户端

```python
import asyncio, json, os
import websockets

class RTVoiceSTT:
    def __init__(self, url=None, api_key=None):
        self.url = url or os.environ['RTVOICE_STT_URL']
        self.api_key = api_key or os.environ.get('RTVOICE_API_KEY', '').strip()

    async def transcribe(self, pcm_int16le_16k_mono: bytes) -> str:
        """一次性识别（不流式）。pcm 是 16kHz mono int16 LE bytes。"""
        headers = {}
        subprotocols = None
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
            subprotocols = [f'bearer.{self.api_key}']

        async with websockets.connect(
            self.url, additional_headers=headers, subprotocols=subprotocols,
            max_size=None, ping_interval=20, ping_timeout=10,
        ) as ws:
            # 切成 ~100ms 帧避免单帧太大
            chunk_size = 16000 * 2 // 10
            for i in range(0, len(pcm_int16le_16k_mono), chunk_size):
                await ws.send(pcm_int16le_16k_mono[i:i+chunk_size])
            await ws.send('EOS')

            async for msg in ws:
                ev = json.loads(msg)
                if ev.get('type') == 'final':
                    return ev.get('text', '')
                if ev.get('type') == 'error':
                    raise RuntimeError(f"STT error: {ev.get('message')}")
            return ''

# 用法
async def example():
    stt = RTVoiceSTT()
    with open('utterance.pcm', 'rb') as f:
        text = await stt.transcribe(f.read())
    print(text)
```

### 5.2 TTS HTTP 单次合成（简单场景）

```python
import httpx, os

class RTVoiceTTS:
    def __init__(self, base_url=None, api_key=None, voice=None):
        self.base_url = (base_url or os.environ['RTVOICE_TTS_URL']).rstrip('/')
        self.api_key = api_key or os.environ.get('RTVOICE_API_KEY', '').strip()
        self.voice = voice or os.environ.get('RTVOICE_TTS_VOICE', 'default_zh_female')
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))

    async def synth(self, text: str, voice: str = None, speed: float = 1.0):
        """yield PCM int16 LE 24kHz mono bytes 块。"""
        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        payload = {
            'text': text,
            'voice': voice or self.voice,
            'lang': 'cmn',
            'speed': speed,
        }
        async with self._client.stream('POST', f'{self.base_url}/v1/tts/stream',
                                       json=payload, headers=headers) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    async def aclose(self):
        await self._client.aclose()
```

用法：
```python
tts = RTVoiceTTS()
with open('out.pcm', 'wb') as f:
    async for pcm in tts.synth("你好，今天天气不错"):
        f.write(pcm)
# out.pcm: 24000 samples/sec, int16 LE, mono → ffmpeg 转 wav:
#   ffmpeg -f s16le -ar 24000 -ac 1 -i out.pcm out.wav
```

### 5.3 TTS WebSocket 双向流式（v0.7+，**推荐**）

适用于 LLM token 流边产生边喂 TTS 的场景，端到端首字节延迟最低。

```python
import asyncio, json, os
import websockets

class RTVoiceTTSStream:
    def __init__(self, base_url=None, api_key=None):
        # 把 http(s) URL 转 ws(s)
        url = base_url or os.environ['RTVOICE_TTS_URL']
        self.ws_url = url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1) + '/v1/tts/stream_ws'
        self.api_key = api_key or os.environ.get('RTVOICE_API_KEY', '').strip()

    async def synth_streaming(self, text_iter, voice='default_zh_female', speed=1.0):
        """
        text_iter: async iterator of str chunks (e.g. LLM token stream).
        yield PCM 24kHz int16 mono bytes.
        """
        headers = {}
        subprotocols = None
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
            subprotocols = [f'bearer.{self.api_key}']

        async with websockets.connect(
            self.ws_url, additional_headers=headers, subprotocols=subprotocols,
            max_size=None, ping_interval=20, ping_timeout=10,
        ) as ws:
            # 1. 第一帧：JSON metadata
            await ws.send(json.dumps({'voice': voice, 'speed': speed}))

            # 2. 并行：feeder 推文本 + main loop 收 PCM
            async def feeder():
                try:
                    async for chunk in text_iter:
                        if chunk:
                            await ws.send(chunk)
                finally:
                    await ws.send('EOS')

            feed_task = asyncio.create_task(feeder())
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        yield msg
                    else:
                        ev = json.loads(msg)
                        if ev.get('type') == 'done':
                            return
                        if ev.get('type') == 'error':
                            raise RuntimeError(f"TTS error: {ev.get('message')}")
            finally:
                if not feed_task.done():
                    feed_task.cancel()
```

**最强用法**：把 LLM token stream 当 `text_iter` 喂给 TTS，端到端延迟可达 ~150ms：

```python
async def llm_to_tts():
    async def llm_tokens():
        # 你的 LLM 客户端（OpenAI 风格 stream）
        async for delta in llm_client.stream("你好"):
            yield delta

    tts = RTVoiceTTSStream()
    audio = bytearray()
    async for pcm in tts.synth_streaming(llm_tokens()):
        audio.extend(pcm)  # 或边收边推到音频输出设备
    return bytes(audio)
```

### 5.4 Realtime Voice 完整对话客户端

```python
import asyncio, json, os
import httpx, websockets


class RTVoiceRealtimeClient:
    def __init__(self, base_http=None, api_key=None):
        self.base_http = base_http or os.environ.get("RTVOICE_RT_HTTP", "http://realtime-server:9000")
        self.api_key = api_key or os.environ.get("RTVOICE_API_KEY", "").strip()

    async def create_session(self, voice="default_zh_female", speed=1.0):
        async with httpx.AsyncClient() as c:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            r = await c.post(
                f"{self.base_http}/v1/sessions",
                json={"voice": voice, "speed": speed},
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            return r.json()

    async def conversation(self, ws_url, audio_chunks_iter, on_transcript=None):
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            async def feed():
                async for pcm in audio_chunks_iter:
                    await ws.send(pcm)
                await ws.send("audio.eos")
            asyncio.create_task(feed())
            async for msg in ws:
                if isinstance(msg, bytes):
                    yield msg
                else:
                    ev = json.loads(msg)
                    if ev["type"] == "transcript.final":
                        if on_transcript:
                            on_transcript(ev["text"])
                    elif ev["type"] == "response.done":
                        return
                    elif ev["type"] == "error":
                        raise RuntimeError(f"realtime error: {ev}")


async def main():
    client = RTVoiceRealtimeClient()
    sess = await client.create_session()
    print(f"session: {sess['session_id']}")

    async def mic_chunks():
        with open("user_input.pcm", "rb") as f:
            while True:
                c = f.read(3200)
                if not c: break
                yield c

    pcm_out = bytearray()
    async for chunk in client.conversation(sess["ws_url"], mic_chunks(),
                                            on_transcript=lambda t: print(f"我说了: {t}")):
        pcm_out.extend(chunk)
    open("agent_reply.pcm", "wb").write(pcm_out)


asyncio.run(main())
```

**用法 — 自定义 prompt + audit**（v0.10+）：

```python
# 注：上面 §5.4 RTVoiceRealtimeClient.create_session 仅传 voice/speed；
# SP3 加字段直接扩展 POST body，或把 create_session 加 kwargs：
async def create_session_sp3(self, voice="default_zh_female", speed=1.0,
                             prompt=None, audit_persist=False):
    body = {"voice": voice, "speed": speed, "audit_persist": audit_persist}
    if prompt: body["prompt"] = prompt
    # ...同上 POST /v1/sessions

sess = await client.create_session_sp3(prompt="你是 IT 客服，用中文简短回答", audit_persist=True)
# 之后所有 turn agent 用此 prompt + 自动滚 6 轮历史
# audit JSONL 在 server 端 /data/transcripts/{date}/{session_id}.jsonl
```

**中途换 prompt**：

```python
async with websockets.connect(ws_url) as ws:
    await ws.send(json.dumps({"type": "session.update", "prompt": "改用英文"}))
    # 下一 turn 起 agent 用新 prompt
```

**Barge-in（打断）**：

用户在 AI 回复播放期间重新开口时，客户端应发送 `interrupt` 事件立即停止当前回复，避免 AI 音频继续播出并覆盖用户的话：

```python
async with websockets.connect(ws_url) as ws:
    # 检测到用户开始说话（VAD 触发）且 AI 正在回复时：
    await ws.send(json.dumps({"type": "interrupt"}))
    # 服务端收到后：取消当前 turn 任务、重置 STT、回复 interrupted 事件
    resp = json.loads(await ws.recv())
    # resp = {"type": "interrupted", "cancelled": True}
    # 之后可继续发送用户音频开始新一轮对话
```

> **客户端时序建议**：收到 `interrupted` 确认后再开始播放新的用户音频或 AI 回复，避免乱序。

---

## 6. 常见模式

### 6.1 完整对话循环（CozyVoice agent 内嵌 RTVoice）

```python
async def conversation_round(user_pcm_16k: bytes) -> bytes:
    """用户音频 → STT → LLM → TTS → 返回 agent 音频。"""
    user_text = await stt.transcribe(user_pcm_16k)
    
    async def llm_stream():
        async for delta in llm.stream(user_text):
            yield delta

    tts = RTVoiceTTSStream()
    audio = bytearray()
    async for pcm in tts.synth_streaming(llm_stream()):
        audio.extend(pcm)
    return bytes(audio)
```

### 6.2 使用自定义音色

```bash
# 准备 16kHz mono wav (3-30 秒)
ffmpeg -i alice.mp3 -ar 16000 -ac 1 -sample_fmt s16 alice_ref.wav

# 通过 admin API 注册
curl -X POST http://127.0.0.1:9880/v1/voices \
  -H "Authorization: Bearer $TTS_ADMIN_API_KEY" \
  -F spk_id=alice \
  -F prompt_text="参考音频对应的文字（≥3秒发音内容）" \
  -F file=@alice_ref.wav
```

之后 CozyVoice 端调用：
```python
async for pcm in tts.synth("你好", voice='alice'):
    ...
```

### 6.3 错误恢复

| 场景 | 现象 | RTVoice 行为 | CozyVoice 该做 |
|---|---|---|---|
| RTVoice tts-server 重启 | TTS 请求 502/connection refused | / | retry 1 次（间隔 2s）|
| Bearer 错 | 401 | / | 检查 RTVOICE_API_KEY 一致 |
| 音色不存在 | TTS 返 400 "未知音色" | / | fallback 到 `default_zh_female` |
| LLM 超时 | TTS 仍工作 | LLM 端 fallback "抱歉没听清"（v0.6.2 内置） | / |
| STT 抖动 | 单次 final 为空 | STT 自动重连（v0.6.2）| 当前 utterance 可让用户重说 |

---

## 7. 其他部署拓扑

### 7.1 同机宿主调用（CozyVoice 不在 docker）

启用 `docker-compose.api.yml` 把 RTVoice 端口暴露到宿主：
```bash
cd /data/RTVoice
echo "BIND_HOST=127.0.0.1" >> .env  # 仅本机；改 0.0.0.0 暴露 LAN
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               -f docker-compose.api.yml --profile prod up -d
```

CozyVoice 端 URL：
```
RTVOICE_STT_URL=ws://127.0.0.1:9090/v1/asr
RTVOICE_TTS_URL=http://127.0.0.1:9880
```

### 7.2 跨机内网（Caddy TLS，默认配置）

生产部署默认已通过 `deployment/docker-compose.yml` 启动 Caddy，`tls internal` 自签 CA，无需额外 overlay：

```bash
cd /data/RTVoice/deployment
docker compose -f docker-compose.yml --env-file .env up -d
```

CozyVoice 端配置（以 SERVER_IP=192.168.66.163 为例）：
```
RTVOICE_STT_URL=wss://192.168.66.163/v1/asr
RTVOICE_TTS_URL=https://192.168.66.163
RTVOICE_RT_HTTP=https://192.168.66.163
```

首次需在 CozyVoice 所在机器信任 RTVoice CA 证书（**一次性操作**）：
```bash
# 从 RTVoice 服务器导出 CA
ssh root@192.168.66.163 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > /tmp/rtvoice-ca.crt

# Linux（在 CozyVoice 机器上执行）
sudo cp /tmp/rtvoice-ca.crt /usr/local/share/ca-certificates/rtvoice-ca.crt
sudo update-ca-certificates

# 验证
curl --cacert /tmp/rtvoice-ca.crt https://192.168.66.163/health
```

Python SDK 指定 CA：`Client(..., verify="/tmp/rtvoice-ca.crt")`

### 7.3 公网域名（Let's Encrypt）

改 `caddy/Caddyfile` 取消模式 A 的注释，把 `voice.example.com` 换成你的真域名，DNS A 记录指到 prod 机公网 IP。
```yaml
voice.example.com {
    handle /asr* { reverse_proxy stt-server:9090 }
    handle /tts/* { reverse_proxy tts-server:9880 }
    handle /voices* { reverse_proxy tts-server:9880 }
    respond 404
}
```
Caddy 自动 ACME 申请 cert（需开放 80/443 公网）。

---

## 7.4 音频格式速查表（SP9 T7 新增 / D1-F5）

所有 STT/TTS endpoint 假设：

| 用途 | 编码 | sample_rate | channels | bytes/sample | 备注 |
|---|---|---|---|---|---|
| STT WS 上传音频 | PCM int16 LE | 16000 Hz | 1 (mono) | 2 | 整段 `bytes` 一帧或分帧 send_bytes 都行 |
| TTS HTTP 返回 | WAV (RIFF) | 24000 Hz | 1 | 2 | 单次 POST /v1/tts/stream Response body |
| TTS WS server→client 帧 | raw PCM int16 LE | 24000 Hz | 1 | 2 | metadata 帧首声明 `sample_rate` |

不匹配会导致识别质量极差或合成失真——别用 44.1kHz / float32 直接喂。

## 7.5 库版本要求（D1-F6）

| Python 库 | 最低版本 | 原因 |
|---|---|---|
| `websockets` | ≥ 11.0 | additional_headers / subprotocols 参数 |
| `httpx` | ≥ 0.27 | async stream + auth |
| `aiohttp` (替代 httpx) | ≥ 3.9 | (可选) |

## 7.6 自环测试 caveat（D1-F8）

文档示例若把 TTS 合成的音频塞回 STT 自环回测，识别结果**会很糟糕**（"你好世界" 可能识别为 "能够做得比我比我..."）。这是因为合成语音对 STT 训练集 OOD（out-of-distribution），**不代表真录音质量**。验证 STT 识别能力请用真人录音或 LibriSpeech / AISHELL 测试集。

---

## 8. 排障

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `connection refused` 到 stt-server / tts-server | docker network 没 join | `docker inspect <cozyvoice容器> -f '{{json .NetworkSettings.Networks}}'` 看是否有 rtvoice_rtvoice_net |
| 401 Unauthorized | RTVOICE_API_KEY 不一致 / 没传 | 在 CozyVoice 容器 `echo $RTVOICE_API_KEY` 与 RTVoice 比对 |
| WS 连接立刻 close（4401）| Bearer 不对 / 三路都没附带 | 看 stt-server log `WS 鉴权失败 client=...` |
| TTS WS 收到 `{"type":"error","message":"..."}`  | server 端推理失败 | `docker logs rtvoice-tts | tail` 看具体异常 |
| CosyVoice 3 延迟没明显比 v0.6 快 | agent 不是真喂 generator / GPU 显存满 | `nvidia-smi`；改 server log 看 `append text token / wait for more` 是否触发 |
| 音色 `unknown voice 'alice'` | 没注册 | `curl /v1/voices` 看注册的音色清单 |

更深入排障 → [OPERATIONS.md §4](./OPERATIONS.md)

---

## 9. 接口契约稳定性

- **HTTP/WS endpoint 路径**：稳定（`/v1/asr` `/v1/tts/stream` `/v1/tts/stream_ws` `/v1/voices/*`）
- **PCM 输出格式**：稳定（24kHz int16 LE mono）
- **Bearer 鉴权方式**：稳定
- **JSON event types**（`partial`/`final`/`error`/`done`）：稳定

如果以后引入破坏性变化，会通过 `/info` 增加 capability 字段（如 `text_streaming` 已有），CozyVoice 端先探测再决定调用路径，**无需协调发版**。
