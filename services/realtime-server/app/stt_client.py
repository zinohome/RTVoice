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

可靠性（v0.6.2 加固）：
    - 初次 connect：指数退避重试（1s→2s→4s→8s→16s），默认 5 次
    - reader 检测到 ConnectionClosed → 后台触发自动重连
    - 重连期间 feed 静默 drop，request_final 立刻返回空串
    - 重连成功后 sherpa-onnx 服务端是新 stream → 当前 utterance 丢失，
      下一轮自动恢复正常
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection, connect

log = logging.getLogger("rtvoice.agent.stt")


PartialCallback = Callable[[str], Awaitable[None] | None]

STT_CONNECT_RETRIES = int(os.environ.get("STT_CONNECT_RETRIES", "5"))
STT_CONNECT_BACKOFF_INITIAL_S = float(os.environ.get("STT_CONNECT_BACKOFF_INITIAL_S", "1.0"))
STT_CONNECT_BACKOFF_MAX_S = float(os.environ.get("STT_CONNECT_BACKOFF_MAX_S", "16.0"))


class STTClient:
    def __init__(
        self,
        url: str,
        on_partial: PartialCallback | None = None,
        api_key: str | None = None,
        connect_retries: int = STT_CONNECT_RETRIES,
        connect_backoff_initial_s: float = STT_CONNECT_BACKOFF_INITIAL_S,
        connect_backoff_max_s: float = STT_CONNECT_BACKOFF_MAX_S,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task | None = None
        self._final_event = asyncio.Event()
        self._final_text: str = ""
        self._on_partial = on_partial
        self._closed = False
        # 重连保护：单飞重连，防多个 reader-loop 退出同时拉起多次
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None
        self._connect_retries = connect_retries
        self._backoff_initial = connect_backoff_initial_s
        self._backoff_max = connect_backoff_max_s

    async def connect(self) -> None:
        """初次连接（带重试）。失败 N 次后抛 ConnectionError。"""
        await self._connect_with_retry()

    async def _connect_with_retry(self) -> None:
        backoff = self._backoff_initial
        last_exc: Exception | None = None
        for attempt in range(1, self._connect_retries + 1):
            if self._closed:
                return
            try:
                await self._do_connect()
                return
            except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as e:
                last_exc = e
                log.warning("STT 连接失败 #%d/%d: %s；%.1fs 后重试",
                            attempt, self._connect_retries, e, backoff)
                if attempt < self._connect_retries:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._backoff_max)
        raise ConnectionError(f"STT 连接失败 {self._connect_retries} 次：{last_exc}")

    async def _do_connect(self) -> None:
        """单次 connect + 启动 reader。失败抛原 websockets/OSError 异常。"""
        log.info("连接 STT: %s", self.url)
        # 服务端接受三种 Bearer 来源；这里同时带 header + subprotocol
        # （中间代理可能 strip header，subprotocol 是 WebSocket 标准字段不会被改）
        extra_headers: dict[str, str] = {}
        subprotocols = None
        if self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"
            subprotocols = [f"bearer.{self.api_key}"]
        self._ws = await connect(
            self.url,
            max_size=None,
            ping_interval=20,
            ping_timeout=10,
            additional_headers=extra_headers or None,
            subprotocols=subprotocols,
        )
        log.info("STT WS 已连接")
        self._reader_task = asyncio.create_task(self._reader_loop())

    def _schedule_reconnect(self) -> None:
        """reader 异常退出后调度后台重连；幂等（lock 保护）。"""
        if self._closed:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_async())

    async def _reconnect_async(self) -> None:
        async with self._reconnect_lock:
            if self._closed or self._ws is not None:
                return
            log.info("STT 自动重连开始…")
            try:
                await self._connect_with_retry()
                log.info("STT 自动重连成功")
            except Exception as e:
                log.error("STT 自动重连失败：%s（agent 此后 STT 静默；需重启）", e)

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
        finally:
            # WS 断开 → 标记 + 调度重连（不阻塞 reader 退出）
            self._ws = None
            self._schedule_reconnect()

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
        # 先停 reconnect 任务，避免 close 期间又拉起新连接
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
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
