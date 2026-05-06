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

import logging
from collections.abc import AsyncIterator

import httpx

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

    async def close(self) -> None:
        await self._client.aclose()
