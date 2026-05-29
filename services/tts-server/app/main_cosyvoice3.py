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
from rtvoice_auth.instrumentation import RequestMetricsMiddleware
from rtvoice_auth.openapi import add_bearer_security_scheme
from rtvoice_auth.metrics import TTS_CHARS_TOTAL
from rtvoice_auth.metrics_labels import safe_key_id

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
# 此参考音频对应的文本。v3 LLM 训练时的格式约定（example.py:76 / triton runtime
# model.py:260-261 自动前置）：
#   "<system 指令><|endofprompt|><参考音频 transcript>"
# bistream 路径在 llm.py:588-590 按 <|endofprompt|>（token 151646）切：
# 之前作为 system prompt，之后才是真正的参考 transcript（与 prompt_speech_token
# 对齐用）。如果分隔符放在末尾，LLM 看到的"参考 transcript"为空，与 prompt
# speech token 完全失配 → 退化为照抄参考音频前缀，导致用户听到的 prompt 残留。
DEFAULT_PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"

DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", DEFAULT_SPK_ID)

# 用户上传的 reference wav 持久化目录（与模型同 named volume，重启后保留）
VOICES_WAV_DIR = Path(MODEL_DIR).parent / "voices"

# Client Bearer 鉴权（v0.6.1）：保护 /tts/stream + GET /voices
# 留空 = 鉴权关闭（dev 默认）；prod 暴露公网时必填
RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip()

# Admin endpoints (POST/DELETE /voices/...) Bearer 鉴权（独立 key，权限更高）
# 留空 = 禁用 admin endpoints（防止误开放）
ADMIN_API_KEY = os.environ.get("TTS_ADMIN_API_KEY", "").strip()

# 上传音频大小上限。支持任意格式（torchaudio 自动解码），内部规范化为 16kHz mono。
# 30s 32kHz 立体声 16-bit WAV ≈ 3.8MB；留 10MB 余量应对高采样率/24-bit 输入。
MAX_WAV_BYTES = int(os.environ.get("TTS_MAX_WAV_BYTES", str(10 * 1024 * 1024)))

# 参考音频规范化目标采样率（Hz）。CosyVoice 内部用 16kHz。
VOICE_TARGET_SR = int(os.environ.get("TTS_VOICE_TARGET_SR", "16000"))

# 参考音频截断上限（秒）。>8s 的 prompt_speech_token 序列在 12GB 显存环境下
# 会导致 LLM attention 显存消耗激增（自定义音色 OOM 的根因）。
# CosyVoice 官方推荐 3-10s；8s 是安全中点：音色质量好，显存占用可控。
MAX_PROMPT_DURATION_S = float(os.environ.get("TTS_MAX_PROMPT_DURATION_S", "8.0"))

# 静音检测阈值（RMS，线性）。约 -40 dBFS，低于此视为静音/噪底。
SILENCE_RMS_THRESHOLD = float(os.environ.get("TTS_SILENCE_RMS_THRESHOLD", "0.01"))

# 静音检测窗口大小（毫秒）。
SILENCE_WINDOW_MS = int(os.environ.get("TTS_SILENCE_WINDOW_MS", "20"))

# 最多从音频头部去除的静音时长（秒）。超过此段若仍无语音则放弃静音裁剪。
MAX_SILENCE_TRIM_S = float(os.environ.get("TTS_MAX_SILENCE_TRIM_S", "5.0"))

# 单次合成超时（秒）。CosyVoice 内部 llm_job 线程 OOM 崩溃时不会向外传播异常，
# 外层 inference_zero_shot 会永久阻塞。此超时用于检测这种死锁并强制中断，
# 防止 _inference_lock 被永久持有。120s 足以覆盖最长的合理推理时间。
SYNTH_TIMEOUT_S = float(os.environ.get("TTS_SYNTH_TIMEOUT_S", "120.0"))

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


