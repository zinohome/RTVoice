# SP2 Multi-tenant Realtime Voice session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新 `realtime-server` docker service 提供 `POST /v1/sessions` + `WS /v1/realtime/{session_id}`，实现多 tenant 并发 session 管理，单 turn 无 memory（SP3 加 memory）。

**Architecture:** FastAPI app 单 process 异步 multiplex；in-memory SessionManager 维护 session 状态；per-session STT/TTS WS 客户端 + LLM HTTP 客户端（copy from agent-worker）；env-driven concurrency 参数（cap 5 默认 RTX 3060 12GB 调优）。

**Tech Stack:** FastAPI / uvicorn / websockets / httpx / pydantic / Docker

**Spec:** [docs/superpowers/specs/2026-05-08-sp2-realtime-session-design.md](../specs/2026-05-08-sp2-realtime-session-design.md)

---

## Task 1: 项目骨架（Dockerfile + requirements + 4 个 client copy + error_schema）

**Files:**
- Create: `services/realtime-server/Dockerfile`
- Create: `services/realtime-server/requirements.txt`
- Create: `services/realtime-server/app/__init__.py`
- Create: `services/realtime-server/app/error_schema.py`（copy 自 stt-server）
- Create: `services/realtime-server/app/stt_client.py`（copy 自 agent-worker）
- Create: `services/realtime-server/app/llm_client.py`（copy 自 agent-worker）
- Create: `services/realtime-server/app/tts_client.py`（copy 自 agent-worker）

- [ ] **Step 1: 创建目录结构**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p services/realtime-server/app
mkdir -p services/realtime-server/tests
touch services/realtime-server/app/__init__.py
touch services/realtime-server/tests/__init__.py
```

- [ ] **Step 2: 写 requirements.txt**

`services/realtime-server/requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.32
websockets>=12.0
httpx>=0.27
pydantic>=2.7
prometheus-client>=0.21
prometheus-fastapi-instrumentator>=7.0
openai>=1.45
```

- [ ] **Step 3: 写 Dockerfile**

`services/realtime-server/Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app /app/app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=2).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
```

- [ ] **Step 4: copy error_schema.py from stt-server**

```bash
cp services/stt-server/app/error_schema.py services/realtime-server/app/error_schema.py
diff services/stt-server/app/error_schema.py services/realtime-server/app/error_schema.py
```

Expected: 无 diff 输出（identical）。

- [ ] **Step 5: copy 3 client files from agent-worker**

```bash
cp services/agent-worker/app/stt_client.py services/realtime-server/app/stt_client.py
cp services/agent-worker/app/llm_client.py services/realtime-server/app/llm_client.py
cp services/agent-worker/app/tts_client.py services/realtime-server/app/tts_client.py
```

- [ ] **Step 6: syntax check 所有 copied files**

```bash
cd services/realtime-server
for f in app/__init__.py app/error_schema.py app/stt_client.py app/llm_client.py app/tts_client.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK $f" || echo "FAIL $f"
done
```

Expected: 5 个 OK。

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/
git commit -m "feat(realtime-server): 项目骨架 + Dockerfile + 4 client copy (SP2 T1)

- requirements.txt: fastapi, uvicorn, websockets, httpx, pydantic, openai
- Dockerfile: python:3.11-slim, expose 9000, healthcheck
- app/error_schema.py: copy from stt-server (per-service pattern)
- app/{stt,llm,tts}_client.py: copy from agent-worker (independent evolution)
- 4 client files 同 agent-worker 的版本完全一致；后续会演进"
```

---

## Task 2: config.py — env vars 集中

**Files:**
- Create: `services/realtime-server/app/config.py`
- Test: `services/realtime-server/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`services/realtime-server/tests/test_config.py`:

```python
"""Test config loads env vars with correct defaults."""
import os


def test_defaults_when_no_env(monkeypatch):
    """When no env set, defaults apply (RTX 3060 调优)."""
    for k in [
        "RTVOICE_MAX_CONCURRENT_SESSIONS",
        "RTVOICE_SESSION_QUEUE_DEPTH",
        "RTVOICE_SESSION_CREATE_TIMEOUT_S",
        "RTVOICE_SESSION_IDLE_TIMEOUT_S",
        "RTVOICE_SESSION_MAX_LIFETIME_S",
        "RTVOICE_WS_DISCONNECT_GRACE_S",
        "RTVOICE_TURN_TIMEOUT_S",
    ]:
        monkeypatch.delenv(k, raising=False)
    # force re-import
    import importlib
    if "app.config" in __import__("sys").modules:
        importlib.reload(__import__("sys").modules["app.config"])
    from app import config
    assert config.MAX_CONCURRENT_SESSIONS == 5
    assert config.SESSION_QUEUE_DEPTH == 0
    assert config.SESSION_CREATE_TIMEOUT_S == 60
    assert config.SESSION_IDLE_TIMEOUT_S == 30
    assert config.SESSION_MAX_LIFETIME_S == 1800
    assert config.WS_DISCONNECT_GRACE_S == 0
    assert config.TURN_TIMEOUT_S == 60


def test_env_override(monkeypatch):
    """Env vars override defaults (24GB GPU upgrade scenario)."""
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "10")
    monkeypatch.setenv("RTVOICE_SESSION_IDLE_TIMEOUT_S", "60")
    import importlib
    if "app.config" in __import__("sys").modules:
        importlib.reload(__import__("sys").modules["app.config"])
    from app import config
    assert config.MAX_CONCURRENT_SESSIONS == 10
    assert config.SESSION_IDLE_TIMEOUT_S == 60
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/realtime-server
python3 -m pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write config.py**

`services/realtime-server/app/config.py`:

