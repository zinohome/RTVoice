"""Admin CLI commands implementing key lifecycle."""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.models import Key


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
    return await store.revoke(key_id)


async def cmd_rotate(store: Any, *, key_id: str) -> dict:
    """重生成 secret；旧 hash 立即失效。"""
    k = store.find_by_id(key_id)
    if k is None:
        raise KeyError(f"key {key_id} not found")
    new_secret = _new_secret()
    k.secret_hash = hashlib.sha256(new_secret.encode()).hexdigest()
    await store.put(k)
    return {"id": key_id, "secret": new_secret}
