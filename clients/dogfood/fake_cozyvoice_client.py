"""SP8-D1 fake CozyVoice client — token + TTS + STT (self-loopback).

Run:
    export RTVOICE_API_KEY=$(cat /tmp/sp8-d1/secret.txt)
    # NOTE: prod stt(:9090)/tts(:9880) are NOT exposed externally — we tunnel
    # them locally first. A real consumer without ssh cannot do this; see
    # docs/superpowers/findings/sp8-d1-findings.md (F1).
    ssh -f -N -L 19090:172.18.0.4:9090 -L 19880:172.18.0.3:9880 root@192.168.66.163
    export RTVOICE_TOKEN_URL=http://192.168.66.163:8000
    export RTVOICE_TTS_URL=http://127.0.0.1:19880
    export RTVOICE_STT_URL=ws://127.0.0.1:19090/v1/asr
    python3 fake_cozyvoice_client.py
Output: /tmp/sp8-d1/hello.wav + final STT text printed.
"""
import asyncio, audioop, httpx, json, os, struct, sys, wave, websockets

KEY = os.environ["RTVOICE_API_KEY"].strip()
TOK = os.environ.get("RTVOICE_TOKEN_URL", "http://192.168.66.163:8000")
TTS = os.environ.get("RTVOICE_TTS_URL", "http://127.0.0.1:19880")
STT = os.environ.get("RTVOICE_STT_URL", "ws://127.0.0.1:19090/v1/asr")
AUTH = {"Authorization": f"Bearer {KEY}"}

async def main():
    # 1) token-server: mint a LiveKit JWT (advanced-mode hand-off demo)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{TOK}/v1/tokens", headers=AUTH,
                         json={"identity": "cozy-demo", "room": "demo", "ttl_minutes": 5})
        r.raise_for_status()
        print("token: ok", r.json()["token"][:32], "...")

    # 2) TTS: synthesize "你好世界" → PCM 24k mono int16 LE
    pcm24 = bytearray()
    async with httpx.AsyncClient(timeout=60) as c:
        async with c.stream("POST", f"{TTS}/v1/tts/stream", headers=AUTH,
                            json={"text": "你好世界，今天天气很好。",
                                  "voice": "default_zh_female", "lang": "cmn", "speed": 1.0}) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes(4096): pcm24.extend(chunk)
    print(f"tts: ok {len(pcm24)} bytes pcm24k")

    # write a playable wav of the TTS output
    with wave.open("/tmp/sp8-d1/hello.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(bytes(pcm24))

    # 3) STT loopback: 24k → 16k resample, stream WS, get final transcript
    pcm16, _ = audioop.ratecv(bytes(pcm24), 2, 1, 24000, 16000, None)
    async with websockets.connect(STT, additional_headers=AUTH, max_size=None) as ws:
        frame = 16000 * 2 // 10  # ~100ms
        for i in range(0, len(pcm16), frame):
            await ws.send(pcm16[i:i + frame])
            await asyncio.sleep(0.02)  # pacing — server endpoint detector wants real-time-ish
        await ws.send("EOS")
        text = ""
        async for msg in ws:
            ev = json.loads(msg)
            if ev.get("type") == "final":
                text = ev.get("text", ""); break
            if ev.get("type") == "error":
                print("stt error:", ev, file=sys.stderr); sys.exit(2)
    print(f"stt: ok -> {text!r}")

asyncio.run(main())
