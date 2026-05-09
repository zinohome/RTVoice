# RTVoice → CozyVoice 集成指南

本文档示范如何把 RTVoice 集成到任意客户端项目作为本地后端。CozyVoice 是其一示例；其他场景（Discord bot / 客服系统 / 自动化 / 移动 app）参照同样模式。

**对象读者**：CozyVoice 项目的开发者。前提：RTVoice prod 已部署（按 [DEPLOY.md](./DEPLOY.md)），跑在 v0.7.0+。

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

### 2.1 设鉴权 key（一次性）

```bash
ssh root@192.168.66.163
cd /data/RTVoice

# 生成 32 字符 key（保密 —— 同时存到 CozyVoice 的 .env）
KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "RTVOICE_API_KEY=$KEY"

# 加到 RTVoice 的 .env
echo "RTVOICE_API_KEY=$KEY" >> .env

# rebuild + restart 让 stt-server / tts-server / agent-worker 三方同步加载 key
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
               build stt-server tts-server agent-worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
               up -d stt-server tts-server agent-worker
```

> 验证：`curl -i http://127.0.0.1:9090/v1/asr`（无 token）应该 → 拒绝 / 4401。

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
r = urllib.request.urlopen(f'http://tts-server:9880/info', timeout=5,
  headers={'Authorization': f'Bearer {os.environ[\"RTVOICE_API_KEY\"]}'})
print(r.read().decode())
"
# 期望：{"backend":"cosyvoice3","model":"Fun-CosyVoice3-0.5B-2512", ...}
```

---

## 4. Endpoints 参考

| 用途 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| 健康检查 | GET | `/health` | 无 |
| 服务信息 | GET | `/info` | 无（公开元数据） |
| STT 流式识别 | WS | `/v1/asr` | Bearer |
| TTS 单次合成 | POST | `/v1/tts/stream` | Bearer |
| **TTS 双向流式** | **WS** | **`/v1/tts/stream_ws`** | **Bearer** |
| 列出音色 | GET | `/v1/voices` | Bearer |
| 注册音色（admin） | POST | `/v1/voices` | TTS_ADMIN_API_KEY |
| 删除音色（admin） | DELETE | `/v1/voices/{id}` | TTS_ADMIN_API_KEY |

### 鉴权方式

**HTTP**：`Authorization: Bearer <RTVOICE_API_KEY>`

**WebSocket**（任选一种）:
1. `Authorization: Bearer <KEY>` header（推荐 server-to-server）
2. `Sec-WebSocket-Protocol: bearer.<KEY>`（浏览器场景，标准 subprotocol）
3. `?token=<KEY>` query param（兜底，会进 access log）

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

### 7.2 跨机内网（自签 TLS）

启用 `docker-compose.tls.yml` + `caddy/Caddyfile`（默认走 `tls internal`）：
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               -f docker-compose.tls.yml --profile prod up -d
```

CozyVoice 端：
```
RTVOICE_STT_URL=wss://192.168.66.163/v1/asr
RTVOICE_TTS_URL=https://192.168.66.163
```
首次需信任 caddy 自签 root CA：
```bash
docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt > /tmp/root.crt
# 在 CozyVoice 端：cp /tmp/root.crt /usr/local/share/ca-certificates/ && update-ca-certificates
```

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
