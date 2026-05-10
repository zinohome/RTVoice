"""import-legacy CLI subcommand."""
from __future__ import annotations
import os

from rtvoice_auth.lifespan import auto_migrate_legacy


async def cmd_import_legacy(store) -> dict:
    """从 RTVOICE_API_KEY 导入 legacy-default key。"""
    legacy_env = os.environ.get("RTVOICE_API_KEY", "").strip()
    if not legacy_env:
        return {"status": "skipped", "reason": "RTVOICE_API_KEY not set"}
    if store.any_keys():
        return {"status": "skipped",
                "reason": "store already has keys; manual import via `create` if needed"}
    k = await auto_migrate_legacy(store)
    return {"status": "imported", "key_id": k.id, "name": k.name, "legacy": True}
