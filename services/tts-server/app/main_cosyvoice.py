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
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

# 让 Python 找到 CosyVoice 模块
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", "/opt/CosyVoice")
sys.path.insert(0, COSYVOICE_DIR)
sys.path.insert(0, os.path.join(COSYVOICE_DIR, "third_party/Matcha-TTS"))

from cosyvoice.cli.cosyvoice import CosyVoice2  # noqa: E402

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

    _cosyvoice_voices = sorted(_cosyvoice.list_available_spks())
    log.info("可用 SFT 音色 (%d): %s", len(_cosyvoice_voices), _cosyvoice_voices)
    log.info("默认 voice=%s sample_rate=%d", DEFAULT_VOICE, SAMPLE_RATE)
    yield
    log.info("shutdown")


app = FastAPI(title="RTVoice TTS Server (CosyVoice 2)", version="0.6.0", lifespan=lifespan)

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
Instrumentator(excluded_handlers=["/health", "/metrics", "/tts/stream"]).instrument(app).expose(app)


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


@app.get("/voices")
async def voices() -> dict:
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


@app.post("/tts/stream")
async def tts_stream(req: TTSRequest, request: Request):
    if _cosyvoice is None:
        raise HTTPException(503, "CosyVoice 尚未加载")
    voice = _resolve_voice(req.voice)
    if _cosyvoice_voices and voice not in _cosyvoice_voices:
        raise HTTPException(
            400, f"未知音色 voice={req.voice!r} → {voice!r}; 可用={_cosyvoice_voices}"
        )
    return StreamingResponse(
        _synthesize_stream(req, request),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Channels": "1",
            "X-Format": "pcm-int16-le",
        },
    )


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
