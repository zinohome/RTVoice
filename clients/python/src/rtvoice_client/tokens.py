"""LiveKit tokens namespace."""
from __future__ import annotations
import asyncio

import httpx

from rtvoice_client._base import _check_response
from rtvoice_client.models import TokenRequest, TokenResponse


class AsyncTokens:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def livekit(
        self,
        *,
        identity: str,
        room: str,
        ttl_minutes: int = 10,
    ) -> TokenResponse:
        req = TokenRequest(identity=identity, room=room, ttl_minutes=ttl_minutes)
        r = await self._http.post(
            f"{self._base}/v1/tokens",
            json=req.model_dump(),
        )
        _check_response(r)
        return TokenResponse.model_validate(r.json())


class SyncTokens:
    def __init__(self, inner: AsyncTokens, runner=None) -> None:
        self._inner = inner
        self._runner = runner if runner is not None else _legacy_runner

    def livekit(self, *, identity: str, room: str, ttl_minutes: int = 10) -> TokenResponse:
        return self._runner(
            self._inner.livekit(identity=identity, room=room, ttl_minutes=ttl_minutes)
        )


def _legacy_runner(coro):
    return asyncio.run(coro)
