"""RTVoice TTS Server — Fun-CosyVoice 3 GPU 变体 (v0.7.0)

后端：Fun-CosyVoice3-0.5B-2512 (HF/ModelScope: FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
模型 ~5.6GB（不含可选 llm.rl.pt 2GB），fp16 GPU
前端：与 v0.6 (CosyVoice2) 协议完全相同
    POST /tts/stream  body={text, voice, lang?, speed?}
    → chunked PCM int16 LE 24kHz mono

v3 vs v2 关键差异：
    - class CosyVoice3 继承自 CosyVoice2；公开方法签名完全相同
    - 构造器去掉 load_jit 参数（v3 dropped JIT path）
    - inference_zero_shot 内部支持 tts_text=Generator 实现 text-in 流式
      （RTVoice 当前传 str；以后切 generator 即享 150ms 端到端延迟）
    - 模型文件：cosyvoice2.yaml→cosyvoice3.yaml,
      speech_tokenizer_v2.onnx→_v3.onnx；llm/flow/hift.pt 名字不变

切换：.env 设置
    TTS_DOCKERFILE=Dockerfile.cosyvoice3
    TTS_IMAGE=rtvoice/tts-server-cosyvoice3:v0.7.0
回滚：恢复 v0.6 配置即可，images/volumes 各自独立。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import json
import queue as sync_queue
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

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402
from fastapi.exceptions import RequestValidationError
from app.error_schema import ErrorResponse, api_error, http_exception_handler, validation_exception_handler
from typing import Annotated

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy
from rtvoice_auth.ws import pick_bearer_subprotocol

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.tts.cosyvoice3")

MODEL_DIR = os.environ.get(
    "MODEL_DIR", "/opt/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B-2512"
)
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", "/opt/CosyVoice")
SAMPLE_RATE = 24000

# CosyVoice 2-0.5B 不自带 SFT 音色，启动时用 repo 自带 reference 注册一个
DEFAULT_SPK_ID = "default_zh_female"
DEFAULT_PROMPT_WAV = os.path.join(COSYVOICE_DIR, "asset/zero_shot_prompt.wav")
# 此参考音频对应的文本（CosyVoice repo runtime/python/fastapi/client.py 默认值）
# v3 关键差异：LLM `inference()` 硬断言输入序列中含 <|endofprompt|>（token 151646）；
# v3 frontend.text_normalize / _extract_text_token 都不自动添加；caller 必须在
# prompt_text 末尾显式拼上。v2 不要求。
DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。<|endofprompt|>"

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
import re
SPK_ID_RE = re.compile(r"^[\w\u4e00-\u9fff\u3040-\u30ff-]{1,64}$")

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


_cosyvoice: CosyVoice3 | None = None
_cosyvoice_voices: list[str] = []

# v0.7.2: 单 GPU 模型的 inference 必须串行 —— CosyVoice 3 的 model.tts() 在并发
# 调用时共享内部 state（hift/flow LLM token 池）会被对方覆盖，prod G1 测试出现
# 5 路并发时 2 路输出 0.04s 空音频。此 lock 包住每路推理全程（包括 stream 读
# pcm_q），确保任何时刻只一路在 GPU。N 路并发→排队，吞吐受限但正确性保证。
# 后续优化方向：CosyVoice batching API（如果上游开放）。
_inference_lock: asyncio.Lock | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cosyvoice, _cosyvoice_voices, _inference_lock
    _inference_lock = asyncio.Lock()
    log.info("加载 CosyVoice3 模型: %s", MODEL_DIR)
    if not Path(MODEL_DIR, "llm.pt").exists():
        raise RuntimeError(
            f"模型文件 {MODEL_DIR}/llm.pt 不存在；entrypoint 应该已下载，"
            "检查 named volume 挂载与权限。"
        )
    t0 = time.time()
    # v3 构造器：去掉 load_jit；保留 load_trt=False（TensorRT 推理可选）+ fp16=True
    _cosyvoice = await asyncio.to_thread(
        CosyVoice3, MODEL_DIR, load_trt=False, fp16=True
    )
    log.info("CosyVoice3 加载完成 (%.1fs)", time.time() - t0)

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


app = FastAPI(title="RTVoice TTS Server (Fun-CosyVoice 3)", version="0.14.0", lifespan=lifespan)

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
        return await verify_key(secret,
                                scope=request.app.state.scope,
                                store=request.app.state.key_store)
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
        "backend": "cosyvoice3",
        "model": "Fun-CosyVoice3-0.5B-2512",
        "sample_rate": SAMPLE_RATE,
        "default_voice": DEFAULT_VOICE,
        "voice_count": len(_cosyvoice_voices),
        "ready": _cosyvoice is not None,
        # agent-worker 探测此字段决定走 ws 流式还是单次 HTTP（v0.6 路径）
        "text_streaming": True,
    }


@app.get("/v1/voices")
async def voices(key: Key = Depends(require_key)) -> dict:
    return {"voices": _cosyvoice_voices}


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
    v0.7.2：用 _inference_lock 串行化（CosyVoice 单 GPU 模型并发会污染 state）。
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
    # v0.7.3: reset model.token_hop_len = 25。CosyVoice 3 model.tts() 内部用
    # `self.token_hop_len = min(token_max_hop_len, hop * scale_factor)` 单调递增，
    # 跨 inference 共享。第二路起 hop_len 已涨到 100，短文本 yield 不出（while
    # 永远不满足条件） → 跳到 finalize 输出 ~40ms 残尾。手动 reset 确保每路
    # 从 token_hop_len=25 重新开始。
    _cosyvoice.model.token_hop_len = 25
    loop = asyncio.get_running_loop()
    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    error_holder: list[Exception] = []
    t_start = time.time()
    audio_samples_total = 0

    # v0.7.3：HTTP path 用 single-element generator 喂 v3（不传 str）。
    # str input 走 v3 内部 model.tts(stream=True) 的"满足 token_hop_len 才 yield"
    # 路径，短文本会触发"yield 0.04s 残尾"+ 偶尔 hifigan F0 kernel/input mismatch
    # 的 deep bug。走 generator 路径更稳（A 测 5/5 验过）。
    def text_gen():
        yield req.text

    def producer():
        nonlocal audio_samples_total
        try:
            for output in _cosyvoice.inference_zero_shot(
                text_gen(),
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


@app.post("/v1/tts/stream")
async def tts_stream(req: TTSRequest, request: Request,
                     key: Key = Depends(require_key)):
    if _cosyvoice is None:
        raise api_error(503, "tts.not_ready", "CosyVoice 尚未加载")
    voice = _resolve_voice(req.voice)
    if _cosyvoice_voices and voice not in _cosyvoice_voices:
        raise api_error(400, "tts.voice_not_found", f"未知音色 voice={req.voice!r} → {voice!r}; 可用={_cosyvoice_voices}")
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
# WebSocket /tts/stream_ws — v0.7 双向流式（text-in 流入，audio-out 流出）
# ---------------------------------------------------------------
# 协议：
#   连接：       wss://host:9880/tts/stream_ws
#   鉴权：       三路 Bearer（同 STT 风格）
#
#   client → server（按顺序）：
#     1) text frame: JSON metadata {"voice":"...", "speed":1.0, "lang":"cmn"}
#     2..N) text frame: 文本增量（任意长度，CosyVoice 内部累积）
#     EOS:  text "EOS"  → 关闭文本流，等待最后 PCM
#
#   server → client：
#     binary frames: PCM int16 LE 24kHz mono chunks
#     {"type":"done"}    text frame，最后一帧（PCM 流结束后）
#     {"type":"error", "message":"..."}  text frame，异常即关
#
# barge-in：
#   client 直接 close ws → server 检测 disconnect → text_q.put(None) → 生成器
#   提前结束 → CosyVoice 内部停止后续 token 处理（依赖 v3 实现停损延迟）

class _SendError:
    def __init__(self, msg: str):
        self.msg = msg


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

    # v0.7.2: 串行化整段流式（含 reader/producer），防止并发污染 model state
    assert _inference_lock is not None
    async with _inference_lock:
        log.info("[ws-tts] lock acquired")
        await _ws_inference(ws, voice, speed)


async def _ws_inference(ws: WebSocket, voice: str, speed: float) -> None:
    assert _cosyvoice is not None
    # 同 _synthesize_stream_locked：reset token_hop_len 防累积污染（见上方注释）
    _cosyvoice.model.token_hop_len = 25
    loop = asyncio.get_running_loop()
    text_q: sync_queue.Queue = sync_queue.Queue()  # async ws → sync gen
    pcm_q: asyncio.Queue = asyncio.Queue()         # sync prod → async send

    def text_gen():
        """喂给 inference_zero_shot 的同步 generator。"""
        while True:
            item = text_q.get()
            if item is None:
                return
            yield item

    def producer():
        try:
            for output in _cosyvoice.inference_zero_shot(
                text_gen(),
                DEFAULT_PROMPT_TEXT,
                DEFAULT_PROMPT_WAV,
                zero_shot_spk_id=voice,
                stream=True,
                speed=speed,
            ):
                pcm = _tensor_to_pcm_bytes(output["tts_speech"])
                asyncio.run_coroutine_threadsafe(pcm_q.put(pcm), loop).result()
        except Exception as e:
            log.exception("[ws-tts] 推理异常")
            asyncio.run_coroutine_threadsafe(
                pcm_q.put(_SendError(str(e))), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(pcm_q.put(None), loop).result()

    inference_fut = loop.run_in_executor(None, producer)

    async def reader():
        """读 ws 文本帧 → text_q。EOS / disconnect → 关闭 generator。"""
        try:
            while True:
                msg = await ws.receive()
                t = msg.get("type")
                if t == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text is None:
                    continue
                if text == "EOS":
                    break
                text_q.put(text)
        except Exception:
            log.exception("[ws-tts] reader 异常")
        finally:
            text_q.put(None)  # 关闭 sync generator

    reader_task = asyncio.create_task(reader())

    # 3) 主循环：pcm_q → ws.send_bytes
    sent_chunks = 0
    t_start = time.time()
    first = True
    try:
        while True:
            item = await pcm_q.get()
            if item is None:
                break
            if isinstance(item, _SendError):
                try:
                    await ws.send_json({"type": "error", "message": item.msg})
                except Exception:
                    pass
                break
            if first:
                TTFB.observe(time.time() - t_start)
                first = False
            try:
                await ws.send_bytes(item)
                sent_chunks += 1
            except (WebSocketDisconnect, RuntimeError) as e:
                # starlette 在 client close 后再 send 抛 RuntimeError 而非
                # WebSocketDisconnect — 都按"对端已断"处理
                log.info("[ws-tts] client disconnected (%s)", type(e).__name__)
                break
        try:
            await ws.send_json({"type": "done", "chunks": sent_chunks})
        except Exception:
            pass
    finally:
        text_q.put(None)
        reader_task.cancel()
        try:
            await asyncio.wait_for(inference_fut, timeout=5)
        except (asyncio.TimeoutError, Exception):
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


@app.post("/v1/voices", status_code=201)
async def add_voice(
    spk_id: str = Form(..., description="新音色 ID（不能与现有冲突）"),
    prompt_text: str = Form(..., min_length=1, max_length=200,
                            description="参考音频对应的文本（≥3 秒发音）"),
    file: UploadFile = File(..., description="参考音频 wav (16kHz mono 推荐, 3-30 秒)"),
    _auth: None = Depends(_check_admin_auth),
) -> dict:
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
    return {"spk_id": spk_id, "voice_count": len(_cosyvoice_voices)}


@app.delete("/v1/voices/{spk_id}")
async def delete_voice(
    spk_id: str,
    _auth: None = Depends(_check_admin_auth),
) -> dict:
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
    return {"spk_id": spk_id, "deleted": True, "voice_count": len(_cosyvoice_voices)}


