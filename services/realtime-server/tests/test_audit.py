"""Test AuditWriter: async JSONL append, queue full drop, IO error swallow."""
import asyncio
import json
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_writes_jsonl_lines(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_abc", str(tmp_path), queue_max=100)
    await w.write({"event": "transcript.final", "text": "hi"})
    await w.write({"event": "response.done", "text": "hello"})
    await w.aclose()
    files = list(tmp_path.rglob("sess_abc.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["event"] == "transcript.final"
    assert "ts" in e1
    assert e1["text"] == "hi"


@pytest.mark.asyncio
async def test_queue_full_drops_event(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_qf", str(tmp_path), queue_max=2)
    await w.write({"event": "e1"})
    await w.write({"event": "e2"})
    for i in range(50):
        await w.write({"event": f"e{i}"})
    await w.aclose()
    files = list(tmp_path.rglob("sess_qf.jsonl"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_aclose_drains_pending(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_drain", str(tmp_path))
    for i in range(10):
        await w.write({"event": f"e{i}"})
    await w.aclose()
    files = list(tmp_path.rglob("sess_drain.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 10


@pytest.mark.asyncio
async def test_dir_unwritable_swallows_error(tmp_path):
    from app.audit import AuditWriter
    f = tmp_path / "blocked"
    f.write_text("x")
    w = AuditWriter("sess_e", str(f / "sub"), queue_max=10)
    await w.write({"event": "x"})
    await w.aclose()


@pytest.mark.asyncio
async def test_path_uses_session_creation_date(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_d", str(tmp_path))
    p = w.path
    assert p.parent.parent == tmp_path
    assert len(p.parent.name) == 10
    assert p.name == "sess_d.jsonl"
    await w.aclose()
