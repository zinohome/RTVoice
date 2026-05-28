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
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pathlib import Path

from fastapi import (
    Depends, FastAPI, Header, HTTPException, Request, WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
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

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied, QuotaExceeded
from rtvoice_auth.quota import QuotaTracker
from rtvoice_auth.lifespan import auto_migrate_legacy
from rtvoice_auth.ws import pick_bearer_subprotocol
from rtvoice_auth.instrumentation import RequestMetricsMiddleware
from rtvoice_auth.openapi import add_bearer_security_scheme
from rtvoice_auth.metrics import REALTIME_SESSION_DURATION_SECONDS
from app.admin_api import router as admin_router
from app.auth_api import (
    router as auth_router, ensure_admin_key, ensure_internal_service_key,
    admin_key_from_session,
)
from app.console_api import router as console_router

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
    # SP6: init key store + quota
    backend = os.environ.get("RTVOICE_KEYS_BACKEND", "yaml").lower()
    if backend == "redis":
        import redis.asyncio as redis_lib
        from rtvoice_auth.store_redis import RedisKeyStore
        url = os.environ.get("RTVOICE_REDIS_URL", "redis://redis:6379/0")
        client = redis_lib.from_url(url)
        app.state.key_store = RedisKeyStore(client)
    else:
        from rtvoice_auth.store import YamlKeyStore
        path = os.environ.get("RTVOICE_KEYS_FILE", "/data/keys.yaml")
        app.state.key_store = YamlKeyStore(path)
    await app.state.key_store.load()
    await auto_migrate_legacy(app.state.key_store)
    # Admin Console：启动即确保全权限内部 admin key 存在且未被吊销（自愈）
    try:
        await ensure_admin_key(app.state.key_store)
    except Exception:
        log.exception("ensure_admin_key on startup failed")
    # 跨服务调用 key（RTVOICE_API_KEY）自愈：保证 realtime 管线 + console 代理始终有权限
    try:
        await ensure_internal_service_key(app.state.key_store)
    except Exception:
        log.exception("ensure_internal_service_key on startup failed")
    app.state.quota = QuotaTracker()
    app.state.scope = "realtime"
    session_mgr = SessionManager(quota=app.state.quota)
    session_mgr.start_expire_loop()

    # SP7: hot reload watcher
    async def _on_keys_changed():
        await app.state.key_store.load()
        log.info("key store hot-reloaded")

    from rtvoice_auth.watcher import YamlFileWatcher, RedisPubSubListener
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.store_redis import RedisKeyStore
    debounce_ms = int(os.environ.get("RTVOICE_KEYS_RELOAD_DEBOUNCE_MS", "100"))
    app.state.key_watcher = None
    if isinstance(app.state.key_store, YamlKeyStore):
        app.state.key_watcher = YamlFileWatcher(
            path=str(app.state.key_store.path),
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        app.state.key_watcher.start()
    elif isinstance(app.state.key_store, RedisKeyStore):
        app.state.key_watcher = RedisPubSubListener(
            redis_client=app.state.key_store.client,
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        await app.state.key_watcher.start()

    log.info("realtime-server lifespan: ready")
    yield
    log.info("realtime-server lifespan: shutdown")
    if hasattr(app.state, "key_watcher") and app.state.key_watcher is not None:
        try:
            await app.state.key_watcher.stop()
        except Exception:
            log.exception("key_watcher stop failed")
    if session_mgr:
        await session_mgr.stop_expire_loop()
        for s in session_mgr.all_sessions():
            await session_mgr.cleanup(s.id, reason="shutdown")


app = FastAPI(
    title="RTVoice Realtime Voice Server",
    version="0.19.0",
    lifespan=lifespan,
)

_cors_raw = os.environ.get("RTVOICE_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
    max_age=3600,
)

app.add_exception_handler(HTTPException, http_exception_handler())
app.add_exception_handler(RequestValidationError, validation_exception_handler())

# SP10 G3 — per-key request metrics
app.add_middleware(RequestMetricsMiddleware, service_name="realtime-server")
# SP10 G4 — OpenAPI Bearer securityScheme
add_bearer_security_scheme(app)

# SP14 — Admin API（keys lifecycle over HTTP；scope='admin' required）
app.include_router(admin_router)
# Admin Console 鉴权：用户名/密码登录 + HttpOnly 会话 cookie
app.include_router(auth_router)
# Admin Console 服务端代理：cookie 鉴权 → 注入内部 key → 转发 STT/TTS/Token/Voices
app.include_router(console_router)


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


async def require_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Key:
    # Admin Console 会话 cookie → 全权限内部 admin key（前端不带 Bearer）
    session_key = await admin_key_from_session(request)
    if session_key is not None:
        request.state.key_id = session_key.id
        return session_key
    if not authorization or not authorization.startswith("Bearer "):
        raise api_error(401, "auth.missing_token", "Authorization: Bearer required")
    secret = authorization[len("Bearer "):]
    try:
        key = await verify_key(secret,
                               scope=request.app.state.scope,
                               store=request.app.state.key_store)
        # SP10 G3 — 把 key_id 喂给 RequestMetricsMiddleware
        request.state.key_id = key.id
        return key
    except InvalidToken as e:
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        raise api_error(403, e.code, e.message)


async def _extract_ws_bearer_key(ws: WebSocket) -> Key | None:
    """Admin Console 会话 cookie 优先；否则三路 Bearer 验证。返 Key record。"""
    session_key = await admin_key_from_session(ws)
    if session_key is not None:
        return session_key
    secret = None
    auth = ws.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        secret = auth[len("Bearer "):]
    if not secret:
        proto = ws.headers.get("sec-websocket-protocol", "")
        for p in (s.strip() for s in proto.split(",")):
            if p.startswith("bearer."):
                secret = p[len("bearer."):]
                break
    if not secret:
        secret = ws.query_params.get("token")
    if not secret:
        return None
    try:
        return await verify_key(secret, scope="realtime", store=ws.app.state.key_store)
    except AuthError:
        return None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict:
    return {
        # SP10 G4 — service 字段（与其他 service /info 一致）；保留 "name" 向后兼容
        "service": "realtime-server",
        "name": "realtime-server",
        "version": "0.19.0",
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


def _build_ws_url(request: Request, session_id: str) -> str:
    """SP9 T3 — ws_url 外部可达。

    优先级：
      1. config.PUBLIC_WS_BASE（显式 override，比如 wss://your-domain.com）
      2. X-Forwarded-Host + X-Forwarded-Proto（反代场景）
      3. Host header + 从 request.url.scheme 推断 ws/wss
    旧行为（默认 PUBLIC_WS_BASE=ws://realtime-server:9000 容器主机名）只在
    env 显式设置时才使用，避免 D4-F3 那种"返了容器主机名给浏览器"的尴尬。
    """
    if config.PUBLIC_WS_BASE and not config.PUBLIC_WS_BASE.startswith("ws://realtime-server"):
        base = config.PUBLIC_WS_BASE
    else:
        fwd_host = request.headers.get("x-forwarded-host")
        fwd_proto = request.headers.get("x-forwarded-proto")
        if fwd_host:
            ws_scheme = "wss" if fwd_proto == "https" else "ws"
            base = f"{ws_scheme}://{fwd_host}"
        else:
            host = request.headers.get("host", "")
            ws_scheme = "wss" if request.url.scheme == "https" else "ws"
            base = f"{ws_scheme}://{host}" if host else config.PUBLIC_WS_BASE
    return f"{base}/v1/realtime/{session_id}"


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
    request: Request,
    key: Key = Depends(require_key),
) -> SessionCreateResponse:
    # SP6 quota acquire
    try:
        await request.app.state.quota.acquire_session(key)
    except QuotaExceeded as e:
        raise api_error(429, e.code, e.message)

    voice = req.voice or config.DEFAULT_VOICE
    prompt = req.prompt if req.prompt is not None else config.DEFAULT_PROMPT
    if len(prompt) > config.PROMPT_MAX_CHARS:
        await request.app.state.quota.release_session(key.id)
        raise api_error(422, "prompt.too_long",
                        f"prompt > {config.PROMPT_MAX_CHARS} chars")

    try:
        sess = await session_mgr.create(
            creator_key_hash=key.id,  # ← 改用 key.id
            voice=voice,
            speed=req.speed,
            prompt=prompt,
            audit_persist=req.audit_persist,
        )
        sess.key_id = key.id
    except CapacityFull as e:
        await request.app.state.quota.release_session(key.id)
        raise api_error(503, "session.capacity_full", str(e))

    return SessionCreateResponse(
        session_id=sess.id,
        ws_url=_build_ws_url(request, sess.id),
        expires_at=sess.expires_at.isoformat(),
        voice=voice,
        speed=req.speed,
        prompt=prompt,
        audit_persist=req.audit_persist,
    )


@app.delete(
    "/v1/sessions/{session_id}",
    status_code=204,
    summary="End a Realtime Voice session early (SP9 T6)",
    description="客户端主动结束 session：释放 quota / 关 WS / 触发 cleanup。"
                "对不存在 / 已结束的 session 返 204（幂等）。",
    tags=["sessions"],
    responses={
        401: {"model": ErrorResponse, "description": "Auth failed"},
        403: {"model": ErrorResponse, "description": "Not session owner"},
    },
)
async def delete_session(
    session_id: str,
    key: Key = Depends(require_key),
) -> Response:
    sess = session_mgr.get(session_id) if session_mgr else None
    if sess is not None:
        # 仅 creator 可关；不是 owner → 403 (不是 404 防泄漏存在性)
        if sess.creator_key_hash != key.id:
            raise api_error(403, "session.not_owner", "session belongs to another key")
        await session_mgr.cleanup(session_id, reason="client_close")
    return Response(status_code=204)


@app.websocket("/v1/realtime/{session_id}")
async def realtime_ws(ws: WebSocket, session_id: str) -> None:
    key = await _extract_ws_bearer_key(ws)
    if key is None:
        await ws.close(code=4401, reason="unauthorized")
        return

    sess = session_mgr.get(session_id) if session_mgr else None
    if sess is None:
        await ws.close(code=4404, reason="session_not_found")
        return
    if sess.creator_key_hash != key.id:
        await ws.close(code=4403, reason="session_unauthorized")
        return
    if sess.expires_at < datetime.now(timezone.utc):
        await ws.close(code=4410, reason="session_expired")
        return

    await ws.accept(subprotocol=pick_bearer_subprotocol(ws))
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