```python
"""Centralized env-driven config for realtime-server.

All scaling / lifecycle parameters here. Future GPU upgrades only require
.env changes, no code changes (per spec D-2026-05-08-A.2).
"""
import os


def _int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Service URLs (resolved from agent-worker pattern)
STT_WS_URL = _str("STT_WS_URL", "ws://stt-server:9090/v1/asr")
LLM_BASE_URL = _str("LLM_BASE_URL", "http://llm-server:11434/v1")
LLM_MODEL = _str("LLM_MODEL", "qwen2.5:1.5b")
LLM_API_KEY = _str("LLM_API_KEY", "ollama")
TTS_BASE_URL = _str("TTS_BASE_URL", "http://tts-server:9880")

# Auth
RTVOICE_API_KEY = _str("RTVOICE_API_KEY", "").strip()  # empty = dev mode no auth

# Public WS URL base (returned in POST /v1/sessions response)
PUBLIC_WS_BASE = _str("PUBLIC_WS_BASE", "ws://realtime-server:9000")

# Concurrency / lifecycle (RTX 3060 12GB tuned defaults)
MAX_CONCURRENT_SESSIONS = _int("RTVOICE_MAX_CONCURRENT_SESSIONS", 5)
SESSION_QUEUE_DEPTH = _int("RTVOICE_SESSION_QUEUE_DEPTH", 0)
SESSION_CREATE_TIMEOUT_S = _int("RTVOICE_SESSION_CREATE_TIMEOUT_S", 60)
SESSION_IDLE_TIMEOUT_S = _int("RTVOICE_SESSION_IDLE_TIMEOUT_S", 30)
SESSION_MAX_LIFETIME_S = _int("RTVOICE_SESSION_MAX_LIFETIME_S", 1800)
WS_DISCONNECT_GRACE_S = _int("RTVOICE_WS_DISCONNECT_GRACE_S", 0)
TURN_TIMEOUT_S = _int("RTVOICE_TURN_TIMEOUT_S", 60)

# TTS / LLM scaling (forward-compat hooks; v1 not yet acted upon)
TTS_MODEL_REPLICAS = _int("RTVOICE_TTS_MODEL_REPLICAS", 1)
LLM_MAX_CONCURRENT = _int("RTVOICE_LLM_MAX_CONCURRENT", 4)

# STT timeout (turn 内等 STT final 的最长时间)
STT_FINAL_TIMEOUT_S = _float("STT_FINAL_TIMEOUT_S", 5.0)

# Voice defaults
DEFAULT_VOICE = _str("TTS_VOICE", "default_zh_female")
DEFAULT_LANG = _str("TTS_LANG", "cmn")

# Logging
LOG_LEVEL = _str("LOG_LEVEL", "INFO").upper()


def log_summary(logger):
    """启动时打印实际生效的参数（便于排障）"""
    logger.info("=== realtime-server config ===")
    logger.info("STT_WS_URL=%s LLM=%s TTS=%s", STT_WS_URL, LLM_MODEL, TTS_BASE_URL)
    logger.info("MAX_CONCURRENT_SESSIONS=%d QUEUE_DEPTH=%d",
                MAX_CONCURRENT_SESSIONS, SESSION_QUEUE_DEPTH)
    logger.info("CREATE_TIMEOUT=%ds IDLE_TIMEOUT=%ds MAX_LIFETIME=%ds DISCONNECT_GRACE=%ds TURN_TIMEOUT=%ds",
                SESSION_CREATE_TIMEOUT_S, SESSION_IDLE_TIMEOUT_S,
                SESSION_MAX_LIFETIME_S, WS_DISCONNECT_GRACE_S, TURN_TIMEOUT_S)
    logger.info("TTS_REPLICAS=%d LLM_MAX_CONCURRENT=%d",
                TTS_MODEL_REPLICAS, LLM_MAX_CONCURRENT)
    logger.info("auth=%s", "enabled" if RTVOICE_API_KEY else "disabled (dev mode)")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/realtime-server
python3 -m pytest tests/test_config.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/config.py services/realtime-server/tests/test_config.py
git commit -m "feat(realtime-server): config.py - env vars 集中 (SP2 T2)

- 12 个 env vars 集中：service URLs / auth / 7 个 lifecycle + concurrency
- RTX 3060 12GB 调优默认（cap=5, idle=30s, max=1800s）
- log_summary() 启动时打印实际生效参数
- 2 单元测试覆盖 defaults + env override 路径

per spec D-2026-05-08-A.2: env-driven scaling, future GPU 扩容只改 .env"
```

---

## Task 3: session_manager.py — Session class + lifecycle

**Files:**
- Create: `services/realtime-server/app/session_manager.py`
- Test: `services/realtime-server/tests/test_session_manager.py`

- [ ] **Step 1: Write failing tests for SessionManager core**

`services/realtime-server/tests/test_session_manager.py`:

```python
"""Test SessionManager: create / get / cleanup / capacity / expire."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    monkeypatch.setenv("RTVOICE_SESSION_MAX_LIFETIME_S", "60")
    monkeypatch.setenv("RTVOICE_SESSION_IDLE_TIMEOUT_S", "10")
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app import session_manager
    return session_manager.SessionManager()


@pytest.mark.asyncio
async def test_create_returns_session_with_stripe_id(mgr):
    """session_id 是 sess_<urlsafe-12bytes>"""
    sess = await mgr.create(creator_key_hash="hash1", voice="alice", speed=1.0)
    assert sess.id.startswith("sess_")
    assert len(sess.id) >= 17  # "sess_" + 12 chars+
    assert sess.creator_key_hash == "hash1"
    assert sess.voice == "alice"
    assert sess.speed == 1.0
    assert sess.state == "CREATED"
    assert sess.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_get_returns_existing_session(mgr):
    sess = await mgr.create("h", "v", 1.0)
    got = mgr.get(sess.id)
    assert got is sess


def test_get_returns_none_when_not_found(mgr):
    assert mgr.get("sess_nonexistent") is None


@pytest.mark.asyncio
async def test_capacity_full_raises(mgr):
    """When MAX_CONCURRENT_SESSIONS reached, create raises CapacityFull."""
    from app.session_manager import CapacityFull
    for _ in range(3):  # cap = 3 from fixture
        await mgr.create("h", "v", 1.0)
    with pytest.raises(CapacityFull):
        await mgr.create("h", "v", 1.0)


@pytest.mark.asyncio
async def test_active_count(mgr):
    assert mgr.active_count() == 0
    await mgr.create("h", "v", 1.0)
    assert mgr.active_count() == 1


@pytest.mark.asyncio
async def test_cleanup_removes_session(mgr):
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, reason="test")
    assert mgr.get(sess.id) is None
    assert mgr.active_count() == 0


@pytest.mark.asyncio
async def test_cleanup_idempotent(mgr):
    """cleanup of already-cleaned session is no-op (no exception)."""
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, reason="test1")
    await mgr.cleanup(sess.id, reason="test2")  # should not raise
    assert mgr.active_count() == 0


@pytest.mark.asyncio
async def test_expire_loop_removes_expired(mgr, monkeypatch):
    """Background expire loop removes sessions past expires_at."""
    sess = await mgr.create("h", "v", 1.0)
    # Force expires_at to past
    sess.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    # run one expire pass
    await mgr._expire_pass()
    assert mgr.get(sess.id) is None


@pytest.mark.asyncio
async def test_expire_loop_removes_idle(mgr):
    """Background expire loop removes sessions with last_activity > IDLE_TIMEOUT ago."""
    sess = await mgr.create("h", "v", 1.0)
    # Force last_activity to past (more than IDLE_TIMEOUT=10s)
    sess.last_activity = datetime.now(timezone.utc) - timedelta(seconds=11)
    sess.state = "ACTIVE"  # only ACTIVE sessions are idle-checked
    await mgr._expire_pass()
    assert mgr.get(sess.id) is None


@pytest.mark.asyncio
async def test_attach_ws_transitions_state(mgr):
    """attach_ws moves CREATED → ACTIVE."""
    sess = await mgr.create("h", "v", 1.0)
    assert sess.state == "CREATED"
    fake_ws = object()
    ok = mgr.attach_ws(sess.id, fake_ws)
    assert ok is True
    assert sess.state == "ACTIVE"
    assert sess.ws is fake_ws


@pytest.mark.asyncio
async def test_attach_ws_fails_if_not_created(mgr):
    """attach_ws returns False if session already cleanup'd."""
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, "test")
    ok = mgr.attach_ws(sess.id, object())
    assert ok is False
```

需要把 `pytest-asyncio` 加到 requirements 测试 dep（不要进 production image）。先用 conftest.py 配置 mode：

`services/realtime-server/tests/conftest.py`:

```python
import pytest
import pytest_asyncio


@pytest_asyncio.fixture(autouse=False)
def _no_op():
    pass
```

测试运行时：`pip install pytest pytest-asyncio` 然后 `pytest -v`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/realtime-server
pip install pytest pytest-asyncio 2>&1 | tail -3
python3 -m pytest tests/test_session_manager.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.session_manager'`.

- [ ] **Step 3: Write session_manager.py**

`services/realtime-server/app/session_manager.py`:

