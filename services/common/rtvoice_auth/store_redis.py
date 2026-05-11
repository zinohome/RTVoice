"""Redis KeyStore backend.

Schema:
  rtvoice:key:{id}             HASH（Key model 字段 → string）
  rtvoice:hash2id:{hash}       STRING → key_id（反查 O(1)）
  rtvoice:keys                 SET（所有 key_id；用于 list_all + any_keys）
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.store_redis")


class RedisKeyStore:
    def __init__(self, redis_client: Any) -> None:
        """redis_client: redis.asyncio.Redis 或 fakeredis."""
        self._r = redis_client
        self._cache: dict[str, Key] = {}

    async def load(self) -> None:
        ids_bytes = await self._r.smembers("rtvoice:keys")
        ids = [b.decode() if isinstance(b, bytes) else b for b in ids_bytes]
        self._cache.clear()
        for kid in ids:
            data = await self._r.hgetall(f"rtvoice:key:{kid}")
            if not data:
                continue
            decoded = {(k.decode() if isinstance(k, bytes) else k):
                       (v.decode() if isinstance(v, bytes) else v)
                       for k, v in data.items()}
            try:
                key = self._decode_key(decoded)
                self._cache[key.id] = key
            except Exception as e:
                log.warning("skipping bad key %s: %s", kid, e)
        log.info("redis store loaded: %d keys", len(self._cache))

    async def put(self, key: Key) -> None:
        await self._r.hset(f"rtvoice:key:{key.id}", mapping=self._encode_key(key))
        await self._r.set(f"rtvoice:hash2id:{key.secret_hash}", key.id)
        await self._r.sadd("rtvoice:keys", key.id)
        self._cache[key.id] = key

    async def revoke(self, key_id: str) -> bool:
        if not await self._r.sismember("rtvoice:keys", key_id):
            return False
        ts = datetime.now(timezone.utc).isoformat()
        await self._r.hset(f"rtvoice:key:{key_id}", "revoked_at", ts)
        if key_id in self._cache:
            self._cache[key_id].revoked_at = datetime.fromisoformat(ts)
        return True

    def find_by_hash(self, secret_hash: str) -> Key | None:
        for k in self._cache.values():
            if k.secret_hash == secret_hash:
                return k
        return None

    def find_by_id(self, key_id: str) -> Key | None:
        return self._cache.get(key_id)

    def list_all(self) -> list[Key]:
        return list(self._cache.values())

    def any_keys(self) -> bool:
        return bool(self._cache)

    async def publish_change(self, key_id: str) -> None:
        """通知所有订阅者：某 key 变更（key_id 仅日志用，订阅者整盘 reload）."""
        await self._r.publish("rtvoice:keys:changed", key_id)

    @staticmethod
    def _encode_key(k: Key) -> dict[str, str]:
        d = k.model_dump(mode="json")
        out = {}
        for key_, val in d.items():
            if val is None:
                out[key_] = ""
            elif isinstance(val, (list, dict)):
                out[key_] = json.dumps(val, ensure_ascii=False)
            else:
                out[key_] = str(val)
        return out

    @staticmethod
    def _decode_key(d: dict[str, str]) -> Key:
        if d.get("scopes"):
            try:
                d["scopes"] = json.loads(d["scopes"])
            except Exception:
                pass
        if d.get("revoked_at") in (None, "", "None"):
            d["revoked_at"] = None
        for int_field in ("sessions_concurrent_max", "sessions_per_hour_max"):
            if int_field in d:
                d[int_field] = int(d[int_field])
        if "legacy" in d:
            d["legacy"] = d["legacy"] in ("True", "true", True, "1")
        return Key.model_validate(d)