def _normalize_voice_audio(
    waveform: torch.Tensor,
    sr: int,
    target_sr: int = VOICE_TARGET_SR,
    target_duration_s: float = MAX_PROMPT_DURATION_S,
    silence_rms: float = SILENCE_RMS_THRESHOLD,
    window_ms: int = SILENCE_WINDOW_MS,
    max_trim_s: float = MAX_SILENCE_TRIM_S,
) -> tuple[torch.Tensor, float, float]:
    """规范化参考音频：多声道→单声道 → 重采样 → 去前导静音 → 截断。

    Returns:
        (normalized_waveform, original_duration_s, effective_duration_s)
    """
    original_duration = waveform.shape[-1] / sr

    # 多声道 → 单声道（平均）
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # 重采样到目标采样率
    if sr != target_sr:
        resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=target_sr)
        waveform = resampler(waveform)

    # 去除前导静音/噪声（基于 RMS 能量帧检测）
    audio = waveform[0]
    window_size = max(1, int(target_sr * window_ms / 1000))
    max_trim_samples = int(target_sr * max_trim_s)
    step = max(1, window_size // 2)

    start_sample = 0
    for i in range(0, min(len(audio) - window_size, max_trim_samples), step):
        frame = audio[i : i + window_size]
        rms = float(torch.sqrt(torch.mean(frame ** 2)))
        if rms > silence_rms:
            # 退一个窗口，避免切到语音起始瞬态
            start_sample = max(0, i - window_size)
            break

    if start_sample > 0:
        waveform = waveform[:, start_sample:]

    # 截断到目标时长
    max_samples = int(target_sr * target_duration_s)
    if waveform.shape[-1] > max_samples:
        waveform = waveform[:, :max_samples]

    effective_duration = waveform.shape[-1] / target_sr
    return waveform, original_duration, effective_duration


def _truncate_text_for_duration(
    text: str, original_duration: float, effective_duration: float
) -> str:
    """按时长比例截断文本，保留与 effective_duration 对应的字符数。"""
    if original_duration <= 0 or effective_duration >= original_duration:
        return text
    ratio = effective_duration / original_duration
    char_count = max(1, int(len(text) * ratio))
    return text[:char_count]


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

    # 启动 warmup：跑一次完整 inference_zero_shot，丢弃输出。
    # 首次推理 LLM/flow/hifigan 内部 cache 还没进入稳态，短文本会让 prompt
    # 参考音频 token 透出（用户实测 default text="你好，这是 RTVoice TTS 测试。"
    # 首次返回 prompt 音频"希望你以后能够做的比我还好呦"）。
    # warmup 用一段长且与 prompt 无关的文本拉热 path，让后续真实请求走稳态。
    log.info("启动 warmup 推理（丢弃输出）...")
    t0 = time.time()

    def _warmup() -> None:
        warmup_text = "系统启动预热中，本句仅用于初始化推理路径，输出会被丢弃。"
        _cosyvoice.model.token_hop_len = 25
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


app = FastAPI(title="RTVoice TTS Server (Fun-CosyVoice 3)", version="0.19.0", lifespan=lifespan)

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


# SP11 T2 — G4 response_model 补全（D2 finding：之前 dict → openapi Record<str,any>）
class VoicesListResponse(BaseModel):
    voices: list[str] = Field(..., description="可用 SFT 音色 ID 列表")


class AddVoiceResponse(BaseModel):
    spk_id: str
    voice_count: int = Field(..., description="注册后总音色数")
    original_duration: float = Field(..., description="上传音频原始时长（秒）")
    effective_duration: float = Field(..., description="实际注册使用的音频时长（秒，去除静音后截断）")
    effective_text: str = Field(..., description="实际注册使用的文本（按时长比例截断后）")


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
    # SP10 G4 — 4 service /info 统一返 service/version/capabilities/models
    return {
        "service": "tts-server",
        "version": "0.19.0",
        "capabilities": {
            "streaming": True,
            "text_streaming": True,        # agent-worker 探测此字段决定走 ws 流式
            "voice_clone": True,
            "subprotocol_bearer": True,
        },
        "models": {
            "tts": "Fun-CosyVoice3-0.5B-2512",
            "backend": "cosyvoice3",
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

    # v0.7.4：HTTP path 改回 str + stream=False。
    # 上一版（v0.7.3）走 single-element generator 触发 v3 LLM 的 inference_bistream
    # 路径；短文本（≈14 字）下 mix-mode 阶段 prompt_speech_token_emb 还没消耗完
    # 就进入 final_decode，导致首批输出 token 沿用 prompt 风格→生成的 mel 听感
    # 上携带参考音频"能够做到比我还好哟"前缀（用户 P0 反馈）。
    # str 输入走 llm.inference()（非 bistream），prompt_speech_token 全段作为
    # 上下文一次性喂入，LLM 直接从 task_id_emb 之后开始生成目标 token。
    # stream=False 绕过 model.tts 内 token_hop_len 流式切片（即上一版顾虑的
    # "yield 0.04s 残尾 / hifigan F0 mismatch"），单 sentence 一次性合成、整段
    # 返回；外层 _synthesize_stream 仍以 chunk 形式 yield 给 client。
    def producer():
        nonlocal audio_samples_total
        try:
            for output in _cosyvoice.inference_zero_shot(
                req.text,
                DEFAULT_PROMPT_TEXT,    # 占位；spk_id 非空时不使用
                DEFAULT_PROMPT_WAV,     # 占位；spk_id 非空时不使用
                zero_shot_spk_id=voice,
                stream=False,
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
            try:
                chunk = await asyncio.wait_for(chunk_queue.get(), timeout=SYNTH_TIMEOUT_S)
            except asyncio.TimeoutError:
                # llm_job 线程 OOM 崩溃时不向外传播异常，inference_zero_shot 永久阻塞。
                # 超时意味着 producer() 已死锁——记录错误、释放 lock，避免锁死整个服务。
                log.error("[TTS] 合成超时 (%.0fs)，疑似 CosyVoice llm_job 线程崩溃，释放锁", SYNTH_TIMEOUT_S)
                SYNTH_FAILS.inc()
                return
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
        # 方案A：每次推理后主动释放 CUDA fragment，防止自定义音色长 token 序列
        # 积累的碎片在 12GB 显存上触发 OOM
        torch.cuda.empty_cache()


@app.post(
    "/v1/tts/stream",
    # SP11 T2 — binary audio response 用 responses 显式声明 content-type（response_model 不适用）
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
#   client 直接 close ws → server 检测 disconnect → 停止后续句子合成

# 句子切分：在强终止标点处断句，逐句送入干净的 str 合成路径。
# 见 _ws_inference 注释——bistream（generator+stream=True）会泄漏参考音频前缀。
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

    # v0.7.2: 串行化整段流式（含 reader/producer），防止并发污染 model state
    assert _inference_lock is not None
    async with _inference_lock:
        log.info("[ws-tts] lock acquired")
        await _ws_inference(ws, voice, speed)


async def _ws_inference(ws: WebSocket, voice: str, speed: float) -> None:
    """按句缓冲 + str 单句合成。

    旧实现把 text_gen() 直接喂给 inference_zero_shot(stream=True)，走 v3 的
    inference_bistream 路径——开头若干 token 沿用参考音频风格，会把默认音色的
    参考 transcript（“希望你以后…做的比我还好呦”）前缀泄漏进合成音频（用户 P0）。
    HTTP path 早在 v0.7.4 改用 str + stream=False 绕过，但本 WS path 一直漏改。

    现改为：累积流入文本，遇到句末标点就把完整句子用 str + stream=False（与
    _synthesize_stream_locked 同款干净路径）逐句合成、立即推音频；EOS/断连时
    flush 残余。既根治前缀泄漏，又保留逐句出声的流式体验。
    """
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
        """单句 str 合成（stream=False，非 bistream）→ PCM 块列表。"""
        # 每句 reset token_hop_len，防跨 inference 累积污染（见 _synthesize_stream_locked）
        _cosyvoice.model.token_hop_len = 25
        out: list[bytes] = []
        try:
            for output in _cosyvoice.inference_zero_shot(
                sentence,
                DEFAULT_PROMPT_TEXT,    # 占位；spk_id 非空时不使用
                DEFAULT_PROMPT_WAV,     # 占位；spk_id 非空时不使用
                zero_shot_spk_id=voice,
                stream=False,
                speed=speed,
            ):
                out.append(_tensor_to_pcm_bytes(output["tts_speech"]))
        finally:
            torch.cuda.empty_cache()
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
            chunks = await asyncio.wait_for(
                loop.run_in_executor(None, synth, sentence),
                timeout=SYNTH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.error("[ws-tts] 合成超时 (%.0fs)，疑似 CosyVoice llm_job 线程崩溃", SYNTH_TIMEOUT_S)
            try:
                await ws.send_json({"type": "error", "message": "合成超时，服务将在下次请求后自动恢复"})
            except Exception:
                pass
            errored = True
            return False
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
                # EOS / 断连：flush 残余片段（断连则丢弃，无人接收）
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
    prompt_text: str = Form(..., min_length=1, max_length=1000,
                            description="参考音频对应的完整文本（系统自动按时长比例截断）"),
    file: UploadFile = File(..., description="参考音频（任意格式，3–30 秒；系统自动规范化为 16kHz mono 8s）"),
    _auth: None = Depends(_check_admin_auth),
) -> AddVoiceResponse:
    if _cosyvoice is None:
        raise api_error(503, "tts.not_ready", "CosyVoice 尚未加载")

    spk_id = _validate_spk_id(spk_id)
    if spk_id in _cosyvoice.list_available_spks():
        raise api_error(409, "tts.voice_already_exists", f"音色 {spk_id!r} 已存在；先 DELETE 再 POST")

    # 读到内存校验
    raw = await file.read()
    if len(raw) > MAX_WAV_BYTES:
        raise api_error(413, "tts.wav_too_large", f"音频超过 {MAX_WAV_BYTES // (1024*1024)} MB 上限")
    if len(raw) < 1024:
        raise api_error(400, "tts.invalid_wav", "音频文件过小，疑似无效")

    # 先写原始文件（torchaudio 支持从文件路径解码各种格式）
    wav_path = VOICES_WAV_DIR / f"{spk_id}.wav"
    raw_path = VOICES_WAV_DIR / f"{spk_id}.orig"
    raw_path.write_bytes(raw)

    try:
        # 解码原始音频（支持 wav/mp3/flac/ogg 等 torchaudio 支持的格式）
        try:
            waveform, sr = torchaudio.load(str(raw_path))
        except Exception as e:
            raise api_error(400, "tts.wav_decode_failed", f"音频解码失败：{e}")

        log.info("[admin] add voice spk_id=%s sr=%d ch=%d duration=%.2fs",
                 spk_id, sr, waveform.shape[0], waveform.shape[-1] / sr)

        # 规范化：单声道 + 重采样到 16kHz + 去前导静音 + 截断到 8s
        waveform, original_duration, effective_duration = await asyncio.to_thread(
            _normalize_voice_audio, waveform, sr
        )
        log.info("[admin] normalized: original=%.2fs effective=%.2fs sr=%d mono",
                 original_duration, effective_duration, VOICE_TARGET_SR)

        # 保存规范化后的 WAV（16kHz mono），供 CosyVoice 注册和审计
        torchaudio.save(str(wav_path), waveform, VOICE_TARGET_SR, encoding="PCM_S", bits_per_sample=16)

        # 清理原始文件
        raw_path.unlink(missing_ok=True)

        # 按时长比例截断文本
        effective_text = _truncate_text_for_duration(prompt_text, original_duration, effective_duration)
        log.info("[admin] text truncated: %d→%d chars", len(prompt_text), len(effective_text))

        # v3 约定：prompt_text 必须形如 "<system><|endofprompt|><transcript>"
        full_prompt_text = (
            effective_text if "<|endofprompt|>" in effective_text
            else f"You are a helpful assistant.<|endofprompt|>{effective_text}"
        )

        # 调 CosyVoice 注册（同步 → 跑线程，避免阻塞 event loop）
        await asyncio.to_thread(
            _cosyvoice.add_zero_shot_spk, full_prompt_text, str(wav_path), spk_id
        )
        # 持久化 spk2info.pt 到 MODEL_DIR（重启自动 reload）
        await asyncio.to_thread(_cosyvoice.save_spkinfo)
    except HTTPException:
        wav_path.unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        wav_path.unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        log.exception("[admin] add voice 失败")
        raise api_error(500, "tts.register_failed", f"注册失败：{e}")

    # 预热新音色：首次推理会把 reference audio token 泄漏到输出，预热丢弃该输出
    # 与启动预热逻辑保持一致（stream=True + token_hop_len=25 + _inference_lock）
    _warmup_spk_id = spk_id

    def _warmup_new_voice() -> None:
        _cosyvoice.model.token_hop_len = 25
        for _ in _cosyvoice.inference_zero_shot(
            "系统预热，本句仅用于初始化推理路径，输出会被丢弃。",
            DEFAULT_PROMPT_TEXT,
            DEFAULT_PROMPT_WAV,
            zero_shot_spk_id=_warmup_spk_id,
            stream=True,
            speed=1.0,
        ):
            pass

    try:
        t_warmup = time.time()
        async with _inference_lock:
            await asyncio.to_thread(_warmup_new_voice)
        log.info("[admin] voice %s warmup done (%.1fs)", spk_id, time.time() - t_warmup)
    except Exception:
        log.exception("[admin] voice %s warmup failed (non-fatal)", spk_id)

    # 刷新 voices 列表
    global _cosyvoice_voices
    _cosyvoice_voices = sorted(_cosyvoice.list_available_spks())
    return AddVoiceResponse(
        spk_id=spk_id,
        voice_count=len(_cosyvoice_voices),
        original_duration=round(original_duration, 2),
        effective_duration=round(effective_duration, 2),
        effective_text=effective_text,
    )


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