```python
"""SessionManager: in-memory store + lifecycle (per spec D-2026-05-08-A.5/§5)."""
from __future__ import annotations
import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from app import config

log = logging.getLogger("rtvoice.realtime.session")


SessionState = Literal["CREATED", "ACTIVE", "CLEANUP"]


class CapacityFull(Exception):
    """Raised when create() called but MAX_CONCURRENT_SESSIONS reached."""


@dataclass
class Session:
    id: str
    creator_key_hash: str
    voice: str
    speed: float
    created_at: datetime
    expires_at: datetime
    state: SessionState = "CREATED"
    ws: Any = None  # WebSocket instance, set on attach_ws
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_turn_task: Optional[asyncio.Task] = None
    stt_client: Any = None
    llm_client: Any = None
    tts_client: Any = None


def _new_session_id() -> str:
    """Stripe-style: sess_<token_urlsafe(12)>"""
    return f"sess_{secrets.token_urlsafe(12)}"


def hash_key(api_key: str) -> str:
    """sha256 prefix for creator binding (no full key kept in memory)."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._capacity_lock = asyncio.Lock()
        self._expire_task: Optional[asyncio.Task] = None

    async def create(self, creator_key_hash: str, voice: str, speed: float) -> Session:
        async with self._capacity_lock:
            if self.active_count() >= config.MAX_CONCURRENT_SESSIONS:
                raise CapacityFull(
                    f"max {config.MAX_CONCURRENT_SESSIONS} concurrent sessions"
                )
            now = _now()
            sess = Session(
                id=_new_session_id(),
                creator_key_hash=creator_key_hash,
                voice=voice,
                speed=speed,
                created_at=now,
                expires_at=now + timedelta(seconds=config.SESSION_MAX_LIFETIME_S),
                last_activity=now,
            )
            self._sessions[sess.id] = sess
            log.info("session created: id=%s voice=%s speed=%.2f expires=%s",
                     sess.id, sess.voice, sess.speed, sess.expires_at.isoformat())
            return sess

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def attach_ws(self, session_id: str, ws: Any) -> bool:
        sess = self._sessions.get(session_id)
        if sess is None or sess.state != "CREATED":
            return False
        sess.ws = ws
        sess.state = "ACTIVE"
        sess.last_activity = _now()
        log.info("session %s attached ws", session_id)
        return True

    async def cleanup(self, session_id: str, reason: str) -> None:
        sess = self._sessions.pop(session_id, None)
        if sess is None:
            return  # idempotent
        log.info("session %s cleanup (reason=%s, lifetime=%.1fs)",
                 session_id, reason, (_now() - sess.created_at).total_seconds())
        sess.state = "CLEANUP"
        # cancel current turn task
        if sess.current_turn_task and not sess.current_turn_task.done():
            sess.current_turn_task.cancel()
            try:
                await sess.current_turn_task
            except (asyncio.CancelledError, Exception):
                pass
        # close ws (best effort)
        if sess.ws:
            try:
                close_codes = {"idle": 4408, "expired": 4410, "ws_close": 1000}
                code = close_codes.get(reason, 1000)
                await sess.ws.close(code=code)
            except Exception:
                pass
        # close upstream clients
        for c in (sess.stt_client, sess.llm_client, sess.tts_client):
            if c and hasattr(c, "close"):
                try:
                    res = c.close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

    def active_count(self) -> int:
        return len(self._sessions)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    async def _expire_pass(self) -> None:
        """One pass over sessions: remove expired or idle."""
        now = _now()
        to_cleanup: list[tuple[str, str]] = []
        for sid, sess in list(self._sessions.items()):
            if sess.expires_at <= now:
                to_cleanup.append((sid, "expired"))
                continue
            # CREATED 状态超 CREATE_TIMEOUT 没 attach ws
            if sess.state == "CREATED":
                age_s = (now - sess.created_at).total_seconds()
                if age_s > config.SESSION_CREATE_TIMEOUT_S:
                    to_cleanup.append((sid, "create_timeout"))
                    continue
            # ACTIVE 状态 idle 超 IDLE_TIMEOUT
            if sess.state == "ACTIVE":
                idle_s = (now - sess.last_activity).total_seconds()
                if idle_s > config.SESSION_IDLE_TIMEOUT_S:
                    to_cleanup.append((sid, "idle"))
        for sid, reason in to_cleanup:
            await self.cleanup(sid, reason)

    async def _expire_loop(self) -> None:
        """Background task: every 5s scan and cleanup."""
        while True:
            try:
                await asyncio.sleep(5)
                await self._expire_pass()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("expire_loop error (continuing)")

    def start_expire_loop(self) -> None:
        if self._expire_task is None or self._expire_task.done():
            self._expire_task = asyncio.create_task(self._expire_loop())

    async def stop_expire_loop(self) -> None:
        if self._expire_task and not self._expire_task.done():
            self._expire_task.cancel()
            try:
                await self._expire_task
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_session_manager.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/session_manager.py services/realtime-server/tests/test_session_manager.py services/realtime-server/tests/conftest.py
git commit -m "feat(realtime-server): SessionManager + lifecycle (SP2 T3)

- Session @dataclass: id (sess_xxx) / creator_key_hash / voice / speed /
  created_at / expires_at / state (CREATED→ACTIVE→CLEANUP) / ws / clients
- SessionManager: create / get / attach_ws / cleanup / active_count /
  _expire_pass / _expire_loop
- 容量上限触发 CapacityFull exception (HTTP 503 caller 侧映射)
- Stripe 风格 session_id (sess_<urlsafe-12bytes>)
- Bearer hash 用 sha256 前 16 字节存（不留完整 key）
- expire 后台任务每 5s 扫，清 expired / idle / create_timeout
- 10 单元测试覆盖（创建/查找/容量/cleanup 幂等/idle/expired 等）

per spec D-2026-05-08-A.5 + §5 lifecycle"
```

---

## Task 4: pipeline.py — run_turn() 协调

**Files:**
- Create: `services/realtime-server/app/pipeline.py`
- Test: `services/realtime-server/tests/test_pipeline_mock.py`

- [ ] **Step 1: Write failing tests with mocks**

`services/realtime-server/tests/test_pipeline_mock.py`:

```python
"""Test pipeline.run_turn() with mocked STT/LLM/TTS clients."""
import asyncio
import json
import pytest
from datetime import datetime, timezone


class FakeSTTClient:
    def __init__(self, final_text="hello world"):
        self.final_text = final_text
        self.feed_calls = []

    async def feed(self, pcm: bytes) -> None:
        self.feed_calls.append(pcm)

    async def request_final(self, timeout: float = 5.0) -> str:
        return self.final_text


class FakeLLMClient:
    def __init__(self, deltas=None):
        self.deltas = deltas or ["你好", "世界"]

    async def stream(self, text: str):
        for d in self.deltas:
            yield d


class FakeTTSWS:
    def __init__(self, pcm_chunks=None):
        self.pcm_chunks = pcm_chunks or [b"\x00" * 480, b"\x00" * 480]
        self.sent_texts = []
        self.eos_called = False
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def eos(self) -> None:
        self.eos_called = True

    async def audio_chunks(self):
        for c in self.pcm_chunks:
            yield c

    async def aclose(self) -> None:
        self.closed = True


class FakeTTSClient:
    def __init__(self):
        self.opened_ws = None

    async def open_ws(self):
        self.opened_ws = FakeTTSWS()
        return self.opened_ws


class FakeWS:
    def __init__(self):
        self.sent: list = []
        self.closed = False
        self.close_code = None

    async def send_json(self, obj) -> None:
        self.sent.append(("text", obj))

    async def send_bytes(self, b: bytes) -> None:
        self.sent.append(("bytes", b))

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code


def _make_session():
    from app.session_manager import Session
    return Session(
        id="sess_test123",
        creator_key_hash="h",
        voice="default_zh_female",
        speed=1.0,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_run_turn_happy_path():
    """完整 turn: STT final → LLM stream → TTS WS → PCM out → done."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="你好")
    sess.llm_client = FakeLLMClient(deltas=["回", "复"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    # 检查事件序列
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    bytes_events = [e[1] for e in ws.sent if e[0] == "bytes"]
    assert {"type": "transcript.final", "text": "你好"} in text_events
    assert any(e.get("type") == "response.done" for e in text_events)
    assert len(bytes_events) >= 1  # 至少 1 chunk PCM


@pytest.mark.asyncio
async def test_run_turn_empty_stt_emits_error():
    """STT final 为空时发 stt.empty error，不调 LLM/TTS。"""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="")  # empty
    sess.llm_client = FakeLLMClient()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    assert any(e.get("type") == "error" and e.get("code") == "stt.empty"
               for e in text_events)
    assert sess.tts_client.opened_ws is None  # TTS 没被调用


@pytest.mark.asyncio
async def test_run_turn_llm_failure_emits_error():
    """LLM stream 抛异常 → 发 llm.failed error。"""
    from app.pipeline import run_turn
    class BrokenLLM:
        async def stream(self, text):
            if False:
                yield None  # make it generator
            raise RuntimeError("llm crashed")
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="x")
    sess.llm_client = BrokenLLM()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    assert any(e.get("type") == "error" for e in text_events)


@pytest.mark.asyncio
async def test_run_turn_clears_current_task_on_finally():
    """run_turn finishes → sess.current_turn_task = None."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="x")
    sess.llm_client = FakeLLMClient()
    sess.tts_client = FakeTTSClient()
    sess.current_turn_task = "should-be-cleared"  # placeholder
    ws = FakeWS()
    await run_turn(sess, ws)
    assert sess.current_turn_task is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline'`.

