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
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
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


_cosyvoice: CosyVoice2 | None = None
_cosyvoice_voices: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cosyvoice, _cosyvoice_voices
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
    yield
    log.info("shutdown")


app = FastAPI(title="RTVoice TTS Server (CosyVoice 2)", version="0.6.0", lifespan=lifespan)
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
    """Bearer 鉴权（client tier）：保护 inference endpoints。"""
    if not RTVOICE_API_KEY:
        return  # dev：未设 key 跳过
    if authorization != f"Bearer {RTVOICE_API_KEY}":
        raise api_error(401, "auth.invalid_token", "invalid or missing Bearer token")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if _cosyvoice is not None else "loading"}


@app.get("/info")
async def info() -> dict:
    return {
        "backend": "cosyvoice2",
        "model": "CosyVoice2-0.5B",
        "sample_rate": SAMPLE_RATE,
        "default_voice": DEFAULT_VOICE,
        "voice_count": len(_cosyvoice_voices),
        "ready": _cosyvoice is not None,
    }


@app.get("/v1/voices")
async def voices(_auth: None = Depends(_check_client_auth)) -> dict:
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
    """
    assert _cosyvoice is not None
    voice = _resolve_voice(req.voice)
    log.info("[TTS] voice=%s speed=%.2f text_len=%d", voice, req.speed, len(req.text))

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


@app.post("/v1/tts/stream")
async def tts_stream(req: TTSRequest, request: Request,
                     _auth: None = Depends(_check_client_auth)):
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


