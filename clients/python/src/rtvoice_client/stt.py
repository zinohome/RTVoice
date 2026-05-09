"""STT namespace: REST transcribe + WS stream."""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import websockets

from rtvoice_client._base import _check_response


class AsyncSTT:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
    ) -> str:
        """One-shot transcribe; pcm = int16 LE mono."""
        r = await self._http.post(
            f"{self._base}/v1/asr",
            content=pcm,
            params={"sample_rate": sample_rate},
            headers={"Content-Type": "application/octet-stream"},
        )
        _check_response(r)
        body = r.json()
        return body.get("text", "")

    @asynccontextmanager
    async def stream(self, *, ws_url: str | None = None) -> AsyncIterator["AsyncSTTStream"]:
        """Open WS streaming session; auto-close on exit."""
        if ws_url is None:
            ws_url = self._base.replace("http://", "ws://").replace("https://", "wss://") + "/v1/asr"
        ws = await websockets.connect(ws_url, max_size=None)
        try:
            yield AsyncSTTStream(ws)
        finally:
            try:
                await ws.close()
            except Exception:
                pass


class AsyncSTTStream:
    """Streaming WS session: feed bytes, request_final returns text."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def feed(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def request_final(self, *, timeout: float = 5.0) -> str:
        await self._ws.send("EOS")
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msg = await asyncio.wait_for(
                self._ws.recv(),
                timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
            )
            if isinstance(msg, str):
                ev = json.loads(msg)
                if ev.get("type") == "final":
                    return ev.get("text", "")
        return ""


class SyncSTT:
    """Sync wrapper: each call asyncio.run AsyncSTT method."""

    def __init__(self, inner: AsyncSTT) -> None:
        self._inner = inner

    def transcribe(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        return asyncio.run(self._inner.transcribe(pcm, sample_rate=sample_rate))
