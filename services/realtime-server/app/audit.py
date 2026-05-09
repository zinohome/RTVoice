"""AuditWriter: per-session 异步 JSONL writer (per spec §5.3)."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("rtvoice.realtime.audit")


class AuditWriter:
    """异步 append-only JSONL；turn 永不阻塞。

    路径：{base_dir}/{YYYY-MM-DD}/{session_id}.jsonl
    日期取自构造时刻，全程一个文件（即便 session 跨 0 点）。
    """

    def __init__(self, session_id: str, base_dir: str, queue_max: int = 1000) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.path = Path(base_dir) / date / f"{session_id}.jsonl"
        self._dir_ok = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.warning("audit dir mkdir failed for %s: %s", self.path, e)
            self._dir_ok = False
        self._q: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._closed = False
        self._task: asyncio.Task = asyncio.create_task(self._loop())

    async def write(self, event: dict) -> None:
        """O(1) 微秒级；queue full 直接 drop + warn。"""
        if self._closed or not self._dir_ok:
            return
        item = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **event}
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("audit queue full for %s, dropping %s",
                        self.path.name, item.get("event"))

    async def _loop(self) -> None:
        while True:
            try:
                first = await self._q.get()
            except asyncio.CancelledError:
                return
            batch = [first]
            for _ in range(49):
                try:
                    batch.append(self._q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await asyncio.to_thread(self._flush_sync, batch)
            except Exception:
                log.exception("audit flush failed for %s", self.path.name)

    def _flush_sync(self, batch: list[dict]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for item in batch:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def aclose(self) -> None:
        """停接收 + drain 剩余 + cancel writer task."""
        if self._closed:
            return
        self._closed = True
        for _ in range(20):
            if self._q.empty():
                await asyncio.sleep(0.01)
                if self._q.empty():
                    break
            await asyncio.sleep(0.05)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
