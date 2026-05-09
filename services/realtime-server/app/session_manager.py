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
from app.memory import ConversationMemory
from app.audit import AuditWriter

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
    ws: Any = None
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_turn_task: Optional[asyncio.Task] = None
    stt_client: Any = None
    llm_client: Any = None
    tts_client: Any = None
    # SP3 fields
    prompt: str = ""
    memory: Any = None
    audit_persist: bool = False
    audit_writer: Any = None


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

    async def create(
        self,
        creator_key_hash: str,
        voice: str,
        speed: float,
        prompt: str = "",
        audit_persist: bool = False,
    ) -> Session:
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
                prompt=prompt,
                memory=ConversationMemory(max_turns=config.MEMORY_MAX_TURNS),
                audit_persist=audit_persist,
            )
            if audit_persist:
                sess.audit_writer = AuditWriter(
                    sess.id,
                    base_dir=config.AUDIT_DIR,
                    queue_max=config.AUDIT_QUEUE_MAX,
                )
            self._sessions[sess.id] = sess
            log.info("session created: id=%s voice=%s speed=%.2f audit=%s expires=%s",
                     sess.id, sess.voice, sess.speed, audit_persist,
                     sess.expires_at.isoformat())
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
        if sess.current_turn_task and not sess.current_turn_task.done():
            sess.current_turn_task.cancel()
            try:
                await sess.current_turn_task
            except (asyncio.CancelledError, Exception):
                pass
        if sess.ws:
            try:
                close_codes = {"idle": 4408, "expired": 4410, "ws_close": 1000}
                code = close_codes.get(reason, 1000)
                await sess.ws.close(code=code)
            except Exception:
                pass
        if sess.audit_writer is not None:
            try:
                await sess.audit_writer.aclose()
            except Exception:
                log.exception("audit_writer.aclose failed for %s", session_id)
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
            if sess.state == "CREATED":
                age_s = (now - sess.created_at).total_seconds()
                if age_s > config.SESSION_CREATE_TIMEOUT_S:
                    to_cleanup.append((sid, "create_timeout"))
                    continue
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
