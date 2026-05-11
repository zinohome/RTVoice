"""Hot reload watchers: file watcher (YAML) + Redis pubsub listener."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Callable, Awaitable

log = logging.getLogger("rtvoice.auth.watcher")

ReloadCallback = Callable[[], Awaitable[None]]


class _Debouncer:
    def __init__(self, callback: ReloadCallback, delay_ms: int = 100):
        self._cb = callback
        self._delay = delay_ms / 1000
        self._task: asyncio.Task | None = None

    def fire(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay)
            await self._cb()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("debounce callback failed")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class YamlFileWatcher:
    def __init__(self, path: str, on_change: ReloadCallback, debounce_ms: int = 100):
        self.path = path
        self._debouncer = _Debouncer(on_change, debounce_ms)
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        self._loop = asyncio.get_event_loop()
        debouncer = self._debouncer
        loop = self._loop
        target_basename = os.path.basename(self.path)

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if os.path.basename(event.src_path) == target_basename:
                    loop.call_soon_threadsafe(debouncer.fire)
            def on_created(self, event):
                self.on_modified(event)
            def on_moved(self, event):
                dest = getattr(event, "dest_path", "")
                if os.path.basename(dest) == target_basename:
                    loop.call_soon_threadsafe(debouncer.fire)

        parent = os.path.dirname(os.path.abspath(self.path)) or "."
        self._observer = Observer()
        self._observer.schedule(_Handler(), parent, recursive=False)
        self._observer.start()
        log.info("yaml file watcher started: %s", self.path)

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        await self._debouncer.stop()
        log.info("yaml file watcher stopped")


class RedisPubSubListener:
    """订阅 'rtvoice:keys:changed' channel；自带 reconnect on disconnect."""

    def __init__(self, redis_client, on_change: ReloadCallback,
                 channel: str = "rtvoice:keys:changed", debounce_ms: int = 100):
        self._r = redis_client
        self._channel = channel
        self._debouncer = _Debouncer(on_change, debounce_ms)
        self._task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self._closed:
            try:
                pubsub = self._r.pubsub()
                await pubsub.subscribe(self._channel)
                log.info("redis pubsub subscribed: %s", self._channel)
                try:
                    async for msg in pubsub.listen():
                        if self._closed:
                            break
                        if msg.get("type") == "message":
                            self._debouncer.fire()
                finally:
                    try:
                        await pubsub.unsubscribe(self._channel)
                        await pubsub.aclose()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("redis pubsub loop error; reconnect in 1s")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._debouncer.stop()
        log.info("redis pubsub listener stopped")
