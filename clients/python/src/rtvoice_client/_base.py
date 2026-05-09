"""BaseClient: URL resolution, Bearer headers, response → typed exception."""
from __future__ import annotations
from typing import Any

import httpx

from rtvoice_client.errors import _raise_for_body


def _resolve_urls(
    *,
    base_url: str | None,
    stt_url: str | None,
    tts_url: str | None,
    realtime_url: str | None,
    tokens_url: str | None,
) -> dict[str, str]:
    """Per-service URL override > base_url. base_url required if no per-service URL given."""
    fallback = base_url
    overrides = {"stt": stt_url, "tts": tts_url, "realtime": realtime_url, "tokens": tokens_url}
    if fallback is None and not all(overrides.values()):
        missing = [k for k, v in overrides.items() if v is None]
        raise ValueError(
            f"base_url is None and these per-service URLs missing: {missing}"
        )
    return {k: (v or fallback) for k, v in overrides.items()}  # type: ignore[misc]


def _build_headers(api_key: str | None) -> dict[str, str]:
    h: dict[str, str] = {"User-Agent": "rtvoice-client/0.1.0"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _try_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _check_response(resp: httpx.Response) -> None:
    """Raise typed exception on RTVoice error body; else return."""
    body = _try_json(resp)
    if resp.status_code >= 400:
        _raise_for_body(body, http_status=resp.status_code)
        from rtvoice_client.errors import ServerError
        raise ServerError(
            code="internal.unknown",
            message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code,
        )


# ---------------- Async + sync clients ----------------


class AsyncClient:
    """Async entry point: AsyncClient(api_key=..., base_url=...).stt / .tts / .realtime / .tokens"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        stt_url: str | None = None,
        tts_url: str | None = None,
        realtime_url: str | None = None,
        tokens_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._urls = _resolve_urls(
            base_url=base_url, stt_url=stt_url, tts_url=tts_url,
            realtime_url=realtime_url, tokens_url=tokens_url,
        )
        self._api_key = api_key
        self._headers = _build_headers(api_key)
        self._http = httpx.AsyncClient(headers=self._headers, timeout=timeout)
        self._stt: Any = None
        self._tts: Any = None
        self._realtime: Any = None
        self._tokens: Any = None

    @property
    def stt(self):
        if self._stt is None:
            from rtvoice_client.stt import AsyncSTT
            self._stt = AsyncSTT(self._http, self._urls["stt"])
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from rtvoice_client.tts import AsyncTTS
            self._tts = AsyncTTS(self._http, self._urls["tts"])
        return self._tts

    @property
    def realtime(self):
        if self._realtime is None:
            from rtvoice_client.realtime import AsyncRealtime
            self._realtime = AsyncRealtime(self._http, self._urls["realtime"], self._api_key)
        return self._realtime

    @property
    def tokens(self):
        if self._tokens is None:
            from rtvoice_client.tokens import AsyncTokens
            self._tokens = AsyncTokens(self._http, self._urls["tokens"])
        return self._tokens

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


class Client:
    """Sync entry point — wraps AsyncClient via single long-lived event loop.

    持有一个 asyncio.new_event_loop()，所有 sync 调用通过它跑，
    避免每次 asyncio.run() 创新 loop 导致 httpx connection pool 引用旧 loop 报
    'Event loop is closed' 的 bug（SP4-fix-1）。

    Use AsyncClient when running inside an async event loop (FastAPI etc.).
    """

    def __init__(self, **kwargs: Any) -> None:
        import asyncio
        self._loop = asyncio.new_event_loop()
        self._async = AsyncClient(**kwargs)
        self._stt: Any = None
        self._tts: Any = None
        self._realtime: Any = None
        self._tokens: Any = None

    def _run(self, coro: Any) -> Any:
        """Execute a coroutine on this Client's persistent event loop."""
        return self._loop.run_until_complete(coro)

    @property
    def stt(self):
        if self._stt is None:
            from rtvoice_client.stt import SyncSTT
            self._stt = SyncSTT(self._async.stt, self._run)
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from rtvoice_client.tts import SyncTTS
            self._tts = SyncTTS(self._async.tts, self._run)
        return self._tts

    @property
    def realtime(self):
        if self._realtime is None:
            from rtvoice_client.realtime import SyncRealtime
            self._realtime = SyncRealtime(self._async.realtime, self._run)
        return self._realtime

    @property
    def tokens(self):
        if self._tokens is None:
            from rtvoice_client.tokens import SyncTokens
            self._tokens = SyncTokens(self._async.tokens, self._run)
        return self._tokens

    def close(self) -> None:
        if self._loop.is_closed():
            return
        try:
            self._loop.run_until_complete(self._async.aclose())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
