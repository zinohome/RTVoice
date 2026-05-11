"""Test YamlFileWatcher + _Debouncer."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_debouncer_fires_once_after_delay():
    from rtvoice_auth.watcher import _Debouncer
    calls = []
    async def cb():
        calls.append(1)
    d = _Debouncer(cb, delay_ms=50)
    d.fire()
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    await d.stop()


@pytest.mark.asyncio
async def test_debouncer_coalesces_rapid_fires():
    from rtvoice_auth.watcher import _Debouncer
    calls = []
    async def cb():
        calls.append(1)
    d = _Debouncer(cb, delay_ms=50)
    d.fire()
    await asyncio.sleep(0.01)
    d.fire()
    await asyncio.sleep(0.01)
    d.fire()
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    await d.stop()


@pytest.mark.asyncio
async def test_yaml_file_watcher_fires_on_modify(tmp_path):
    from rtvoice_auth.watcher import YamlFileWatcher
    p = tmp_path / "keys.yaml"
    p.write_text("version: 1\nkeys: []\n")
    fired = asyncio.Event()
    async def cb():
        fired.set()
    w = YamlFileWatcher(str(p), on_change=cb, debounce_ms=50)
    w.start()
    await asyncio.sleep(0.05)
    p.write_text("version: 1\nkeys:\n  - id: k1\n    secret_hash: h\n    name: n\n    created_at: 2026-01-01T00:00:00+00:00\n")
    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await w.stop()
    assert fired.is_set()
