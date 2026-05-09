"""TTS namespace: synthesize (one-shot bytes) + stream (chunked)."""
from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

import httpx

from rtvoice_client._base import _check_response
from rtvoice_client.errors import _raise_for_body, ServerError


class AsyncTTS:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "default_zh_female",
        speed: float = 1.0,
        lang: str = "cmn",
    ) -> bytes:
        """Return entire PCM (24k mono int16) as bytes."""
        r = await self._http.post(
            f"{self._base}/v1/tts/stream",
            json={"text": text, "voice": voice, "speed": speed, "lang": lang},
        )
        _check_response(r)
        return r.content

    async def stream(
        self,
        text: str,
        *,
        voice: str = "default_zh_female",
        speed: float = 1.0,
        lang: str = "cmn",
    ) -> AsyncIterator[bytes]:
        """Yield PCM chunks as they arrive."""
        async with self._http.stream(
            "POST",
            f"{self._base}/v1/tts/stream",
            json={"text": text, "voice": voice, "speed": speed, "lang": lang},
        ) as r:
            if r.status_code >= 400:
                content = await r.aread()
                try:
                    body = json.loads(content)
                except Exception:
                    body = None
                _raise_for_body(body, http_status=r.status_code)
                raise ServerError(
                    code="internal.unknown",
                    message=f"HTTP {r.status_code}",
                    http_status=r.status_code,
                )
            async for chunk in r.aiter_bytes():
                if chunk:
                    yield chunk


class SyncTTS:
    def __init__(self, inner: AsyncTTS) -> None:
        self._inner = inner

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "default_zh_female",
        speed: float = 1.0,
        lang: str = "cmn",
    ) -> bytes:
        return asyncio.run(
            self._inner.synthesize(text, voice=voice, speed=speed, lang=lang)
        )

    def stream(self, text: str, **kwargs):
        """Sync iterator wrapping async stream — drains into list."""
        async def _drain():
            return [c async for c in self._inner.stream(text, **kwargs)]
        chunks = asyncio.run(_drain())
        for c in chunks:
            yield c
