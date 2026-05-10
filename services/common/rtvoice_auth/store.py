"""KeyStore abstract base + YAML backend."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import yaml

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.store")


class KeyStore(Protocol):
    async def load(self) -> None: ...
    async def put(self, key: Key) -> None: ...
    async def revoke(self, key_id: str) -> bool: ...
    def find_by_hash(self, secret_hash: str) -> Key | None: ...
    def find_by_id(self, key_id: str) -> Key | None: ...
    def list_all(self) -> list[Key]: ...
    def any_keys(self) -> bool: ...


class YamlKeyStore:
    """YAML 文件后端；in-memory dict + atomic file write。"""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._by_hash: dict[str, Key] = {}
        self._by_id: dict[str, Key] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.error("yaml store load failed: %s", e)
            return
        keys_raw = data.get("keys", [])
        async with self._lock:
            self._by_hash.clear()
            self._by_id.clear()
            for kd in keys_raw:
                try:
                    k = Key.model_validate(kd)
                    self._by_hash[k.secret_hash] = k
                    self._by_id[k.id] = k
                except Exception as e:
                    log.warning("skipping invalid key entry: %s", e)
        log.info("yaml store loaded: %d keys from %s", len(self._by_id), self.path)

    async def put(self, key: Key) -> None:
        async with self._lock:
            self._by_hash[key.secret_hash] = key
            self._by_id[key.id] = key
            await self._flush()

    async def revoke(self, key_id: str) -> bool:
        async with self._lock:
            k = self._by_id.get(key_id)
            if k is None:
                return False
            k.revoked_at = datetime.now(timezone.utc)
            await self._flush()
            return True

    def find_by_hash(self, secret_hash: str) -> Key | None:
        return self._by_hash.get(secret_hash)

    def find_by_id(self, key_id: str) -> Key | None:
        return self._by_id.get(key_id)

    def list_all(self) -> list[Key]:
        return list(self._by_id.values())

    def any_keys(self) -> bool:
        return bool(self._by_id)

    async def _flush(self) -> None:
        """Atomic file write: tmp + rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "keys": [k.model_dump(mode="json") for k in self._by_id.values()],
        }
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                       encoding="utf-8")
        tmp.replace(self.path)
