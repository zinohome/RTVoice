"""Realtime namespace: create_session + connect + conversation helper."""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterable, AsyncIterator

import httpx
import websockets

from rtvoice_client._base import _check_response
from rtvoice_client.errors import RTVoiceError
from rtvoice_client.models import (
    SessionCreateRequest, SessionCreateResponse,
    RealtimeEvent, ResponsePCM, parse_realtime_event,
)


class AsyncRealtimeSession:
    """Active WS session: send PCM, send EOS, send updates, receive typed events."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def feed(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def eos(self) -> None:
        await self._ws.send("audio.eos")

    async def update_prompt(self, prompt: str) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "prompt": prompt}))

    async def update_voice(self, voice: str) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "voice": voice}))

    async def update_speed(self, speed: float) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "speed": speed}))

    async def clear_memory(self) -> None:
        await self._ws.send(json.dumps({"type": "memory.clear"}))

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        """Iterate WS frames -> typed RealtimeEvent."""
        while True:
            msg = await self._ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                yield ResponsePCM(data=bytes(msg))
                continue
            try:
                payload = json.loads(msg)
            except Exception:
                continue
            evt = parse_realtime_event(payload)
            if evt is not None:
                yield evt


class AsyncRealtime:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    async def create_session(
        self,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        prompt: str | None = None,
        audit_persist: bool = False,
    ) -> SessionCreateResponse:
        req = SessionCreateRequest(
            voice=voice, speed=speed, prompt=prompt, audit_persist=audit_persist,
        )
        r = await self._http.post(
            f"{self._base}/v1/sessions",
            json=req.model_dump(exclude_none=True),
        )
        _check_response(r)
        return SessionCreateResponse.model_validate(r.json())

    @asynccontextmanager
    async def connect(self, sess: SessionCreateResponse) -> AsyncIterator[AsyncRealtimeSession]:
        """Open WS to sess.ws_url with bearer; yield session helper."""
        subprotocols = [f"bearer.{self._api_key}"] if self._api_key else None
        ws = await websockets.connect(
            sess.ws_url,
            max_size=None,
            subprotocols=subprotocols,
        )
        try:
            yield AsyncRealtimeSession(ws)
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def conversation(
        self,
        audio_iter: AsyncIterable[bytes],
        *,
        voice: str | None = None,
        speed: float = 1.0,
        prompt: str | None = None,
        audit_persist: bool = False,
    ) -> AsyncIterator[RealtimeEvent]:
        """High-level helper: create session + connect WS + feed audio + yield events until response.done."""
        sess = await self.create_session(
            voice=voice, speed=speed, prompt=prompt, audit_persist=audit_persist,
        )
        async with self.connect(sess) as ws_sess:
            async def _feed():
                try:
                    async for chunk in audio_iter:
                        await ws_sess.feed(chunk)
                finally:
                    try:
                        await ws_sess.eos()
                    except Exception:
                        pass

            feed_task = asyncio.create_task(_feed())
            try:
                async for evt in ws_sess.events():
                    yield evt
                    if hasattr(evt, "type") and evt.type == "response.done":
                        break
            finally:
                if not feed_task.done():
                    feed_task.cancel()
                    try:
                        await feed_task
                    except (asyncio.CancelledError, Exception):
                        pass


class SyncRealtime:
    def __init__(self, inner: AsyncRealtime, runner=None) -> None:
        self._inner = inner
        self._runner = runner if runner is not None else _legacy_runner

    def create_session(self, **kwargs) -> SessionCreateResponse:
        return self._runner(self._inner.create_session(**kwargs))


def _legacy_runner(coro):
    return asyncio.run(coro)
