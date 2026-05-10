# rtvoice-client

Official Python client for [RTVoice](https://github.com/zinohome/RTVoice) — self-hosted voice services platform.

## Install

```bash
pip install rtvoice-client
```

## Quick start

```python
from rtvoice_client import Client

c = Client(api_key="bear-32-...", base_url="https://rtvoice.your-domain.com")

# STT
text = c.stt.transcribe(open("user.pcm", "rb").read(), sample_rate=16000)

# TTS
pcm = c.tts.synthesize("你好", voice="default_zh_female", speed=1.0)

# Realtime — high-level helper
async for evt in c.realtime.conversation(audio_iter, prompt="你是助手"):
    print(evt)

# LiveKit token (optional advanced mode)
tok = c.tokens.livekit(identity="alice", room="rtvoice-test", ttl_minutes=10)
```

## Async API

```python
from rtvoice_client import AsyncClient

c = AsyncClient(api_key="...", base_url="...")
text = await c.stt.transcribe(pcm)
```

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

## Status

**Alpha (0.1.x).** API may change. Pin minor version (`rtvoice-client~=0.1.0`).

## License

Apache 2.0
