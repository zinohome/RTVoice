"""RTVoice TTS Server — CosyVoice 2 GPU 变体 (v0.6.0)

后端：CosyVoice2-0.5B (modelscope: iic/CosyVoice2-0.5B)，~5GB 模型 fp16 GPU
前端：与 Kokoro 版完全相同的 HTTP 协议
    POST /tts/stream  body={text, voice, lang?, speed?}
    → chunked PCM int16 LE 24kHz mono

⚠️ CosyVoice 2-0.5B **不自带 SFT 预训练音色**（spk2info.pt 文件不存在），
   但 repo 自带 asset/zero_shot_prompt.wav 作为默认 reference audio。
   启动时调 add_zero_shot_spk() 把它注册成 SFT 音色 ID 'default_zh_female'，
   之后 inference_sft() 即可用。这是 CosyVoice 2 的官方推荐路径。

   未来想换音色：把自己的参考音频放到 named volume，然后 add_zero_shot_spk
   注册新 ID（v0.7+ 加 admin endpoint）。

agent 端代码完全不变；切换由 docker-compose 的 image 选择。Kokoro voice
ID（zf_xiaobei 等）+ "中文女"/"中文男" 等都 alias 到 default_zh_female。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import torch
import torchaudio
from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

# 让 Python 找到 CosyVoice 模块
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", "/opt/CosyVoice")
sys.path.insert(0, COSYVOICE_DIR)
sys.path.insert(0, os.path.join(COSYVOICE_DIR, "third_party/Matcha-TTS"))

from cosyvoice.cli.cosyvoice import CosyVoice2  # noqa: E402
from fastapi.exceptions import RequestValidationError
from app.error_schema import ErrorResponse, api_error, http_exception_handler, validation_exception_handler
from typing import Annotated

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy
from rtvoice_auth.ws import pick_bearer_subprotocol
from rtvoice_auth.instrumentation import RequestMetricsMiddleware
from rtvoice_auth.openapi import add_bearer_security_scheme
from rtvoice_auth.metrics import TTS_CHARS_TOTAL
from rtvoice_auth.metrics_labels import safe_key_id

import re

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.tts.cosyvoice")

MODEL_DIR = os.environ.get(
    "MODEL_DIR", "/opt/CosyVoice/pretrained_models/CosyVoice2-0.5B"
)
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", "/opt/CosyVoice")
SAMPLE_RATE = 24000

# CosyVoice 2-0.5B 不自带 SFT 音色，启动时用 repo 自带 reference 注册一个
DEFAULT_SPK_ID = "default_zh_female"
DEFAULT_PROMPT_WAV = os.path.join(COSYVOICE_DIR, "asset/zero_shot_prompt.wav")
# 此参考音频对应的文本（CosyVoice repo runtime/python/fastapi/client.py 默认值）
DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。"

DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", DEFAULT_SPK_ID)

# 用户上传的 reference wav 持久化目录（与模型同 named volume，重启后保留）
VOICES_WAV_DIR = Path(MODEL_DIR).parent / "voices"

# Client Bearer 鉴权（v0.6.1）：保护 /tts/stream + GET /voices
# 留空 = 鉴权关闭（dev 默认）；prod 暴露公网时必填
RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip()

# Admin endpoints (POST/DELETE /voices/...) Bearer 鉴权（独立 key，权限更高）
# 留空 = 禁用 admin endpoints（防止误开放）
ADMIN_API_KEY = os.environ.get("TTS_ADMIN_API_KEY", "").strip()

# 上传 wav 大小上限（防爆磁盘）；CosyVoice 推荐 prompt 3-30 秒 16k mono → ≤2 MB
MAX_WAV_BYTES = int(os.environ.get("TTS_MAX_WAV_BYTES", str(5 * 1024 * 1024)))

# spk_id 限制：避免路径穿越/特殊字符；接受字母数字下划线和中日韩字符
SPK_ID_RE = re.compile(r"^[\w一-鿿぀-ヿ-]{1,64}$")

# 任意 voice 别名都 fallback 到默认注册的 SFT id
# Kokoro 用户不改 .env 可直接复用
VOICE_ALIASES = {
    "zf_xiaobei": DEFAULT_SPK_ID,
    "zf_xiaoni": DEFAULT_SPK_ID,
    "zm_yunjian": DEFAULT_SPK_ID,
    "zm_yunxia": DEFAULT_SPK_ID,
    "zm_yunyang": DEFAULT_SPK_ID,
    "中文女": DEFAULT_SPK_ID,
    # 注：repo 自带的 reference 是中文女声；'中文男' 等需要用户上传自己的 reference
    # （v0.7+ 加 /voices/add API 支持运行时注册）
    "中文男": DEFAULT_SPK_ID,
}


_cosyvoice: CosyVoice2 | None = None
_cosyvoice_voices: list[str] = []

# 单 GPU 模型的 inference 必须串行 —— CosyVoice2 并发调用时共享内部 state，
# 多路并发会导致音频输出混乱或 GPU state 污染。此 lock 包住每路推理全程，
# 确保任何时刻只一路在 GPU。N 路并发→排队。
_inference_lock: asyncio.Lock | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cosyvoice, _cosyvoice_voices, _inference_lock
    _inference_lock = asyncio.Lock()
    log.info("加载 CosyVoice2 模型: %s", MODEL_DIR)
    if not Path(MODEL_DIR, "llm.pt").exists():
        raise RuntimeError(
            f"模型文件 {MODEL_DIR}/llm.pt 不存在；entrypoint 应该已下载，"
            "检查 named volume 挂载与权限。"
        )
    t0 = time.time()
    _cosyvoice = await asyncio.to_thread(
        CosyVoice2, MODEL_DIR, load_jit=False, load_trt=False, fp16=True
    )
    log.info("CosyVoice2 加载完成 (%.1fs)", time.time() - t0)

    # 注册默认 zero-shot speaker → 之后 inference_sft 即可用 DEFAULT_SPK_ID
    if not Path(DEFAULT_PROMPT_WAV).exists():
        raise RuntimeError(
            f"参考音频 {DEFAULT_PROMPT_WAV} 不存在——image build 时 git clone "
            "应该已经包含 CosyVoice/asset/，检查 Dockerfile"
        )
    log.info("注册默认 SFT 音色 '%s'（reference: %s）",
             DEFAULT_SPK_ID, DEFAULT_PROMPT_WAV)
    t0 = time.time()
    await asyncio.to_thread(
        _cosyvoice.add_zero_shot_spk,
        DEFAULT_PROMPT_TEXT,
        DEFAULT_PROMPT_WAV,
        DEFAULT_SPK_ID,
    )
    log.info("默认音色注册完成 (%.1fs)", time.time() - t0)

    # 启动 warmup：跑一次完整 inference_zero_shot，丢弃输出。
    # 首次推理 LLM/flow/hifigan 内部 cache 还没进入稳态，warmup 拉热路径，
    # 让后续真实请求走稳态。
    log.info("启动 warmup 推理（丢弃输出）...")
    t0 = time.time()

    def _warmup() -> None:
        warmup_text = "系统启动预热中，本句仅用于初始化推理路径，输出会被丢弃。"
        for _ in _cosyvoice.inference_zero_shot(
            warmup_text,
            DEFAULT_PROMPT_TEXT,
            DEFAULT_PROMPT_WAV,
            zero_shot_spk_id=DEFAULT_SPK_ID,
            stream=True,
            speed=1.0,
        ):
            pass

    try:
        await asyncio.to_thread(_warmup)
        log.info("warmup 推理完成 (%.1fs)", time.time() - t0)
    except Exception:
        log.exception("warmup 推理失败（不影响启动，首次请求可能仍受影响）")

    # 用户上传的 reference 持久化目录（admin 注册的音色由 spk2info.pt 自动恢复，
    # 这里只放原始 wav 文件用于审计/重建）
    VOICES_WAV_DIR.mkdir(parents=True, exist_ok=True)

    _cosyvoice_voices = sorted(_cosyvoice.list_available_spks())
    log.info("可用 SFT 音色 (%d): %s", len(_cosyvoice_voices), _cosyvoice_voices)
    log.info("默认 voice=%s sample_rate=%d", DEFAULT_VOICE, SAMPLE_RATE)
    if ADMIN_API_KEY:
        log.info("admin endpoints 已启用 (TTS_ADMIN_API_KEY 设置)")
    else:
        log.info("admin endpoints 已禁用 (TTS_ADMIN_API_KEY 未设置)")

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


app = FastAPI(title="RTVoice TTS Server (CosyVoice 2)", version="0.19.0", lifespan=lifespan)

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

# SP10 G3 + G4
app.add_middleware(RequestMetricsMiddleware, service_name="tts-server")
add_bearer_security_scheme(app)
app.add_exception_handler(RequestValidationError, validation_exception_handler())

# Prometheus 指标（与 Kokoro 版同名，dashboard 不变）
SYNTH_PHRASES = Counter("rtvoice_tts_phrases_total", "Phrases synthesized")
SYNTH_FAILS = Counter("rtvoice_tts_failures_total", "Phrase synth failures")
TTFB = Histogram(
    "rtvoice_tts_ttfb_seconds",
    "Time to first PCM byte after request",
    buckets=(0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 5.0),
)
PHRASE_RTF = Histogram(
    "rtvoice_tts_phrase_rtf",
    "Per-phrase real-time factor (audio_seconds / synth_seconds)",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)
Instrumentator(excluded_handlers=["/health", "/metrics", "/v1/tts/stream"]).instrument(app).expose(app)


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice: str | None = Field(None, description="SFT 音色名 或 Kokoro 别名")
    lang: str | None = Field(None, description="忽略 (CosyVoice 自动判语言)")
    speed: float = Field(1.0, ge=0.5, le=2.0)


class VoicesListResponse(BaseModel):
    voices: list[str] = Field(..., description="可用 SFT 音色 ID 列表")


class AddVoiceResponse(BaseModel):
    spk_id: str
    voice_count: int = Field(..., description="注册后总音色数")


class DeleteVoiceResponse(BaseModel):
    spk_id: str
    deleted: bool
    voice_count: int


def _resolve_voice(voice: str | None) -> str:
    """Kokoro 别名 → CosyVoice SFT 音色名。"""
    if not voice:
        return DEFAULT_VOICE
    if voice in VOICE_ALIASES:
        return VOICE_ALIASES[voice]
    return voice  # 直接传 SFT 名


def _check_client_auth(authorization: str | None = Header(None)) -> None:
    """Legacy fallback (deprecated v0.6.2)；新代码走 require_key。"""
    if not RTVOICE_API_KEY:
        return  # dev：未设 key 跳过
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
        key = await verify_key(secret,
                               scope=request.app.state.scope,
                               store=request.app.state.key_store)
        # SP10 G3 — 喂 key_id 给 RequestMetricsMiddleware
        request.state.key_id = key.id
        return key
    except InvalidToken as e:
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        raise api_error(403, e.code, e.message)


def _ws_auth_ok(ws: WebSocket) -> bool:
    """WS 三路 Bearer：header / subprotocol / query。RTVOICE_API_KEY 空时通过。"""
    if not RTVOICE_API_KEY:
        return True
    proto = ws.headers.get("sec-websocket-protocol", "")
    for p in (s.strip() for s in proto.split(",")):
        if p.startswith("bearer.") and p[len("bearer."):] == RTVOICE_API_KEY:
            return True
    if ws.headers.get("authorization") == f"Bearer {RTVOICE_API_KEY}":
        return True
    if ws.query_params.get("token") == RTVOICE_API_KEY:
        return True
    return False


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if _cosyvoice is not None else "loading"}


@app.get("/info")
async def info() -> dict:
    return {
        "service": "tts-server",
        "version": "0.19.0",
        "capabilities": {
            "streaming": True,
            "text_streaming": True,
            "voice_clone": True,
            "subprotocol_bearer": True,
        },
        "models": {
            "tts": "CosyVoice2-0.5B",
            "backend": "cosyvoice2",
            "sample_rate": SAMPLE_RATE,
            "default_voice": DEFAULT_VOICE,
            "voice_count": len(_cosyvoice_voices),
        },
        "ready": _cosyvoice is not None,
    }


@app.get("/v1/voices", response_model=VoicesListResponse)
async def voices(key: Key = Depends(require_key)) -> VoicesListResponse:
    return VoicesListResponse(voices=_cosyvoice_voices)


def _tensor_to_pcm_bytes(samples: torch.Tensor) -> bytes:
    """CosyVoice 输出 float32 tensor [-1,1] → int16 LE bytes。"""
    if samples.dim() > 1:
        samples = samples.squeeze(0)
    samples = samples.detach().cpu().clamp(-1.0, 1.0)
    pcm = (samples * 32767.0).to(torch.int16).numpy().tobytes()
    return pcm


async def _synthesize_stream(req: TTSRequest, request: Request) -> AsyncIterator[bytes]:
    """流式合成：把 CosyVoice 同步生成器桥接到 asyncio。

    sync 生成器跑在线程里，每个 chunk 通过 Queue 推回 event loop。
    用 _inference_lock 串行化（CosyVoice 单 GPU 模型并发会污染 state）。
    """
    assert _cosyvoice is not None and _inference_lock is not None
    voice = _resolve_voice(req.voice)
    log.info("[TTS] voice=%s speed=%.2f text_len=%d (waiting lock)", voice, req.speed, len(req.text))

    async with _inference_lock:
        log.info("[TTS] lock acquired, start inference")
        async for chunk in _synthesize_stream_locked(req, request, voice):
            yield chunk


async def _synthesize_stream_locked(req: TTSRequest, request: Request, voice: str) -> AsyncIterator[bytes]:
    assert _cosyvoice is not None
    loop = asyncio.get_running_loop()
    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    error_holder: list[Exception] = []
    t_start = time.time()
    audio_samples_total = 0

    def producer():
        nonlocal audio_samples_total
        try:
            # 用 inference_zero_shot 而非 inference_sft：
            # - CosyVoice 2-0.5B 不带 SFT spk2info.pt（schema: 'embedding'）
            # - 启动时 add_zero_shot_spk 注册的是 zero-shot schema (llm_embedding/flow_embedding)
            # - inference_zero_shot 在 zero_shot_spk_id != '' 时走 spk2info 缓存路径，
            #   prompt_text/prompt_wav 此时被 frontend 忽略（仍需传值占位）
            for output in _cosyvoice.inference_zero_shot(
                req.text,
                DEFAULT_PROMPT_TEXT,    # 占位；spk_id 非空时不使用
                DEFAULT_PROMPT_WAV,     # 占位；spk_id 非空时不使用
                zero_shot_spk_id=voice,
                stream=True,
                speed=req.speed,
            ):
                samples = output["tts_speech"]
                audio_samples_total += samples.numel()
                pcm = _tensor_to_pcm_bytes(samples)
                # 跨线程把 chunk 推到 asyncio.Queue
                fut = asyncio.run_coroutine_threadsafe(chunk_queue.put(pcm), loop)
                fut.result()
        except Exception as e:
            log.exception("CosyVoice 合成失败")
            error_holder.append(e)
        finally:
            asyncio.run_coroutine_threadsafe(chunk_queue.put(None), loop).result()

    # 在线程池运行 sync 生成器
    producer_task = loop.run_in_executor(None, producer)

    first_chunk = True
    try:
        while True:
            if await request.is_disconnected():
                log.info("[TTS] client disconnected")
                return
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            if first_chunk:
                TTFB.observe(time.time() - t_start)
                first_chunk = False
            yield chunk
        if error_holder:
            SYNTH_FAILS.inc()
        else:
            SYNTH_PHRASES.inc()
            elapsed = time.time() - t_start
            audio_s = audio_samples_total / SAMPLE_RATE
            if elapsed > 0:
                PHRASE_RTF.observe(audio_s / elapsed)
            log.info("[TTS] done audio=%.2fs synth=%.1fs rtf=%.2fx",
                     audio_s, elapsed, audio_s / max(elapsed, 0.001))
    finally:
        # 确保 producer 结束（已通过 None 哨兵；但 client disconnect 路径需要）
        if not producer_task.done():
            try:
                await asyncio.wait_for(producer_task, timeout=2)
            except asyncio.TimeoutError:
                pass


@app.post(
    "/v1/tts/stream",
    responses={
        200: {
            "description": "PCM int16 LE 24kHz mono streaming audio",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
async def tts_stream(req: TTSRequest, request: Request,
                     key: Key = Depends(require_key)):
    if _cosyvoice is None:
        raise api_error(503, "tts.not_ready", "CosyVoice 尚未加载")
    voice = _resolve_voice(req.voice)
    if _cosyvoice_voices and voice not in _cosyvoice_voices:
        raise api_error(400, "tts.voice_not_found", f"未知音色 voice={req.voice!r} → {voice!r}; 可用={_cosyvoice_voices}")
    # SP10 G3 — per-key chars total
    try:
        TTS_CHARS_TOTAL.labels(key_id=safe_key_id(key)).inc(len(req.text or ""))
    except Exception:
        pass
    return StreamingResponse(
        _synthesize_stream(req, request),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Format": "pcm-int16-le",
        },
    )


# ---------------------------------------------------------------
# WebSocket /v1/tts/stream_ws — 双向流式（text-in 流入，audio-out 流出）
# ---------------------------------------------------------------
# 协议：
#   连接：       wss://host:9880/v1/tts/stream_ws
#   鉴权：       三路 Bearer（同 STT 风格）
#
#   client → server（按顺序）：
#     1) text frame: JSON metadata {"voice":"...", "speed":1.0, "lang":"cmn"}
#     2..N) text frame: 文本增量（任意长度，累积按句切分）
#     EOS:  text "EOS"  → 关闭文本流，等待最后 PCM
#
#   server → client：
#     binary frames: PCM int16 LE 24kHz mono chunks
#     {"type":"done"}    text frame，最后一帧（PCM 流结束后）
#     {"type":"error", "message":"..."}  text frame，异常即关

_SENT_BOUNDARY = "。！？!?；;…\n"


def _drain_sentences(buf: str) -> tuple[list[str], str]:
    """从累积缓冲里切出完整句子，返回 (完整句子列表, 剩余未完成片段)。"""
    sentences: list[str] = []
    start = 0
    for i, ch in enumerate(buf):
        if ch in _SENT_BOUNDARY:
            seg = buf[start:i + 1].strip()
            if seg:
                sentences.append(seg)
            start = i + 1
    return sentences, buf[start:]


@app.websocket("/v1/tts/stream_ws")
async def tts_stream_ws(ws: WebSocket) -> None:
    if _cosyvoice is None:
        await ws.close(code=1013, reason="CosyVoice 尚未加载")
        return
    if not _ws_auth_ok(ws):
        await ws.close(code=4401, reason="unauthorized")
        log.warning("[ws-tts] 鉴权失败 client=%s", ws.client)
        return
    await ws.accept(subprotocol=pick_bearer_subprotocol(ws))

    # 1) 接收 metadata
    try:
        meta_raw = await asyncio.wait_for(ws.receive_text(), timeout=5)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=4400, reason="metadata 超时或断连")
        return
    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        await ws.send_json({"type": "error", "message": "首帧必须是 JSON metadata"})
        await ws.close()
        return
    voice = _resolve_voice(meta.get("voice"))
    speed = float(meta.get("speed", 1.0))
    if _cosyvoice_voices and voice not in _cosyvoice_voices:
        await ws.send_json({"type": "error",
                            "message": f"未知音色 {meta.get('voice')!r}"})
        await ws.close()
        return

    log.info("[ws-tts] start voice=%s speed=%.2f (waiting lock)", voice, speed)

    assert _inference_lock is not None
    async with _inference_lock:
        log.info("[ws-tts] lock acquired")
        await _ws_inference(ws, voice, speed)


async def _ws_inference(ws: WebSocket, voice: str, speed: float) -> None:
    """按句缓冲 + str 流式合成。"""
    assert _cosyvoice is not None
    loop = asyncio.get_running_loop()
    delta_q: asyncio.Queue[str | None] = asyncio.Queue()
    disconnected = False

    async def reader():
        """读 ws 文本帧 → delta_q。EOS / disconnect → 推 None 收尾。"""
        nonlocal disconnected
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    disconnected = True
                    break
                text = msg.get("text")
                if text is None:
                    continue
                if text == "EOS":
                    break
                await delta_q.put(text)
        except Exception:
            log.exception("[ws-tts] reader 异常")
            disconnected = True
        finally:
            await delta_q.put(None)

    reader_task = asyncio.create_task(reader())

    def synth(sentence: str) -> list[bytes]:
        """单句 str 流式合成 → PCM 块列表。"""
        out: list[bytes] = []
        for output in _cosyvoice.inference_zero_shot(
            sentence,
            DEFAULT_PROMPT_TEXT,
            DEFAULT_PROMPT_WAV,
            zero_shot_spk_id=voice,
            stream=True,
            speed=speed,
        ):
            out.append(_tensor_to_pcm_bytes(output["tts_speech"]))
        return out

    sent_chunks = 0
    t_start = time.time()
    first = True
    errored = False
    pending = ""

    async def emit(sentence: str) -> bool:
        """合成并推送一句；返回 False 表示应停止（断连/错误）。"""
        nonlocal sent_chunks, first, errored
        if not sentence.strip() or disconnected:
            return not disconnected
        try:
            chunks = await loop.run_in_executor(None, synth, sentence)
        except Exception as e:
            log.exception("[ws-tts] 推理异常")
            try:
                await ws.send_json({"type": "error", "message": str(e)[:200]})
            except Exception:
                pass
            errored = True
            return False
        for pcm in chunks:
            if first:
                TTFB.observe(time.time() - t_start)
                first = False
            try:
                await ws.send_bytes(pcm)
                sent_chunks += 1
            except (WebSocketDisconnect, RuntimeError) as e:
                log.info("[ws-tts] client disconnected (%s)", type(e).__name__)
                return False
        return True

    try:
        while True:
            item = await delta_q.get()
            if item is None:
                if pending.strip() and not disconnected:
                    await emit(pending)
                break
            pending += item
            sentences, pending = _drain_sentences(pending)
            for s in sentences:
                if not await emit(s):
                    pending = ""
                    break
            else:
                continue
            break
        if not errored and not disconnected:
            try:
                await ws.send_json({"type": "done", "chunks": sent_chunks})
            except Exception:
                pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await ws.close()
        except Exception:
            pass
    SYNTH_PHRASES.inc()
    log.info("[ws-tts] done chunks=%d elapsed=%.1fs", sent_chunks, time.time() - t_start)


# ---------------------------------------------------------------
# Admin endpoints — 音色注册（POST/DELETE /voices/...）
# ---------------------------------------------------------------
# Bearer 鉴权：TTS_ADMIN_API_KEY 留空时拒绝（避免误开放）
# 注册路径：multipart/form-data 上传 wav + form 字段 spk_id + prompt_text
# 持久化：CosyVoice.save_spkinfo() 写 spk2info.pt 到 model dir（即 named volume），
#        启动 CosyVoice2.__init__ 自动 torch.load → 重启即恢复。
# 原始 wav 另存到 VOICES_WAV_DIR/<spk_id>.wav 便于审计/重建（同卷）

def _check_admin_auth(authorization: str | None = Header(None)) -> None:
    if not ADMIN_API_KEY:
        raise api_error(403, "auth.admin_disabled", "admin endpoints disabled (TTS_ADMIN_API_KEY 未设置)")
    expected = f"Bearer {ADMIN_API_KEY}"
    if authorization != expected:
        raise api_error(401, "auth.invalid_token", "invalid or missing Bearer token")


def _validate_spk_id(spk_id: str) -> str:
    spk_id = spk_id.strip()
    if not SPK_ID_RE.match(spk_id):
        raise api_error(400, "tts.invalid_spk_id", "spk_id 只允许字母数字下划线/中日韩字符/连字符，长度 1-64")
    return spk_id


@app.post("/v1/voices", status_code=201, response_model=AddVoiceResponse)
async def add_voice(
    spk_id: str = Form(..., description="新音色 ID（不能与现有冲突）"),
    prompt_text: str = Form(..., min_length=1, max_length=200,
                            description="参考音频对应的文本（≥3 秒发音）"),
    file: UploadFile = File(..., description="参考音频 wav (16kHz mono 推荐, 3-30 秒)"),
    _auth: None = Depends(_check_admin_auth),
) -> AddVoiceResponse:
    if _cosyvoice is None:
        raise api_error(503, "tts.not_ready", "CosyVoice 尚未加载")

    spk_id = _validate_spk_id(spk_id)
    if spk_id in _cosyvoice.list_available_spks():
        raise api_error(409, "tts.voice_already_exists", f"音色 {spk_id!r} 已存在；先 DELETE 再 POST")

    # 读到内存校验（5 MB 上限够 30 秒 16k mono 16-bit wav）
    raw = await file.read()
    if len(raw) > MAX_WAV_BYTES:
        raise api_error(413, "tts.wav_too_large", f"wav 超过 {MAX_WAV_BYTES} 字节上限")
    if len(raw) < 1024:
        raise api_error(400, "tts.invalid_wav", "wav 文件过小，疑似无效")

    # 持久化到 named volume；先写再注册，失败时清理
    wav_path = VOICES_WAV_DIR / f"{spk_id}.wav"
    wav_path.write_bytes(raw)

    try:
        # torchaudio 校验 + load 给 CosyVoice
        try:
            waveform, sr = torchaudio.load(str(wav_path))
        except Exception as e:
            raise api_error(400, "tts.wav_decode_failed", f"wav 解码失败：{e}")
        log.info("[admin] add voice spk_id=%s sr=%d duration=%.2fs",
                 spk_id, sr, waveform.shape[-1] / sr)

        # 调 CosyVoice 注册（同步 → 跑线程，避免阻塞 event loop）
        await asyncio.to_thread(
            _cosyvoice.add_zero_shot_spk, prompt_text, str(wav_path), spk_id
        )
        # 持久化 spk2info.pt 到 MODEL_DIR（重启自动 reload）
        await asyncio.to_thread(_cosyvoice.save_spkinfo)
    except HTTPException:
        wav_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        wav_path.unlink(missing_ok=True)
        log.exception("[admin] add voice 失败")
        raise api_error(500, "tts.register_failed", f"注册失败：{e}")

    # 刷新 voices 列表
    global _cosyvoice_voices
    _cosyvoice_voices = sorted(_cosyvoice.list_available_spks())
    return AddVoiceResponse(spk_id=spk_id, voice_count=len(_cosyvoice_voices))


@app.delete("/v1/voices/{spk_id}", response_model=DeleteVoiceResponse)
async def delete_voice(
    spk_id: str,
    _auth: None = Depends(_check_admin_auth),
) -> DeleteVoiceResponse:
    if _cosyvoice is None:
        raise api_error(503, "tts.not_ready", "CosyVoice 尚未加载")
    spk_id = _validate_spk_id(spk_id)
    if spk_id == DEFAULT_SPK_ID:
        raise api_error(400, "tts.default_voice_protected", f"默认音色 {DEFAULT_SPK_ID!r} 不可删除")
    if spk_id not in _cosyvoice.list_available_spks():
        raise api_error(404, "tts.voice_not_found", f"音色 {spk_id!r} 不存在")

    # 从 frontend.spk2info pop（CosyVoice 没暴露 delete API，直接操作 dict）
    spk2info = _cosyvoice.frontend.spk2info
    spk2info.pop(spk_id, None)
    await asyncio.to_thread(_cosyvoice.save_spkinfo)

    # 删除原始 wav（如果有）
    wav_path = VOICES_WAV_DIR / f"{spk_id}.wav"
    wav_path.unlink(missing_ok=True)

    global _cosyvoice_voices
    _cosyvoice_voices = sorted(_cosyvoice.list_available_spks())
    log.info("[admin] deleted voice spk_id=%s", spk_id)
    return DeleteVoiceResponse(spk_id=spk_id, deleted=True, voice_count=len(_cosyvoice_voices))