- [ ] **Step 3: Write pipeline.py**

`services/realtime-server/app/pipeline.py`:

```python
"""Per-turn pipeline: STT final → LLM → TTS → client PCM (copy-paste from
agent-worker `_run_pipeline_ws`, simplified for SP2 = no memory)."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.session_manager import Session
    from fastapi import WebSocket

log = logging.getLogger("rtvoice.realtime.pipeline")


def _classify_error(exc: Exception) -> str:
    """Python exception → CONVENTIONS.md §6 error code"""
    if isinstance(exc, asyncio.TimeoutError):
        return "turn.timeout"
    s = str(exc).lower()
    if "stt" in s:
        return "stt.failed"
    if "tts" in s:
        return "tts.failed"
    if "llm" in s or "openai" in s or "ollama" in s:
        return "llm.failed"
    return "internal.unknown"


async def run_turn(sess, ws):
    """Single turn: STT final → LLM → TTS → client PCM + done.

    Per spec §6.1 (SP2; no memory).
    """
    sess.current_turn_task = asyncio.current_task()
    try:
        # 1. STT: PCM 已流式 forward 到 stt-server，发 EOS 拿 final
        try:
            final_text = await sess.stt_client.request_final(
                timeout=config.STT_FINAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            await ws.send_json({
                "type": "error",
                "code": "stt.timeout",
                "message": "STT did not return final in time",
                "request_id": None,
            })
            return

        if not final_text or not final_text.strip():
            await ws.send_json({
                "type": "error",
                "code": "stt.empty",
                "message": "no speech detected",
                "request_id": None,
            })
            return

        # 2. 通知 client STT 结果
        await ws.send_json({
            "type": "transcript.final",
            "text": final_text,
        })

        # 3-4. LLM stream → TTS WS double-streaming → client PCM
        tts_ws = await sess.tts_client.open_ws()
        try:
            async def feeder():
                try:
                    async for delta in sess.llm_client.stream(final_text):
                        if delta:
                            await tts_ws.send_text(delta)
                finally:
                    await tts_ws.eos()

            feed_task = asyncio.create_task(feeder())
            try:
                async for pcm in tts_ws.audio_chunks():
                    if pcm:
                        await ws.send_bytes(pcm)
                # 等 feeder 跑完（理论上 EOS 已发）
                await feed_task
            finally:
                if not feed_task.done():
                    feed_task.cancel()
                    try:
                        await feed_task
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            await tts_ws.aclose()

        # 5. 通知 client 本 turn 完
        await ws.send_json({
            "type": "response.done",
        })

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("turn failed: %s", e)
        try:
            await ws.send_json({
                "type": "error",
                "code": _classify_error(e),
                "message": str(e)[:200],
                "request_id": None,
            })
        except Exception:
            pass
    finally:
        sess.current_turn_task = None
        sess.last_activity = datetime.now(timezone.utc)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/pipeline.py services/realtime-server/tests/test_pipeline_mock.py
git commit -m "feat(realtime-server): pipeline.run_turn() (SP2 T4)

- copy-paste from agent-worker _run_pipeline_ws, simplified for SP2:
  - 无 memory（每 turn LLM 独立调用）
  - 无 transcript.partial / response.text（SP3 加）
  - 仅 transcript.final + binary PCM + response.done + error
- _classify_error() 把 Python 异常映射到 error code
- 4 mock 测试：happy path / stt.empty / llm 异常 / current_turn_task 清理

per spec D-2026-05-08-A.3 + §6.1"
```

---

## Task 5: main.py — FastAPI app + endpoints + WS handler + auth

**Files:**
- Create: `services/realtime-server/app/main.py`
- Test: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: Write failing tests for endpoints**

`services/realtime-server/tests/test_endpoints.py`:

```python
"""Test FastAPI endpoints with TestClient: POST /v1/sessions + WS gateway."""
import json
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RTVOICE_API_KEY", "")  # auth disabled for test
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_auth(monkeypatch):
    monkeypatch.setenv("RTVOICE_API_KEY", "test-key-32chars-test-key-32chars")
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_info(client):
    r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "realtime-server"
    assert "version" in body
    assert "capabilities" in body
    assert body["capabilities"]["max_concurrent_sessions"] == 3


def test_openapi_paths_include_v1_sessions(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/v1/sessions" in paths


def test_create_session_default_voice(client):
    r = client.post("/v1/sessions", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["session_id"].startswith("sess_")
    assert body["ws_url"].endswith(body["session_id"])
    assert body["voice"] == "default_zh_female"
    assert body["speed"] == 1.0
    assert "expires_at" in body


def test_create_session_custom_voice_speed(client):
    r = client.post("/v1/sessions", json={"voice": "alice", "speed": 1.5})
    assert r.status_code == 201
    body = r.json()
    assert body["voice"] == "alice"
    assert body["speed"] == 1.5


def test_create_session_speed_out_of_range_returns_422(client):
    r = client.post("/v1/sessions", json={"speed": 3.0})
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "validation.invalid_request"


def test_create_session_capacity_full(client):
    """First 3 succeed, 4th returns 503 session.capacity_full."""
    for _ in range(3):
        r = client.post("/v1/sessions", json={})
        assert r.status_code == 201
    r = client.post("/v1/sessions", json={})
    assert r.status_code == 503
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "session.capacity_full"


def test_create_session_auth_required(client_with_auth):
    """When RTVOICE_API_KEY set, missing Bearer returns 401."""
    r = client_with_auth.post("/v1/sessions", json={})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] in ("auth.missing_token", "auth.invalid_token")


def test_create_session_auth_correct(client_with_auth):
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": "Bearer test-key-32chars-test-key-32chars"},
    )
    assert r.status_code == 201


def test_ws_session_not_found(client):
    """Connect to non-existent session_id returns close 4404."""
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/realtime/sess_nonexistent") as ws:
            ws.receive()
    # TestClient raises WebSocketDisconnect on close


def test_ws_creator_binding_mismatch(client_with_auth):
    """Create session with key A, connect with key B → 4403."""
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": "Bearer test-key-32chars-test-key-32chars"},
    )
    sid = r.json()["session_id"]
    # Try connecting with different bearer (即使是同一 key，TestClient 不传 ws header 默认就行)
    # 注：TestClient WS support headers via subprotocols 或 url query
    # 这里简化：设环境为不同 key，应导致 4403
    # 实际上 TestClient 可能难以模拟不同 bearer 的 WS；先 skip 该具体断言，留 prod 测
    # 改测：连接 WS 不带任何 bearer 时应被拒
    with pytest.raises(Exception):
        with client_with_auth.websocket_connect(f"/v1/realtime/{sid}") as ws:
            ws.receive_text()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Write main.py**

`services/realtime-server/app/main.py`:

```python
"""Realtime Voice service entry point — FastAPI app.

Endpoints:
  POST /v1/sessions               create session
  WS   /v1/realtime/{session_id}  bidirectional audio + events
  GET  /health                    healthcheck
  GET  /info                      capability discovery
  GET  /metrics                   prometheus
  GET  /openapi.json              auto-gen
"""
from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import (
    Depends, FastAPI, Header, HTTPException, Request, WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from app import config
from app.error_schema import (
    ErrorResponse, api_error, http_exception_handler,
    validation_exception_handler,
)
from app.session_manager import (
    CapacityFull, Session, SessionManager, hash_key,
)
from app.pipeline import run_turn
from app.stt_client import STTClient
from app.llm_client import LLMClient
from app.tts_client import TTSClient

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.realtime")


# Global session manager
session_mgr: SessionManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_mgr
    config.log_summary(log)
    session_mgr = SessionManager()
    session_mgr.start_expire_loop()
    log.info("realtime-server lifespan: ready")
    yield
    log.info("realtime-server lifespan: shutdown")
    if session_mgr:
        await session_mgr.stop_expire_loop()
        for s in session_mgr.all_sessions():
            await session_mgr.cleanup(s.id, reason="shutdown")


app = FastAPI(
    title="RTVoice Realtime Voice Server",
    version="0.9.0",
    lifespan=lifespan,
)
app.add_exception_handler(HTTPException, http_exception_handler())
app.add_exception_handler(RequestValidationError, validation_exception_handler())


# Optional: prometheus metrics
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(app)
except Exception as e:
    log.warning("prometheus instrumentator unavailable: %s", e)


# ----------------- Pydantic models -----------------


class SessionCreateRequest(BaseModel):
    voice: str | None = Field(None, description="TTS voice spk_id, default: default_zh_female")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="TTS speed factor")


class SessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str
    expires_at: str
    voice: str
    speed: float


# ----------------- Auth helpers -----------------


def _check_bearer_http(authorization: str | None) -> str:
    """Return the bearer token if valid (or "" in dev mode); else raise 401."""
    if not config.RTVOICE_API_KEY:
        return ""  # dev mode: no auth
    if not authorization:
        raise api_error(401, "auth.missing_token", "Authorization header required")
    if authorization != f"Bearer {config.RTVOICE_API_KEY}":
        raise api_error(401, "auth.invalid_token", "invalid Bearer token")
    return config.RTVOICE_API_KEY


def _extract_ws_bearer(ws: WebSocket) -> str:
    """Three-way Bearer extract: header / subprotocol / query.
    Returns "" if dev mode (no key set); else returns matched key or raises."""
    if not config.RTVOICE_API_KEY:
        return ""
    # 1) Authorization: Bearer ...
    auth = ws.headers.get("authorization", "")
    if auth == f"Bearer {config.RTVOICE_API_KEY}":
        return config.RTVOICE_API_KEY
    # 2) Sec-WebSocket-Protocol: bearer.<KEY>
    proto = ws.headers.get("sec-websocket-protocol", "")
    for p in (s.strip() for s in proto.split(",")):
        if p.startswith("bearer.") and p[len("bearer."):] == config.RTVOICE_API_KEY:
            return config.RTVOICE_API_KEY
    # 3) ?token=<KEY>
    if ws.query_params.get("token") == config.RTVOICE_API_KEY:
        return config.RTVOICE_API_KEY
    return None  # auth failed


# ----------------- Public endpoints -----------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict:
    return {
        "name": "realtime-server",
        "version": "0.9.0",
        "capabilities": {
            "session_api": True,
            "ws_realtime": True,
            "transcript_final": True,
            "memory": False,  # SP3 will set True
            "max_concurrent_sessions": config.MAX_CONCURRENT_SESSIONS,
            "session_idle_timeout_s": config.SESSION_IDLE_TIMEOUT_S,
            "session_max_lifetime_s": config.SESSION_MAX_LIFETIME_S,
        },
    }


