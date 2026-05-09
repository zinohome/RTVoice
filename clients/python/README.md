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

## Status

**Alpha (0.1.x).** API may change. Pin minor version (`rtvoice-client~=0.1.0`).

## License

Apache 2.0
