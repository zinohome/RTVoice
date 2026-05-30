"""rtvoice-admin CLI 入口."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from typing import Any


def _make_store() -> Any:
    """根据 RTVOICE_KEYS_BACKEND 选 yaml/redis."""
    backend = os.environ.get("RTVOICE_KEYS_BACKEND", "yaml").lower()
    if backend == "redis":
        import redis.asyncio as redis_lib
        from rtvoice_auth.store_redis import RedisKeyStore
        url = os.environ.get("RTVOICE_REDIS_URL", "redis://localhost:6379/0")
        client = redis_lib.from_url(url)
        return RedisKeyStore(client)
    from rtvoice_auth.store import YamlKeyStore
    path = os.environ.get("RTVOICE_KEYS_FILE", "/data/keys.yaml")
    return YamlKeyStore(path)


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(empty)")
        return
    keys = ["id", "name", "sessions_concurrent_max", "sessions_per_hour_max",
            "scopes", "created_at", "revoked_at", "legacy"]
    print(" | ".join(keys))
    for r in rows:
        print(" | ".join(str(r.get(k, "")) for k in keys))


async def _run_async(args) -> int:
    from rtvoice_admin import commands as cmd_mod
    store = _make_store()
    await store.load()

    if args.cmd == "create":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        out = await cmd_mod.cmd_create(
            store,
            name=args.name,
            sessions_concurrent=args.sessions_concurrent,
            sessions_per_hour=args.sessions_per_hour,
            scopes=scopes,
            notes=args.notes,
        )
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("\n\u26a0\ufe0f  secret 仅显示这一次，请立即保存。")
        return 0

    if args.cmd == "list":
        rows = await cmd_mod.cmd_list(store)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            _print_table(rows)
        return 0

    if args.cmd == "show":
        d = await cmd_mod.cmd_show(store, key_id=args.key_id)
        if d is None:
            print(f"key {args.key_id} not found", file=sys.stderr)
            return 2
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "revoke":
        ok = await cmd_mod.cmd_revoke(store, key_id=args.key_id)
        if ok:
            print(f"revoked {args.key_id}")
            return 0
        print(f"key {args.key_id} not found", file=sys.stderr)
        return 2

    if args.cmd == "rotate":
        try:
            out = await cmd_mod.cmd_rotate(store, key_id=args.key_id)
        except KeyError:
            print(f"key {args.key_id} not found", file=sys.stderr)
            return 2
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("\n\u26a0\ufe0f  new secret 仅显示这一次，请立即保存。")
        return 0

    if args.cmd == "import-legacy":
        from rtvoice_admin.commands_legacy import cmd_import_legacy
        out = await cmd_import_legacy(store)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtvoice-admin",
                                description="RTVoice multi-tenant admin CLI")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("version", help="show version")

    pc = sub.add_parser("create", help="create a new API key")
    pc.add_argument("--name", required=True)
    pc.add_argument("--sessions-concurrent", type=int, default=5,
                    dest="sessions_concurrent")
    pc.add_argument("--sessions-per-hour", type=int, default=100,
                    dest="sessions_per_hour")
    pc.add_argument("--scopes", default="stt,tts,realtime,tokens",
                    help="comma-separated; default all")
    pc.add_argument("--notes", default="")

    pl = sub.add_parser("list", help="list all keys")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="show key detail")
    ps.add_argument("key_id")

    pv = sub.add_parser("revoke", help="revoke a key")
    pv.add_argument("key_id")

    pr = sub.add_parser("rotate", help="rotate (regenerate) secret")
    pr.add_argument("key_id")

    sub.add_parser("import-legacy",
                   help="import RTVOICE_API_KEY env as legacy-default")

    args = p.parse_args(argv)
    if args.cmd == "version":
        from rtvoice_admin import __version__
        print(__version__)
        return 0
    if args.cmd is None:
        p.print_help()
        return 1
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    sys.exit(main())
