"""STT namespace — WS streaming + transcribe convenience.

RTVoice STT 是 WS-only（`/v1/asr`），没 HTTP REST 端点。
- `stream()`：低层 WS context manager，最大灵活性
- `transcribe(pcm)`：高层便利，内部走 WS 一次 send-EOS-recv 完整流程

SP13 T6：之前的 transcribe() 错以为有 HTTP `/v1/asr` REST 端点（实际只 WS）；
之前的 stream() 不传 api_key（WS 无鉴权失败）。本次修两处。
"""
from __future__ import annotations
import asyncio
import json
import ssl as _ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import websockets


def _http_to_ws(url: str) -> str:
    return url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


def _ws_kwargs(api_key: str | None, verify: bool | str | _ssl.SSLContext) -> dict:
    """构造 websockets.connect 的 kwargs：subprotocol bearer + 可选 SSL context。"""
    kw: dict[str, Any] = {"max_size": None}
    if api_key:
        # RTVoice WS 接受 3 路 Bearer：subprotocol / header / query。subprotocol 最浏览器友好
        kw["subprotocols"] = [f"bearer.{api_key}"]
        # server-to-server 也带 Authorization header（双保险）
        kw["additional_headers"] = {"Authorization": f"Bearer {api_key}"}
    # wss + 自签 CA：caller 应传 verify=<ca-path> 或 verify=False
    if verify is False:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        kw["ssl"] = ctx
    elif isinstance(verify, str):
        ctx = _ssl.create_default_context(cafile=verify)
        kw["ssl"] = ctx
    elif isinstance(verify, _ssl.SSLContext):
        kw["ssl"] = verify
    return kw


class AsyncSTT:
    def __init__(self, http: httpx.AsyncClient, base_url: str,
                 api_key: str | None = None) -> None:
        self._http = http  # 备用（未来 REST endpoint）
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        timeout: float = 30.0,
        verify: bool | str | _ssl.SSLContext = True,
    ) -> str:
        """高层便利：整段 PCM int16 LE mono → 一次 WS 流程 → 返 final text。

        Args:
            pcm: PCM int16 LE mono bytes（sample_rate 见参数）
            sample_rate: 服务端固定接收 16kHz；这里只是兼容字段（不重采样）
            timeout: 总超时（含 send + EOS + 等 final）
            verify: 同 httpx — True / False / <ca-path> / SSLContext
        """
        if sample_rate != 16000:
            raise ValueError(f"RTVoice STT 只接受 16kHz；got {sample_rate}")
        async with self.stream(verify=verify) as s:
            await s.feed(pcm)
            return await s.request_final(timeout=timeout)

    @asynccontextmanager
    async def stream(
        self,
        *,
        ws_url: str | None = None,
        verify: bool | str | _ssl.SSLContext = True,
    ) -> AsyncIterator["AsyncSTTStream"]:
        """打开 WS streaming session；context exit 时自动关闭。

        必传 api_key（来自父 Client）→ 走 subprotocol `bearer.<token>`。
        verify 传给 SSL：True / False / <ca-path>。
        """
        if ws_url is None:
            ws_url = _http_to_ws(self._base) + "/v1/asr"
        ws = await websockets.connect(ws_url, **_ws_kwargs(self._api_key, verify))
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
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                msg = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=max(0.1, deadline - loop.time()),
                )
            except asyncio.TimeoutError:
                break
            if isinstance(msg, str):
                ev = json.loads(msg)
                if ev.get("type") == "final":
                    return ev.get("text", "")
        return ""


class SyncSTT:
    """Sync wrapper: each call uses parent Client's runner."""

    def __init__(self, inner: AsyncSTT, runner=None) -> None:
        self._inner = inner
        self._runner = runner if runner is not None else _legacy_runner

    def transcribe(self, pcm: bytes, *, sample_rate: int = 16000,
                   timeout: float = 30.0, verify=True) -> str:
        return self._runner(self._inner.transcribe(
            pcm, sample_rate=sample_rate, timeout=timeout, verify=verify))


def _legacy_runner(coro):
    """Fallback for tests / direct construction without parent Client."""
    return asyncio.run(coro)
