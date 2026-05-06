"""TTS HTTP 流式客户端。

连接 tts-server，按 [services/tts-server/app/main.py] 文档化的 HTTP 协议工作：
    POST /tts/stream  body={"text": "...", "voice": "...", "speed": 1.0}
    → chunked transfer，binary PCM int16 LE 24kHz mono

设计：
    - 每次合成一个 HTTP 请求（短连接）；TTS 不需要长连保持状态
    - 使用 httpx.AsyncClient 流式 receiver；可被外部 cancel（barge-in 关键）
    - yield 的是 PCM bytes 块（chunk size 由 server / 网络决定）
    - 调用方负责切成 LiveKit 期望的固定帧大小
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection, connect as ws_connect

log = logging.getLogger("rtvoice.agent.tts")


class TTSClient:
    def __init__(
        self,
        base_url: str,
        voice: str = "zf_xiaobei",
        lang: str = "cmn",
        speed: float = 1.0,
        timeout: float = 60.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.lang = lang
        self.speed = speed
        self.api_key = api_key
        # 长 timeout：CPU 上 Kokoro 合成一段 30s 文本可能要 1 分钟；放宽防止超时切断
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """yield PCM int16 LE 24kHz mono bytes 块。

        失败时抛 httpx 异常；外部 catch 决定是否 fallback。
        """
        if not text.strip():
            return
        log.info("[TTS] text=%r voice=%s", text, self.voice)
        payload = {
            "text": text,
            "voice": self.voice,
            "lang": self.lang,
            "speed": self.speed,
        }
        url = f"{self.base_url}/tts/stream"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        async with self._client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(
                    f"TTS server {resp.status_code}: {body.decode(errors='replace')[:200]}"
                )
            sr = resp.headers.get("X-Sample-Rate")
            log.debug("[TTS] streaming sr=%s", sr)
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    async def probe_capabilities(self) -> dict[str, Any]:
        """GET /info；返回 dict 含 text_streaming/backend 等。失败抛异常。"""
        url = f"{self.base_url}/info"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        # /info 在 v0.6 是公开的；v0.7 同样公开（不加 Depends）。
        # 即便加了，agent-worker 的 RTVOICE_API_KEY 也对得上。
        r = await self._client.get(url, headers=headers, timeout=10.0)
        r.raise_for_status()
        return r.json()

    async def open_ws(self) -> "TTSWSStream":
        """打开 v0.7 的双向 WS：先发 metadata，之后 send_text 增量、await audio_chunks。

        协议：见 services/tts-server/app/main_cosyvoice3.py /tts/stream_ws
        失败抛 websockets 异常；调用方 catch 决定是否 fallback HTTP 单次 POST。
        """
        # http(s)://host:port → ws(s)://host:port
        ws_url = self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        full = f"{ws_url}/tts/stream_ws"
        extra_headers = {}
        subprotocols = None
        if self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"
            subprotocols = [f"bearer.{self.api_key}"]
        ws = await ws_connect(
            full,
            additional_headers=extra_headers or None,
            subprotocols=subprotocols,
            max_size=None,
            ping_interval=20,
            ping_timeout=10,
        )
        meta = {"voice": self.voice, "lang": self.lang, "speed": self.speed}
        await ws.send(json.dumps(meta))
        log.info("[TTS-ws] connected voice=%s speed=%.2f", self.voice, self.speed)
        return TTSWSStream(ws)

    async def close(self) -> None:
        await self._client.aclose()


class TTSWSStream:
    """v0.7 双向流式句柄。

    用法：
        ws = await tts.open_ws()
        async def feed():
            async for delta in llm.stream(user_text):
                await ws.send_text(delta)
            await ws.eos()
        feed_task = asyncio.create_task(feed())
        async for pcm_chunk in ws.audio_chunks():
            await audio_publish(pcm_chunk)
        await feed_task

    barge-in：直接 await ws.aclose() —— 服务端检测 disconnect 关闭推理。
    """

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._sent_eos = False

    async def send_text(self, text: str) -> None:
        if not text or self._sent_eos:
            return
        await self._ws.send(text)

    async def eos(self) -> None:
        if self._sent_eos:
            return
        self._sent_eos = True
        await self._ws.send("EOS")

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        """yield PCM bytes；遇到 {"type":"done"} 正常退出，{"type":"error"} 抛异常。"""
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    yield msg
                else:
                    try:
                        ev = json.loads(msg)
                    except json.JSONDecodeError:
                        log.warning("[TTS-ws] 非 JSON 文本: %r", msg[:80])
                        continue
                    t = ev.get("type")
                    if t == "done":
                        log.info("[TTS-ws] done chunks=%s", ev.get("chunks"))
                        return
                    if t == "error":
                        raise RuntimeError(f"TTS-ws server error: {ev.get('message')}")
        except websockets.exceptions.ConnectionClosed as e:
            log.info("[TTS-ws] connection closed: %s", e)

    async def aclose(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass
