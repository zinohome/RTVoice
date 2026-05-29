"""Admin CLI commands implementing key lifecycle."""
from __future__ import annotations
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.models import Key

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return f"key_{secrets.token_urlsafe(12)}"


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


async def cmd_create(
    store: Any,
    *,
    name: str,
    sessions_concurrent: int,
    sessions_per_hour: int,
    scopes: list[str],
    notes: str = "",
) -> dict:
    """生成 plaintext secret + 注册 Key；返回含 secret 的 dict（仅此一次）."""
    secret = _new_secret()
    key_id = _new_id()
    h = hashlib.sha256(secret.encode()).hexdigest()
    k = Key(
        id=key_id,
        secret_hash=h,
        name=name,
        sessions_concurrent_max=sessions_concurrent,
        sessions_per_hour_max=sessions_per_hour,
        scopes=scopes,
        created_at=datetime.now(timezone.utc),
        notes=notes,
    )
    await store.put(k)
    await _maybe_publish_change(store, key_id)
    return {
        "id": key_id,
        "secret": secret,
        "name": name,
        "sessions_concurrent_max": sessions_concurrent,
        "sessions_per_hour_max": sessions_per_hour,
        "scopes": scopes,
    }


async def cmd_list(store: Any) -> list[dict]:
    """列表（不含 secret）."""
    rows = []
    for k in store.list_all():
        d = k.model_dump(mode="json")
        d.pop("secret_hash", None)
        rows.append(d)
    return rows


async def cmd_show(store: Any, *, key_id: str) -> dict | None:
    k = store.find_by_id(key_id)
    if k is None:
        return None
    d = k.model_dump(mode="json")
    d.pop("secret_hash", None)
    return d


async def cmd_revoke(store: Any, *, key_id: str) -> bool:
    ok = await store.revoke(key_id)
    if ok:
        await _maybe_publish_change(store, key_id)
    return ok


class KeyNotRevoked(Exception):
    """Raised when attempting to delete a key that is still active."""


async def cmd_delete(store: Any, *, key_id: str) -> bool:
    """删除单个已吊销 key。活跃 key 拒删（KeyNotRevoked）；不存在返回 False。"""
    k = store.find_by_id(key_id)
    if k is None:
        return False
    if k.revoked_at is None:
        raise KeyNotRevoked(key_id)
    ok = await store.delete(key_id)
    if ok:
        await _maybe_publish_change(store, key_id)
    return ok


async def cmd_purge_revoked(store: Any) -> list[str]:
    """删除所有已吊销 key，返回被删 id 列表。"""
    revoked_ids = [k.id for k in store.list_all() if k.revoked_at is not None]
    deleted: list[str] = []
    for kid in revoked_ids:
        if await store.delete(kid):
            deleted.append(kid)
    if deleted:
        await _maybe_publish_change(store, deleted[-1])
    return deleted


async def cmd_rotate(store: Any, *, key_id: str) -> dict:
    """重生成 secret；旧 hash 立即失效。"""
    k = store.find_by_id(key_id)
    if k is None:
        raise KeyError(f"key {key_id} not found")
    new_secret = _new_secret()
    k.secret_hash = hashlib.sha256(new_secret.encode()).hexdigest()
    await store.put(k)
    await _maybe_publish_change(store, key_id)
    return {"id": key_id, "secret": new_secret}


async def _maybe_publish_change(store, key_id: str) -> None:
    """Redis backend → PUBLISH；YAML 不需要（watchdog 自动监 file write）."""
    from rtvoice_auth.store_redis import RedisKeyStore
    if not isinstance(store, RedisKeyStore):
        return
    try:
        await store.publish_change(key_id)
    except Exception as e:
        logger.warning("publish_change failed for %s: %s", key_id, e)
