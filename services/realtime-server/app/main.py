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

from pathlib import Path

from fastapi import (
    Depends, FastAPI, Header, HTTPException, Request, WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import app.config as config
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


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def index() -> HTMLResponse:
    idx = _STATIC_DIR / "index.html"
    if not idx.is_file():
        return HTMLResponse("<h1>RTVoice Realtime</h1><p>静态测试页未部署。</p>")
    return HTMLResponse(idx.read_text(encoding="utf-8"))


try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(app)
except Exception as e:
    log.warning("prometheus instrumentator unavailable: %s", e)


class SessionCreateRequest(BaseModel):
    voice: str | None = Field(None, description="TTS voice spk_id, default: default_zh_female")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="TTS speed factor")
    prompt: str | None = Field(None, description="System prompt; default: env RTVOICE_DEFAULT_PROMPT")
    audit_persist: bool = Field(False, description="If true, persist transcript JSONL to AUDIT_DIR")


class SessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str
    expires_at: str
    voice: str
    speed: float
    prompt: str
    audit_persist: bool


def _check_bearer_http(authorization: str | None) -> str:
    if not config.RTVOICE_API_KEY:
        return ""
    if not authorization:
        raise api_error(401, "auth.missing_token", "Authorization header required")
    if authorization != f"Bearer {config.RTVOICE_API_KEY}":
        raise api_error(401, "auth.invalid_token", "invalid Bearer token")
    return config.RTVOICE_API_KEY


def _extract_ws_bearer(ws: WebSocket) -> str | None:
    if not config.RTVOICE_API_KEY:
        return ""
    auth = ws.headers.get("authorization", "")
    if auth == f"Bearer {config.RTVOICE_API_KEY}":
        return config.RTVOICE_API_KEY
    proto = ws.headers.get("sec-websocket-protocol", "")
    for p in (s.strip() for s in proto.split(",")):
        if p.startswith("bearer.") and p[len("bearer."):] == config.RTVOICE_API_KEY:
            return config.RTVOICE_API_KEY
    if ws.query_params.get("token") == config.RTVOICE_API_KEY:
        return config.RTVOICE_API_KEY
    return None


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
            "transcript_partial": True,
            "response_text": True,
            "memory": True,
            "memory_max_turns": config.MEMORY_MAX_TURNS,
            "audit_persist": True,
            "default_prompt": config.DEFAULT_PROMPT,
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
    prompt = req.prompt if req.prompt is not None else config.DEFAULT_PROMPT
    if len(prompt) > config.PROMPT_MAX_CHARS:
        raise api_error(422, "prompt.too_long",
                        f"prompt > {config.PROMPT_MAX_CHARS} chars")

    try:
        sess = await session_mgr.create(
            creator_key_hash=hash_key(bearer),
            voice=voice,
            speed=req.speed,
            prompt=prompt,
            audit_persist=req.audit_persist,
        )
    except CapacityFull as e:
        raise api_error(503, "session.capacity_full", str(e))

    return SessionCreateResponse(
        session_id=sess.id,
        ws_url=f"{config.PUBLIC_WS_BASE}/v1/realtime/{sess.id}",
        expires_at=sess.expires_at.isoformat(),
        voice=voice,
        speed=req.speed,
        prompt=prompt,
        audit_persist=req.audit_persist,
    )


@app.websocket("/v1/realtime/{session_id}")
async def realtime_ws(ws: WebSocket, session_id: str) -> None:
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

    async def _on_stt_partial(text: str) -> None:
        if not text:
            return
        try:
            await ws.send_json({"type": "transcript.partial", "text": text, "stable": False})
            if sess.audit_writer is not None:
                await sess.audit_writer.write({"event": "transcript.partial", "text": text})
        except Exception:
            log.exception("on_partial forward failed")

    sess.stt_client = STTClient(
        config.STT_WS_URL,
        on_partial=_on_stt_partial,
        api_key=config.RTVOICE_API_KEY or None,
    )
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
                try:
                    await sess.stt_client.feed(msg["bytes"])
                except Exception:
                    log.exception("STT feed failed")
            elif msg.get("text"):
                text_msg = msg["text"]
                if text_msg == "audio.eos":
                    if sess.current_turn_task and not sess.current_turn_task.done():
                        await ws.send_json({
                            "type": "error", "code": "turn.in_progress",
                            "message": "previous turn not yet done",
                            "request_id": None,
                        })
                    else:
                        asyncio.create_task(run_turn(sess, ws))
                else:
                    import json as _json
                    try:
                        ev = _json.loads(text_msg)
                    except Exception:
                        log.debug("session %s: non-JSON text %r", session_id, text_msg[:80])
                        continue
                    if ev.get("type") == "session.update":
                        allowed = {"type", "prompt", "voice", "speed"}
                        extra = set(ev.keys()) - allowed
                        if extra:
                            await ws.send_json({
                                "type": "error",
                                "code": "session.update.invalid",
                                "message": f"only prompt/voice/speed; got extra: {sorted(extra)}",
                                "request_id": None,
                            })
                            continue
                        if "prompt" in ev:
                            new_prompt = str(ev["prompt"])
                            if len(new_prompt) > config.PROMPT_MAX_CHARS:
                                await ws.send_json({
                                    "type": "error", "code": "prompt.too_long",
                                    "message": f"prompt > {config.PROMPT_MAX_CHARS}",
                                    "request_id": None,
                                })
                            else:
                                sess.prompt = new_prompt
                                log.info("session %s prompt updated (%d chars)",
                                         session_id, len(new_prompt))
                        if "voice" in ev:
                            sess.voice = str(ev["voice"])
                            sess.tts_client_dirty = True
                            log.info("session %s voice updated to %s (dirty)",
                                     session_id, sess.voice)
                        if "speed" in ev:
                            try:
                                s = float(ev["speed"])
                            except (TypeError, ValueError):
                                await ws.send_json({
                                    "type": "error", "code": "validation.invalid_request",
                                    "message": "speed must be a number",
                                    "request_id": None,
                                })
                                continue
                            if not (0.5 <= s <= 2.0):
                                await ws.send_json({
                                    "type": "error", "code": "validation.invalid_request",
                                    "message": "speed out of range (0.5-2.0)",
                                    "request_id": None,
                                })
                                continue
                            sess.speed = s
                            sess.tts_client_dirty = True
                            log.info("session %s speed updated to %.2f (dirty)",
                                     session_id, s)
                    elif ev.get("type") == "memory.clear":
                        sess.memory.clear()
                        log.info("session %s memory cleared", session_id)
                        if sess.audit_writer is not None:
                            try:
                                await sess.audit_writer.write({"event": "memory.clear"})
                            except Exception:
                                log.exception("audit write memory.clear failed")
                    else:
                        log.debug("session %s: unknown event %r",
                                  session_id, ev.get("type"))
            else:
                log.debug("session %s: unknown msg %s", session_id, msg)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws handler error")
    finally:
        await session_mgr.cleanup(session_id, reason="ws_close")