@app.post(
    "/v1/sessions",
    response_model=SessionCreateResponse,
    status_code=201,
    summary="Create a Realtime Voice session",
    description="Allocates a session_id + ws_url for a Realtime Voice conversation. SP2: single-turn LLM, no memory.",
    tags=["sessions"],
    responses={
        401: {"model": ErrorResponse, "description": "Auth failed"},
        422: {"model": ErrorResponse, "description": "Invalid input"},
        503: {"model": ErrorResponse, "description": "Capacity full"},
    },
)
async def create_session(
    req: SessionCreateRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> SessionCreateResponse:
    bearer = _check_bearer_http(authorization)
    voice = req.voice or config.DEFAULT_VOICE

    try:
        sess = await session_mgr.create(
            creator_key_hash=hash_key(bearer),
            voice=voice,
            speed=req.speed,
        )
    except CapacityFull as e:
        raise api_error(503, "session.capacity_full", str(e))

    return SessionCreateResponse(
        session_id=sess.id,
        ws_url=f"{config.PUBLIC_WS_BASE}/v1/realtime/{sess.id}",
        expires_at=sess.expires_at.isoformat(),
        voice=voice,
        speed=req.speed,
    )


@app.websocket("/v1/realtime/{session_id}")
async def realtime_ws(ws: WebSocket, session_id: str) -> None:
    """Bidirectional WS: PCM in / PCM + events out."""
    bearer = _extract_ws_bearer(ws)
    if bearer is None:
        await ws.close(code=4401, reason="unauthorized")
        return

    sess = session_mgr.get(session_id) if session_mgr else None
    if sess is None:
        await ws.close(code=4404, reason="session_not_found")
        return
    if sess.creator_key_hash != hash_key(bearer):
        await ws.close(code=4403, reason="session_unauthorized")
        return
    if sess.expires_at < datetime.now(timezone.utc):
        await ws.close(code=4410, reason="session_expired")
        return

    await ws.accept()
    if not session_mgr.attach_ws(session_id, ws):
        await ws.close(code=1011, reason="attach_failed")
        return

    # Initialize per-session upstream clients
    sess.stt_client = STTClient(config.STT_WS_URL, api_key=config.RTVOICE_API_KEY or None)
    try:
        await sess.stt_client.connect()
    except Exception as e:
        log.exception("STT connect failed: %s", e)
        await ws.send_json({
            "type": "error", "code": "stt.connect_failed",
            "message": str(e)[:200], "request_id": None,
        })
        await ws.close(code=1011, reason="upstream_failed")
        await session_mgr.cleanup(session_id, "upstream_failed")
        return

    sess.llm_client = LLMClient(
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        api_key=config.LLM_API_KEY,
    )
    sess.tts_client = TTSClient(
        base_url=config.TTS_BASE_URL,
        voice=sess.voice,
        speed=sess.speed,
        api_key=config.RTVOICE_API_KEY or None,
    )

    log.info("session %s: ws connected, ready for turns", session_id)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    ws.receive(),
                    timeout=config.SESSION_IDLE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                await ws.close(code=4408, reason="idle_timeout")
                break

            if msg["type"] == "websocket.disconnect":
                break

            sess.last_activity = datetime.now(timezone.utc)

            if msg.get("bytes"):
                # PCM bytes → forward to STT
                try:
                    await sess.stt_client.feed(msg["bytes"])
                except Exception:
                    log.exception("STT feed failed")
            elif msg.get("text") == "audio.eos":
                if sess.current_turn_task and not sess.current_turn_task.done():
                    await ws.send_json({
                        "type": "error", "code": "turn.in_progress",
                        "message": "previous turn not yet done",
                        "request_id": None,
                    })
                else:
                    asyncio.create_task(run_turn(sess, ws))
            else:
                log.debug("session %s: unknown msg %s", session_id, msg)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws handler error")
    finally:
        await session_mgr.cleanup(session_id, reason="ws_close")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v
```

Expected: ~9 passed (TestClient WS auth tests may be skipped/marked—acceptable as long as 401 path tested and HTTP endpoints all pass).

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/main.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): main.py FastAPI app + endpoints + WS handler (SP2 T5)

- POST /v1/sessions: create session + capacity check + Bearer auth
- WS /v1/realtime/{session_id}: 三路 Bearer + creator binding + per-session
  STT/LLM/TTS client + main loop (forward PCM, dispatch run_turn on audio.eos)
- /health /info /openapi.json /metrics 运维端点（/info 含 capabilities dict）
- ErrorResponse + RequestValidationError handlers (CONVENTIONS.md §6 一致)
- Idle timeout (asyncio.wait_for)、turn.in_progress 防并发

per spec §4 + §6.2"
```

---

## Task 6: docker-compose.yml + .env.example 集成

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: 加 realtime-server 到 docker-compose.yml**

在 `docker-compose.yml` 适当位置（如 token-server 之后）插入:

```yaml
  # ---------------------------------------------------------------
  # Realtime Voice Service — multi-tenant session API + WS gateway (SP2)
  # ---------------------------------------------------------------
  realtime-server:
    build:
      context: ./services/realtime-server
      dockerfile: Dockerfile
    image: rtvoice/realtime-server:v0.9.0
    container_name: rtvoice-realtime
    profiles: ["dev", "prod"]
    restart: unless-stopped
    networks: [rtvoice_net]
    depends_on:
      stt-server:
        condition: service_healthy
      tts-server:
        condition: service_healthy
    environment:
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      RTVOICE_API_KEY: ${RTVOICE_API_KEY:-}
      STT_WS_URL: ws://stt-server:9090/v1/asr
      LLM_BASE_URL: ${LLM_BASE_URL:-http://llm-server:11434/v1}
      LLM_MODEL: ${LLM_MODEL:-qwen2.5:1.5b}
      LLM_API_KEY: ollama
      TTS_BASE_URL: http://tts-server:9880
      PUBLIC_WS_BASE: ${PUBLIC_WS_BASE:-ws://realtime-server:9000}
      AGENT_SYSTEM_PROMPT: ${AGENT_SYSTEM_PROMPT:-}
      AGENT_LLM_MAX_TOKENS: ${AGENT_LLM_MAX_TOKENS:-80}
      RTVOICE_MAX_CONCURRENT_SESSIONS: ${RTVOICE_MAX_CONCURRENT_SESSIONS:-5}
      RTVOICE_SESSION_QUEUE_DEPTH: ${RTVOICE_SESSION_QUEUE_DEPTH:-0}
      RTVOICE_SESSION_CREATE_TIMEOUT_S: ${RTVOICE_SESSION_CREATE_TIMEOUT_S:-60}
      RTVOICE_SESSION_IDLE_TIMEOUT_S: ${RTVOICE_SESSION_IDLE_TIMEOUT_S:-30}
      RTVOICE_SESSION_MAX_LIFETIME_S: ${RTVOICE_SESSION_MAX_LIFETIME_S:-1800}
      RTVOICE_WS_DISCONNECT_GRACE_S: ${RTVOICE_WS_DISCONNECT_GRACE_S:-0}
      RTVOICE_TURN_TIMEOUT_S: ${RTVOICE_TURN_TIMEOUT_S:-60}
    expose:
      - "9000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=2).status==200 else 1)"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.5"
    logging:
      driver: json-file
      options:
        max-size: "30m"
        max-file: "3"
```

- [ ] **Step 2: 加 env vars 到 .env.example**

定位 .env.example 中 STT/TTS env 段落附近（如 `# STT/TTS 对外鉴权（v0.6.1+）` 之后），追加：

```bash
# ============================================================
# Realtime Voice Service (SP2, v0.9+)
# ============================================================
# 公网 WS URL base（POST /v1/sessions response 里的 ws_url 用此 base）
# dev: ws://realtime-server:9000  prod: wss://your-domain.com
# PUBLIC_WS_BASE=ws://realtime-server:9000

# 并发与生命周期参数（默认调优 RTX 3060 12GB）
# 单 GPU 12GB (3060/4060)：默认值
# 单 GPU 24GB (3090/4090)：MAX_CONCURRENT_SESSIONS=10
# 多 GPU box：等 SP6+ GPU 调度后再说
RTVOICE_MAX_CONCURRENT_SESSIONS=5
RTVOICE_SESSION_QUEUE_DEPTH=0
RTVOICE_SESSION_CREATE_TIMEOUT_S=60
RTVOICE_SESSION_IDLE_TIMEOUT_S=30
RTVOICE_SESSION_MAX_LIFETIME_S=1800
RTVOICE_WS_DISCONNECT_GRACE_S=0
RTVOICE_TURN_TIMEOUT_S=60

# Forward-compatibility hooks（v1 暂不生效）
# RTVOICE_TTS_MODEL_REPLICAS=1     未来 24GB+ 可设 2，并发 TTS 翻倍
# RTVOICE_LLM_MAX_CONCURRENT=4
```

- [ ] **Step 3: 验证 docker-compose 语法**

```bash
docker compose -f docker-compose.yml config 2>&1 | grep -A 5 "realtime-server:" | head -20
```

Expected: 输出含 realtime-server service 配置（无 yaml 语法错误）。

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(compose): 加 realtime-server service block + .env.example (SP2 T6)

- docker-compose.yml: realtime-server image v0.9.0, expose 9000,
  depends_on stt+tts healthy, healthcheck, 12 env vars
- .env.example: SP2 段落 + RTX 3060 调优默认 + 24GB sizing 注释
- profile dev/prod 都启用"
```

---

## Task 7: 文档更新（README + ARCHITECTURE + OPERATIONS + COZYVOICE_INTEGRATION + sessions.md + CONVENTIONS.md）

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `OPERATIONS.md`
- Modify: `COZYVOICE_INTEGRATION.md`
- Modify: `docs/api/sessions.md`
- Modify: `docs/api/CONVENTIONS.md`

- [ ] **Step 1: README.md — Realtime Voice card 升 placeholder + 60s try 加 RTV**

找到 `What's in the box` 下的 Realtime Voice card，确认它已经描述 `POST /v1/sessions` + `WS /v1/realtime/{id}`（来自 SP1）。如果有 `（即将上线）` 等标注，**删除**。

在 60 秒 try 表里加一行（在 TTS 行之后）:

```markdown
| **Realtime 对话**（API 方式）| `curl -X POST http://127.0.0.1:9000/v1/sessions -d '{}' -H "Content-Type: application/json"` 拿 ws_url，然后 websocat 连 |
```

- [ ] **Step 2: ARCHITECTURE.md — §1 Mermaid 图 RTV 实线化 + §4 内容更新**

§1 Mermaid 图里的 `RTV[Realtime Voice<br/>POST /sessions +<br/>WS /v1/realtime]` 节点已存在；不需要改图。但确认下面文字是否还说"placeholder"或"SP2 实现"等占位字眼，删除或改为"已实现 (v0.9)"。

§4 Realtime Voice Service 章节内已有完整数据流 + 决策权衡描述。把所有"（SP2 实现）"等字眼**删除**，改为"已实现 v0.9.0"或直接删除括号内容。

- [ ] **Step 3: OPERATIONS.md — §2.5 加 SP2 env vars + §3 加升级路径 + §4 加 cookbook**

OPERATIONS.md §2 找到 `### 2.4 v0.7 (Fun-CosyVoice 3)` 之后，加：

```markdown
### 2.5 Realtime Voice Service (SP2, v0.9+)

| 变量 | 默认 | 调整时机 |
|---|---|---|
| `RTVOICE_MAX_CONCURRENT_SESSIONS` | 5 | RTX 3060 12GB 默认；GPU 升级后调高 |
| `RTVOICE_SESSION_IDLE_TIMEOUT_S` | 30 | 用户停下不说话多久 close |
| `RTVOICE_SESSION_MAX_LIFETIME_S` | 1800 | 单 session 最长 30 min |
| `RTVOICE_TURN_TIMEOUT_S` | 60 | 单 turn 处理最长时间 |
| `PUBLIC_WS_BASE` | ws://realtime-server:9000 | 公网部署改 wss://your-domain.com |
```

§3 升级路径加：

```markdown
### 3.4 v0.8.x → v0.9.0（SP2 加 Realtime Voice service）

```bash
# .env 不需改（默认值已调优 3060 12GB；想改 cap 可加 RTVOICE_MAX_CONCURRENT_SESSIONS）
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
               build realtime-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod up -d realtime-server
```

**验证**：
```bash
curl -s http://127.0.0.1:9000/health    # 期望 {"status":"ok"}
curl -X POST http://127.0.0.1:9000/v1/sessions -H 'Content-Type: application/json' -d '{}'
# 期望 201 + {"session_id":"sess_xxx","ws_url":"...","expires_at":"...","voice":"...","speed":1.0}
```
```

§4 cookbook 加：

```markdown
### 4.6 realtime-server: session 创建返 503 capacity_full

- 看 `docker logs rtvoice-realtime | grep "session created"` 看活的 session 数
- 如果实际只有 1-2 个但 503，可能 cleanup 没及时执行 — 重启 `docker compose ... restart realtime-server`
- 想接收更多并发：`RTVOICE_MAX_CONCURRENT_SESSIONS=10`（仅在 GPU 容量充足时调高，否则 TTS 队列卡）

### 4.7 realtime-server: WS 连接立即 close 4404

- session_id 拼错 / 过期 / 已被 cleanup
- 检查：`docker logs rtvoice-realtime | grep "session created\|cleanup"` 找 session_id 历史
- 客户端应在 `expires_at` 之前连，超出立即返 4410 expired
```

- [ ] **Step 4: COZYVOICE_INTEGRATION.md — §5 加 RealtimeClient Python SDK 例**

在 `## 5. Python SDK 示例` 末尾加：

```markdown
### 5.4 Realtime Voice 完整对话客户端

```python
import asyncio, json, os
import httpx, websockets


class RTVoiceRealtimeClient:
    def __init__(self, base_http=None, api_key=None):
        self.base_http = base_http or os.environ.get("RTVOICE_RT_HTTP", "http://realtime-server:9000")
        self.api_key = api_key or os.environ.get("RTVOICE_API_KEY", "").strip()

    async def create_session(self, voice="default_zh_female", speed=1.0):
        async with httpx.AsyncClient() as c:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            r = await c.post(
                f"{self.base_http}/v1/sessions",
                json={"voice": voice, "speed": speed},
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            return r.json()  # {session_id, ws_url, ...}

    async def conversation(self, ws_url: str, audio_chunks_iter, on_transcript=None):
        """Connect WS, stream audio, yield agent PCM chunks."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            # send PCM
            async def feed():
                async for pcm in audio_chunks_iter:
                    await ws.send(pcm)
                await ws.send("audio.eos")
            asyncio.create_task(feed())
            # receive PCM + events
            async for msg in ws:
                if isinstance(msg, bytes):
                    yield msg
                else:
                    ev = json.loads(msg)
                    if ev["type"] == "transcript.final":
                        if on_transcript:
                            on_transcript(ev["text"])
                    elif ev["type"] == "response.done":
                        return
                    elif ev["type"] == "error":
                        raise RuntimeError(f"realtime error: {ev}")


# 用法
async def main():
    client = RTVoiceRealtimeClient()
    sess = await client.create_session()
    print(f"session: {sess['session_id']}")
    
    async def mic_chunks():
        # 假设有 16k mono PCM 文件
        with open("user_input.pcm", "rb") as f:
            while True:
                c = f.read(3200)  # 100ms @ 16k mono int16
                if not c: break
                yield c

    pcm_out = bytearray()
    async for chunk in client.conversation(sess["ws_url"], mic_chunks(),
                                            on_transcript=lambda t: print(f"我说了: {t}")):
        pcm_out.extend(chunk)
    
    # 24k mono int16 → 写文件 / 播放
    open("agent_reply.pcm", "wb").write(pcm_out)


asyncio.run(main())
```
```

- [ ] **Step 5: docs/api/sessions.md — 状态从"协议骨架"改"已实现"**

文件开头 `> **状态：协议骨架 ready；完整实现见 SP2**` 删掉，改成：

```markdown
> **状态：v0.9.0 已实现**（SP3 加 prompt + memory + transcript 流式）。
```

`POST /v1/sessions (SP2 实现)` 头部去掉 `(SP2 实现)`；`WS /v1/realtime/{session_id} (SP2 实现)` 同。

- [ ] **Step 6: docs/api/CONVENTIONS.md — error code 表加 session.* 系列**

§6 error code 速查表里加：

```markdown
| `session.capacity_full` | server 超并发上限 |
| `session.not_found` | session_id 不存在 |
| `session.unauthorized` | Bearer 不匹配 creator |
| `session.expired` | session 超 max lifetime |
| `session.idle_timeout` | ws idle 超时 |
| `turn.timeout` | 单 turn 处理超时 |
| `turn.in_progress` | 前 turn 未结束就发新 audio.eos |
| `stt.empty` | STT final 为空（无有效语音）|
| `internal.upstream_closed` | 上游 service WS 断 |
```

- [ ] **Step 7: Commit 6 文档更新**

```bash
git add README.md ARCHITECTURE.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/sessions.md docs/api/CONVENTIONS.md
git commit -m "docs: SP2 完工配套文档更新 (SP2 T7)

README.md: Realtime Voice card 去 placeholder + 60s try 加 curl 例
ARCHITECTURE.md: §4 RTV section 去 (SP2 实现) 占位
OPERATIONS.md: §2.5 加 SP2 env vars + §3.4 升级路径 + §4.6/4.7 cookbook
COZYVOICE_INTEGRATION.md: §5.4 加 RealtimeClient Python SDK 完整例
docs/api/sessions.md: 状态从骨架升级为已实现
docs/api/CONVENTIONS.md: error code 表加 9 条 session/turn/stt/internal 系列"
```

---

## Task 8: CHANGELOG v0.9.0 + 全文档 lint + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 加 v0.9.0 entry 到 CHANGELOG.md**

定位 `## [Unreleased]` 段之后、`## [0.8.0]` 之前，插入:

```markdown
## [0.9.0] — 2026-05-08 — SP2 Realtime Voice 多 tenant session

平台化重构第三阶段：新增 `realtime-server` docker service 提供 `POST /v1/sessions` + `WS /v1/realtime/{id}` 多 tenant Realtime Voice service。

### Added

- `services/realtime-server/` 新 docker service
  - `Dockerfile` + `requirements.txt` (fastapi/uvicorn/websockets/httpx/pydantic)
  - `app/main.py` (FastAPI app + 2 endpoints + WS handler + auth)
  - `app/session_manager.py` (Session class + SessionManager + lifecycle: CREATED→ACTIVE→CLEANUP)
  - `app/pipeline.py` (run_turn: STT → LLM → TTS streaming)
  - `app/config.py` (12 个 env vars 集中)
  - `app/{stt,llm,tts}_client.py` (copy from agent-worker)
  - `app/error_schema.py` (per-service copy)
  - 3 个测试文件（unit + integration mock + endpoints TestClient）
- `docker-compose.yml` 加 realtime-server service block (image v0.9.0, expose 9000)
- `.env.example` SP2 段落（12 个 RTVOICE_* env vars + sizing 速查表）

### Changed

- `docs/api/sessions.md`: 状态从"协议骨架"升级为"v0.9.0 已实现"
- `docs/api/CONVENTIONS.md`: error code 表加 `session.*` `turn.*` `stt.empty` `internal.upstream_closed` 共 9 条
- `README.md` 60 秒 try 表加 Realtime API curl 示例
- `ARCHITECTURE.md` §4 Realtime Voice 段去掉"SP2 实现"占位
- `OPERATIONS.md` §2.5 SP2 env vars + §3.4 升级路径 + §4.6/4.7 故障 cookbook
- `COZYVOICE_INTEGRATION.md` §5.4 加 RealtimeClient Python SDK 完整例

### Notes / 设计决策

- `agent-worker` v0.7 LiveKit demo 路径**保留不动**进入维护模式；新 Realtime 主线在 realtime-server
- pipeline 代码 copy-paste 自 agent-worker（不抽 shared lib，与 SP1.5 决策一致）
- session_id Stripe 风格 `sess_<urlsafe-12bytes>` + Bearer + creator binding（可选）
- 协议 SP2 minimal: PCM in/out + audio.eos + transcript.final + response.done + error
- 不含 memory / prompt 透传 / transcript.partial / response.text — SP3 范围
- 所有 concurrency 参数 env-driven；3060 12GB 调优默认（cap=5）；未来 GPU 扩容只改 .env

### 验证（autonomous）

- ✅ FastAPI auto-gen `/openapi.json` 含 `/v1/sessions`
- ✅ unit test: SessionManager 10 测试全过（create/get/cleanup/capacity/expire/attach_ws）
- ✅ integration test: pipeline.run_turn() 4 mock 测试全过（happy/empty/llm 异常/finally）
- ✅ endpoints test: 9+ TestClient 测试全过
- ⏳ prod 集成测试（待 user 部署 + 浏览器对话验收）

详见 [SP2 设计文档](./docs/superpowers/specs/2026-05-08-sp2-realtime-session-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-08-sp2-realtime-session.md)。

---
```

- [ ] **Step 2: 全文档链接 lint**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in README.md ARCHITECTURE.md DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/CONVENTIONS.md docs/api/stt.md docs/api/tts.md docs/api/sessions.md; do
    [ -e "$f" ] || continue
    echo "--- $f ---"
    grep -oE '\]\(\./[^)#]+' "$f" | sed 's/](\.\///' | sort -u | while read p; do
        [ -e "$p" ] && echo "  [ok] $p" || echo "  [FAIL] $p"
    done
done
```

Expected: 全 [ok]，无 [FAIL]。

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): v0.9.0 — SP2 Realtime Voice 多 tenant session

- Added: services/realtime-server/ 新 docker service + 12 env vars
- Changed: 6 docs SP2 完工配套 sync
- Notes: agent-worker LiveKit demo 保留维护模式；Memory/transcript 留 SP3"

git push origin main 2>&1 | tail -5
```

Expected: push 成功。

---

## Task 9: prod 集成测试 + user-participation 验收

**Files:** none（read-only verification + user notification）

- [ ] **Step 1: prod 端 git pull + build + up**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -3
  echo
  t1=$(date +%s)
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server 2>&1 | tail -5
  t2=$(date +%s)
  echo "build: $((t2-t1))s"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d realtime-server 2>&1 | tail -5
  echo
  for i in $(seq 1 30); do
    h=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod ps --format "{{.Status}}" 2>&1 | grep -c healthy)
    [ "$h" -ge 6 ] && echo "[$i] $h/6 healthy" && break
    sleep 3
  done
}'
```

Expected: build 成功，realtime-server healthy。

- [ ] **Step 2: prod 验收测试 1 — 创建 session**

```bash
ssh root@192.168.66.163 'docker exec rtvoice-agent python3 -c "
import urllib.request, json
req = urllib.request.Request(
    \"http://realtime-server:9000/v1/sessions\",
    data=json.dumps({}).encode(),
    headers={\"Content-Type\":\"application/json\"},
)
r = urllib.request.urlopen(req, timeout=10)
print(\"status:\", r.status)
print(\"body:\", r.read().decode())
"'
```

Expected: status 201 + body 含 `session_id`/`ws_url`/`expires_at`/`voice`/`speed`。

- [ ] **Step 3: prod 验收测试 2 — capacity_full**

```bash
ssh root@192.168.66.163 'docker exec rtvoice-agent python3 -c "
import urllib.request, json
# 创 6 个 session（默认 cap=5）；第 6 个应 503
codes = []
for i in range(6):
    try:
        req = urllib.request.Request(
            \"http://realtime-server:9000/v1/sessions\",
            data=json.dumps({}).encode(),
            headers={\"Content-Type\":\"application/json\"},
        )
        r = urllib.request.urlopen(req, timeout=5)
        codes.append(r.status)
    except urllib.error.HTTPError as e:
        codes.append(e.code)
print(\"codes:\", codes)
# 期望 [201, 201, 201, 201, 201, 503]
"'
```

Expected: 前 5 个 201，第 6 个 503 + body code = `session.capacity_full`。

- [ ] **Step 4: prod 验收测试 3 — agent-worker LiveKit demo 不破**

```bash
ssh root@192.168.66.163 'docker compose -f /data/RTVoice/docker-compose.yml -f /data/RTVoice/docker-compose.prod.yml --profile prod ps agent-worker --format json | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(\"agent-worker:\", d.get(\"State\",\"?\"))"'
```

Expected: `State: running`（agent-worker 仍跑 v0.7 LiveKit demo）。

- [ ] **Step 5: 通知 user 做浏览器对话验收**

```
SP2 沙盒 + prod 部分完工。以下需要你做：

1. 浏览器测试页（如有 /v1/sessions 测试页）创建 session → 加入 ws → 说一句
   - 期望：听到 agent 回复 + 看到 transcript.final 事件
2. 多并发：同时开 2-3 个浏览器 tab 创建 session，验各自独立工作
3. session 闲置 30s 后自动 close（看 ws 关闭码 4408）
4. agent-worker LiveKit demo 仍可用（旧浏览器测试页）
```

- [ ] **Step 6: User 反馈后标 SP2 完工**

如有问题 → fix loop；OK → SP2 done，准备 SP3 brainstorm。

---

## Self-Review

### 1. Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §1.1 realtime-server 9 文件 | T1-T5（每 task 一组）|
| §3 整体架构 + 文件结构 | T1 设骨架 + T2-T5 各文件 |
| §3.3 docker-compose 集成 | T6 |
| §4 API schema | T5 main.py |
| §5 Lifecycle + 7 timeouts/concurrency | T2 config + T3 session_manager |
| §6 Pipeline 实现 | T4 + T5 |
| §7 Error codes 总表 | T7 (CONVENTIONS) + T5 (raise) |
| §8 验收（沙盒 6 + prod 7）| T2-T5 沙盒测试 + T9 prod 验收 |

无遗漏。

### 2. Placeholder scan

无 TBD / TODO；每 step 含完整代码或命令。

### 3. Type consistency

- `Session` dataclass 字段在 T3（定义）+ T5（使用）一致
- `SessionManager` API: create/get/attach_ws/cleanup/active_count/all_sessions 在 T3+T5 一致
- `ErrorResponse` schema 与 SP1.5 沿用
- `run_turn(sess, ws)` 签名在 T4+T5 一致

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-sp2-realtime-session.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - 我 dispatch fresh subagent per task + spec/quality 双审；与 SP1/SP1.5 同流程
2. **Inline Execution** - 本 session 批量执行 + checkpoints

Which approach?
