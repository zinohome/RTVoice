"""QuotaTracker: sessions_concurrent + sessions_per_hour 强制执行。

In-memory backend；prod 可换 Redis 实现（同接口）。
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from rtvoice_auth.errors import QuotaExceeded
from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.quota")


class QuotaTracker:
    """In-memory rolling-hour + concurrent counter."""

    def __init__(self) -> None:
        self._concurrent: dict[str, int] = {}
        self._hour_count: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()

    async def acquire_session(self, key: Key) -> None:
        """create session 前调；超限 raise QuotaExceeded（counter rollback）。"""
        async with self._lock:
            bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            buckets = self._hour_count.setdefault(key.id, {})
            self._gc_old_buckets(buckets, bucket)
            new_hour = buckets.get(bucket, 0) + 1
            if new_hour > key.sessions_per_hour_max:
                raise QuotaExceeded("auth.quota_per_hour",
                                    f"key {key.id} reached {key.sessions_per_hour_max}/hour")
            new_concurrent = self._concurrent.get(key.id, 0) + 1
            if new_concurrent > key.sessions_concurrent_max:
                raise QuotaExceeded("auth.quota_concurrent",
                                    f"key {key.id} reached {key.sessions_concurrent_max} concurrent")
            buckets[bucket] = new_hour
            self._concurrent[key.id] = new_concurrent

    async def release_session(self, key_id: str) -> None:
        """session cleanup 时调；DECR concurrent；不动 per_hour（rolling）。"""
        async with self._lock:
            cur = self._concurrent.get(key_id, 0)
            if cur > 0:
                self._concurrent[key_id] = cur - 1

    @staticmethod
    def _gc_old_buckets(buckets: dict[str, int], current_bucket: str) -> None:
        keep = {current_bucket}
        for b in list(buckets.keys()):
            if b not in keep:
                buckets.pop(b, None)

    def get_concurrent(self, key_id: str) -> int:
        return self._concurrent.get(key_id, 0)
