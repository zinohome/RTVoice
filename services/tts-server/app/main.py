"""RTVoice TTS Server.

后端：Kokoro v1.0 ONNX (~325MB), CPU
前端：HTTP chunked streaming, JSON in / PCM bytes out

HTTP 协议（v0.5）
==================
端点：
    POST /tts/stream
    Content-Type: application/json

请求体：
    {
      "text": "你好，今天天气真好。",
      "voice": "zf_xiaobei",      // 可选；默认中文女声
      "speed": 1.0                // 可选；0.5-1.5
    }

响应：
    HTTP/1.1 200 OK
    Content-Type: application/octet-stream
    Transfer-Encoding: chunked
    X-Sample-Rate: 24000
    X-Channels: 1

    [PCM int16 LE bytes 流，按句切分；每句 chunk 在合成完成后整段推出]

辅助端点：
    GET /health           ── {"status": "ok"}
    GET /info             ── 模型/音色信息
    GET /voices           ── 可用音色 ID 列表

设计：
    - Kokoro 模型本身非流式（单次输入 → 单次输出），用"按标点切句"模拟流式
    - 长文本分句后逐句合成；每句合成完即推；调用方播放节奏由网络/HTTP 缓冲决定
    - 单实例 Kokoro，串行合成（CPU 资源限制 + 内存友好）
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

import time

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from app.error_schema import ErrorResponse, api_error, http_exception_handler, validation_exception_handler
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from typing import Annotated

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.tts")

MODELS_DIR = Path(os.environ.get("TTS_MODELS_DIR", "/app/models"))
MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"
DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "zf_xiaobei")  # 中文女声
DEFAULT_LANG = os.environ.get("TTS_DEFAULT_LANG", "cmn")           # Mandarin
SAMPLE_RATE = 24000

# Bearer 鉴权：留空 = 鉴权关闭（dev 默认）
RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip()


def _check_client_auth(authorization: str | None = Header(None)) -> None:
    """Legacy fallback (deprecated v0.6.2). Kept only as compatibility shim;
    新代码应通过 require_key 走 rtvoice_auth.key_store。"""
    if not RTVOICE_API_KEY:
        return
    if authorization != f"Bearer {RTVOICE_API_KEY}":
        raise api_error(401, "auth.invalid_token", "invalid or missing Bearer token")


# SP6 T11: scope=tts via rtvoice_auth.key_store
async def require_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Key:
    if not authorization or not authorization.startswith("Bearer "):
        raise api_error(401, "auth.missing_token", "Authorization: Bearer required")
    secret = authorization[len("Bearer "):]
    try:
        return await verify_key(secret,
                                scope=request.app.state.scope,
                                store=request.app.state.key_store)
    except InvalidToken as e:
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        raise api_error(403, e.code, e.message)

# 句子切分正则：中英文标点都吃
_SENTENCE_SPLIT = re.compile(r'(?<=[。！？\.\!\?])\s*|(?<=[，；,;])\s+')
# 短句最小长度（合成 < N 字的太碎，合并到下一句）
MIN_PHRASE_CHARS = 4


def split_phrases(text: str) -> list[str]:
    """按标点切短语；过短的合并到下一句。"""
    raw = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    out: list[str] = []
    buf = ""
    for p in raw:
        buf = (buf + p) if buf else p
        if len(buf) >= MIN_PHRASE_CHARS:
            out.append(buf)
            buf = ""
    if buf:
        if out:
            out[-1] = out[-1] + buf
        else:
            out.append(buf)
    return out


# 全局 Kokoro 单例（避免重复加载 325MB 模型）
_kokoro = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kokoro
    log.info("加载 Kokoro 模型: %s (%.1fMB)", MODEL_PATH, MODEL_PATH.stat().st_size / 1e6)
    log.info("加载音色 embedding: %s (%.1fMB)", VOICES_PATH, VOICES_PATH.stat().st_size / 1e6)
    # 在线程池里加载，避免阻塞事件循环（虽然 startup 期间没并发，但养成习惯）
    from kokoro_onnx import Kokoro
    _kokoro = await asyncio.to_thread(
        Kokoro, str(MODEL_PATH), str(VOICES_PATH)
    )
    voices = list(_kokoro.get_voices())
    log.info("Kokoro 就绪：%d 个音色，默认 voice=%s lang=%s",
             len(voices), DEFAULT_VOICE, DEFAULT_LANG)
    # SP6 T11: init key store + scope=tts
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
    app.state.scope = "tts"
    log.info("key store ready (backend=%s, scope=tts)", backend)

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

    yield
    log.info("shutdown")
    if hasattr(app.state, "key_watcher") and app.state.key_watcher is not None:
        try:
            await app.state.key_watcher.stop()
        except Exception:
            log.exception("key_watcher stop failed")


app = FastAPI(title="RTVoice TTS Server", version="0.16.0", lifespan=lifespan)

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

# --- Prometheus metrics ---
SYNTH_PHRASES = Counter("rtvoice_tts_phrases_total", "Phrases synthesized")
SYNTH_FAILS = Counter("rtvoice_tts_failures_total", "Phrase synth failures")
TTFB = Histogram(
    "rtvoice_tts_ttfb_seconds",
    "Time to first PCM byte after request",
    buckets=(0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)
PHRASE_RTF = Histogram(
    "rtvoice_tts_phrase_rtf",
    "Per-phrase real-time factor (audio_seconds / synth_seconds)",
    buckets=(0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0),
)
Instrumentator(excluded_handlers=["/health", "/metrics", "/v1/tts/stream"]).instrument(app).expose(app)


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice: str | None = Field(None, description="音色 ID，缺省用 TTS_DEFAULT_VOICE")
    speed: float = Field(1.0, ge=0.5, le=1.5)
    lang: str | None = Field(None, description="语言代码，缺省用 TTS_DEFAULT_LANG")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if _kokoro is not None else "loading"}


@app.get("/info")
async def info() -> dict:
    if _kokoro is None:
        return {"status": "loading"}
    return {
        "model": str(MODEL_PATH.name),
        "sample_rate": SAMPLE_RATE,
        "default_voice": DEFAULT_VOICE,
        "default_lang": DEFAULT_LANG,
        "voice_count": len(list(_kokoro.get_voices())),
    }


@app.get("/v1/voices")
async def voices(key: Key = Depends(require_key)) -> dict:
    if _kokoro is None:
        raise api_error(503, "tts.not_ready", "Kokoro 尚未加载")
    return {"voices": sorted(_kokoro.get_voices())}


def _to_pcm_int16(samples_f32: np.ndarray) -> bytes:
    """Kokoro 输出 float32 in [-1,1]；转 int16 LE bytes（LiveKit 习惯）。"""
    clipped = np.clip(samples_f32, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


async def _synthesize_stream(req: TTSRequest, request: Request) -> AsyncIterator[bytes]:
    """按短语切分，逐句合成 → yield PCM bytes。

    监听 request.is_disconnected() 让客户端断开时及时退出（节约 CPU）。
    """
    assert _kokoro is not None
    voice = req.voice or DEFAULT_VOICE
    lang = req.lang or DEFAULT_LANG
    phrases = split_phrases(req.text)
    log.info("[TTS] voice=%s lang=%s speed=%.2f text_len=%d phrases=%d",
             voice, lang, req.speed, len(req.text), len(phrases))

    t_request_start = time.time()
    first_yielded = False
    for i, phrase in enumerate(phrases):
        if await request.is_disconnected():
            log.info("[TTS] client disconnected, stop at phrase %d/%d", i, len(phrases))
            return
        try:
            t0 = time.time()
            samples, sr = await asyncio.to_thread(
                _kokoro.create, phrase, voice=voice, speed=req.speed, lang=lang
            )
            assert sr == SAMPLE_RATE, f"unexpected sr {sr}"
            synth_s = time.time() - t0
            audio_s = len(samples) / sr
            if synth_s > 0:
                PHRASE_RTF.observe(audio_s / synth_s)
            pcm = _to_pcm_int16(samples)
            log.debug("[TTS] phrase %d/%d %r → %d bytes (synth %.0fms, audio %.2fs)",
                      i + 1, len(phrases), phrase[:20], len(pcm), synth_s * 1000, audio_s)
            SYNTH_PHRASES.inc()
            if not first_yielded:
                TTFB.observe(time.time() - t_request_start)
                first_yielded = True
            yield pcm
        except Exception as e:
            log.exception("[TTS] phrase %d 合成失败: %s", i, e)
            SYNTH_FAILS.inc()
            continue


@app.post("/v1/tts/stream")
async def tts_stream(req: TTSRequest, request: Request,
                     key: Key = Depends(require_key)):
    if _kokoro is None:
        raise api_error(503, "tts.not_ready", "Kokoro 尚未加载")
    voice = req.voice or DEFAULT_VOICE
    if voice not in _kokoro.get_voices():
        raise api_error(400, "tts.voice_not_found", f"未知音色 voice={voice!r}")
    return StreamingResponse(
        _synthesize_stream(req, request),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Format": "pcm-int16-le",
        },
    )


