"""Shared lifespan helper: auto-migrate RTVOICE_API_KEY → legacy-default key."""
from __future__ import annotations
import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.lifespan")


async def auto_migrate_legacy(store) -> Key | None:
    """空 store + RTVOICE_API_KEY 设 → 创建 legacy-default key 返回。

    幂等：store 已有 key 时 no-op。
    """
    if store.any_keys():
        return None
    legacy_secret = os.environ.get("RTVOICE_API_KEY", "").strip()
    if not legacy_secret:
        return None
    key_id = f"key_{secrets.token_urlsafe(12)}"
    k = Key(
        id=key_id,
        secret_hash=hashlib.sha256(legacy_secret.encode()).hexdigest(),
        name="legacy-default",
        sessions_concurrent_max=10,
        sessions_per_hour_max=1000,
        scopes=["stt", "tts", "realtime", "tokens"],
        created_at=datetime.now(timezone.utc),
        legacy=True,
        notes="auto-migrated from RTVOICE_API_KEY env",
    )
    await store.put(k)
    log.warning(
        "migrated RTVOICE_API_KEY → legacy-default key (id=%s); "
        "recommend `rtvoice-admin create --name <app>` per app, then revoke legacy",
        key_id,
    )
    return k
