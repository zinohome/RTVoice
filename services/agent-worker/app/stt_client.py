"""STT WebSocket 客户端。

连接 stt-server，按 [services/stt-server/app/main.py] 文档化的 WS 协议工作：
    - send bytes  : PCM int16 LE 16kHz mono
    - send "EOS"  : 触发 final
    - recv json   : {type: partial|final, text}

设计：
    - 长连接：agent 启动时 connect，活到 close
    - 单条 utterance 的边界由 EOS 标记，不开新连接（省握手）
    - 流式 feed 不阻塞；reader 任务后台收 partial/final 事件
    - request_final() 发 EOS 然后等 final（带超时）
    - 连接断开会触发自动重连（指数退避，但 v0.3 简化为单次重连尝试）
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection, connect

log = logging.getLogger("rtvoice.agent.stt")


PartialCallback = Callable[[str], Awaitable[None] | None]


class STTClient:
    def __init__(
        self,
        url: str,
        on_partial: PartialCallback | None = None,
    ) -> None:
        self.url = url
        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task | None = None
        self._final_event = asyncio.Event()
        self._final_text: str = ""
        self._on_partial = on_partial
        self._closed = False

    async def connect(self) -> None:
        """建立 WS 连接并启动 reader 任务。"""
        log.info("连接 STT: %s", self.url)
        self._ws = await connect(
            self.url,
            max_size=None,        # 不限制单帧大小
            ping_interval=20,     # 保活
            ping_timeout=10,
        )
        log.info("STT WS 已连接")
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    log.warning("STT 服务端发了 binary（不应出现），忽略")
                    continue
                try:
                    ev = json.loads(msg)
                except json.JSONDecodeError:
                    log.warning("STT 非 JSON 消息: %r", msg[:80])
                    continue
                t = ev.get("type")
                text = ev.get("text", "")
                if t == "partial":
                    if self._on_partial:
                        try:
                            r = self._on_partial(text)
                            if asyncio.iscoroutine(r):
                                await r
                        except Exception:
                            log.exception("on_partial 回调异常")
                elif t == "final":
                    self._final_text = text
                    self._final_event.set()
                elif t == "error":
                    log.error("STT 服务端 error: %s", ev.get("message"))
        except websockets.exceptions.ConnectionClosed as e:
            log.warning("STT WS closed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("STT reader 异常")

    async def feed(self, pcm_int16le_bytes: bytes) -> None:
        """喂入 PCM bytes（int16 LE 16kHz mono）。失败不抛，只 log（避免污染主管线）。"""
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(pcm_int16le_bytes)
        except websockets.exceptions.ConnectionClosed:
            log.warning("STT 连接已关闭，丢弃 %d bytes", len(pcm_int16le_bytes))
        except Exception:
            log.exception("STT feed 异常")

    async def request_final(self, timeout: float = 5.0) -> str:
        """发 EOS 并等 final 事件返回。超时返回当前 partial 或空串。"""
        if self._ws is None or self._closed:
            return ""
        self._final_event.clear()
        self._final_text = ""
        try:
            await self._ws.send("EOS")
        except websockets.exceptions.ConnectionClosed:
            log.warning("STT 连接已关闭，无法发 EOS")
            return ""
        try:
            await asyncio.wait_for(self._final_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("STT final 超时（%.1fs），返回空串", timeout)
            return ""
        return self._final_text

    async def reset(self) -> None:
        """丢弃当前 stream 状态（不发 EOS，纯重置）。"""
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send("RESET")
        except Exception:
            pass

    async def close(self) -> None:
        self._closed = True
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        log.info("STT 客户端已关闭")
