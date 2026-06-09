"""RTVoice STT Server.

后端：sherpa-onnx + 中文流式 Paraformer
前端：WebSocket /asr，PCM int16 LE 16kHz mono in，JSON 事件 out

WS 协议（v0.3）
================
连接：
    ws://stt-server:9090/asr

客户端 → 服务端：
    binary frame   PCM int16 LE 16kHz mono samples（任意长度，建议 20-100ms 一帧）
    text "EOS"     声明本轮 utterance 结束，等 final
    text "RESET"   丢弃当前 stream 状态（一般不必，server 在 final 后自动 reset）

服务端 → 客户端：
    {"type": "partial", "text": "..."}    streaming 中间结果
    {"type": "final",   "text": "..."}    final（EOS 或 endpoint 触发；之后 server 自动 reset）
    {"type": "error",   "message": "..."} 出错

健康检查：
    GET /health → {"status": "ok"|"loading"}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import sherpa_onnx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

from fastapi.exceptions import RequestValidationError
from app.error_schema import ErrorResponse, api_error, http_exception_handler, validation_exception_handler
from app.segmentation import join_segments, should_soft_segment

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy
from rtvoice_auth.ws import pick_bearer_subprotocol
from rtvoice_auth.instrumentation import RequestMetricsMiddleware
from rtvoice_auth.openapi import add_bearer_security_scheme
from rtvoice_auth.metrics import STT_AUDIO_SECONDS_TOTAL
from rtvoice_auth.metrics_labels import safe_key_id

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.stt")

# Bearer 鉴权（v0.6.2 / SP6 T10）：通过 rtvoice_auth.key_store 校验。
# WS 不能像 HTTP 那样轻易加 header，所以接受三种来源（按优先级）：
#   1) Sec-WebSocket-Protocol: bearer.<TOKEN>     （browser 友好，标准用法）
#   2) Authorization: Bearer <TOKEN>              （server-to-server）
#   3) ?token=<TOKEN>                             （query param fallback；URL log 风险）

MODELS_DIR = Path(os.environ.get("STT_MODELS_DIR", "/app/models"))
MODEL_NAME = os.environ.get(
    "STT_MODEL", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
)
MODEL_DIR = MODELS_DIR / MODEL_NAME
NUM_THREADS = int(os.environ.get("STT_NUM_THREADS", "2"))
PROVIDER = os.environ.get("STT_PROVIDER", "cpu")  # "cpu" | "cuda"
# STT_QUANTIZED=true → model.int8.onnx（量化版，~230MB，显存零占用）
# STT_QUANTIZED=false → model.onnx（非量化版，~1.1GB，精度更高，显存低占用）
STT_QUANTIZED = os.environ.get("STT_QUANTIZED", "true").lower() in ("1", "true", "yes")
SAMPLE_RATE = 16000

# SenseVoice 识别语言："auto"|"zh"|"en"|"ja"|"ko"|"yue"（空串=auto）
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", "auto")
# 反向文本归一化（数字/日期等转标准书写形式）
STT_USE_ITN = os.environ.get("STT_USE_ITN", "true").lower() in ("1", "true", "yes")
# partial 重解码节流：累积音频每增长这么多秒才重新解码一次，避免短句内 O(N²) 抖动
PARTIAL_MIN_GROWTH_S = float(os.environ.get("STT_PARTIAL_MIN_GROWTH_S", "0.6"))
# 软切分（默认关）：长独白无 VAD 静音端点时，缓冲超过 SOFT_SEGMENT_MAX_S 秒就强制提交一段、
# 清空音频缓冲，把单次解码窗口限制在 N 秒内（SenseVoice 解码延迟≈0.33×窗口秒数，超长还掉字）。
# 服务端内部累积「已提交前缀」，对外仍每个 EOS 只发一次 final，协议/下游零改动。
# tradeoff：N 越小延迟越低但越易切断完整句子；N 越大语义越完整但延迟越高。
STT_SOFT_SEGMENT = os.environ.get("STT_SOFT_SEGMENT", "false").lower() in ("1", "true", "yes")
STT_SOFT_SEGMENT_MAX_S = float(os.environ.get("STT_SOFT_SEGMENT_MAX_S", "8.0"))
# 前导静音裁剪：裁掉音频缓冲头部低能量帧，降低 CIF predictor 对噪底 over-firing 产生的重复幻觉
STT_TRIM_SILENCE = os.environ.get("STT_TRIM_SILENCE", "true").lower() in ("1", "true", "yes")
STT_TRIM_SILENCE_THRESHOLD = float(os.environ.get("STT_TRIM_SILENCE_THRESHOLD", "0.008"))
# 调试：非空时将每条 EOS 触发的最终音频 dump 到该目录（用于离线分析重复幻觉）
STT_DEBUG_DUMP_DIR = os.environ.get("STT_DEBUG_DUMP_DIR", "").strip()
# 内部服务鉴权（供 TTS server 调用 /v1/transcribe 时使用，与 TTS 共享同一 env key）
_RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip()


def _build_recognizer() -> sherpa_onnx.OfflineRecognizer:
    """加载 SenseVoice-Small（非自回归离线模型）。

    决策（v0.20）：从 2023 streaming Zipformer (transducer + greedy) 切到 SenseVoice。
    Zipformer 自回归 + greedy 解码会掉进 token 重复循环（啦啦啦/妈妈妈幻觉）；
    SenseVoice 是非自回归、一次性输出整段，从架构上免疫重复幻觉，中文 CER 也更低，
    且 int8 权重 <300MB、CPU 即可、显存零占用。

    SenseVoice 是 offline 模型（不逐帧流式），但 RTVoice 的断句权威是客户端 silero VAD：
    客户端 feed PCM、发 EOS 标记一句结束。服务端在缓冲上做整段解码即可，WS 协议不变。
    """
    model_file = "model.int8.onnx" if STT_QUANTIZED else "model.onnx"
    model = MODEL_DIR / model_file
    tokens = MODEL_DIR / "tokens.txt"

    log.info("加载 sherpa-onnx SenseVoice (offline, non-autoregressive):")
    log.info("  model=%s (%.1fMB) quantized=%s", model, model.stat().st_size / 1e6, STT_QUANTIZED)
    log.info("  tokens=%s", tokens)
    log.info("  threads=%d provider=%s language=%s itn=%s",
             NUM_THREADS, PROVIDER, STT_LANGUAGE, STT_USE_ITN)

    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model),
        tokens=str(tokens),
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider=PROVIDER,
        language="" if STT_LANGUAGE == "auto" else STT_LANGUAGE,
        use_itn=STT_USE_ITN,
    )


# 全局单例（loadtime 即创建）
_recognizer: sherpa_onnx.OfflineRecognizer | None = None


def _trim_leading_silence(samples: np.ndarray, threshold_rms: float = 0.008) -> np.ndarray:
    """裁剪缓冲头部低能量帧，降低 CIF predictor 对噪底 over-firing 产生的字符重复幻觉。

    SenseVoice-Small int8 量化版本中，CIF 预测器会对低能量噪声帧连续 fire，
    产生高频汉字的重复幻觉（如"你你你你不你不"）。裁掉头部静音帧可显著减少此现象。
    """
    window_size = int(SAMPLE_RATE * 0.02)  # 20ms 窗口
    if len(samples) <= window_size * 2:
        return samples
    step = max(1, window_size // 2)
    for i in range(0, len(samples) - window_size, step):
        rms = float(np.sqrt(np.mean(samples[i : i + window_size] ** 2)))
        if rms > threshold_rms:
            start = max(0, i - step)
            return samples[start:] if start > 0 else samples
    return samples


def _decode_buffer(samples: np.ndarray) -> str:
    """在累积音频缓冲上做一次整段离线解码，返回识别文本。

    SenseVoice 是 offline：每次都用新 stream 喂全部样本、解码一次。
    用于 partial（在增长中的缓冲上重解码）和 final（EOS 后最终缓冲）。
    """
    assert _recognizer is not None
    if STT_TRIM_SILENCE and len(samples) > 0:
        samples = _trim_leading_silence(samples, STT_TRIM_SILENCE_THRESHOLD)
    stream = _recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    _recognizer.decode_stream(stream)
    r = stream.result
    return r if isinstance(r, str) else r.text


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _recognizer
    _recognizer = await asyncio.to_thread(_build_recognizer)
    log.info("recognizer ready")
    # SP6: init key store
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
    app.state.scope = "stt"
    log.info("key store ready (backend=%s, scope=stt)", backend)

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


app = FastAPI(title="RTVoice STT Server", version="0.21.0", lifespan=lifespan)

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

# SP10 G3 + G4
app.add_middleware(RequestMetricsMiddleware, service_name="stt-server")
add_bearer_security_scheme(app)

# --- Prometheus metrics ---
WS_ACTIVE = Gauge("rtvoice_stt_ws_connections_active", "Currently open /asr WS connections")
WS_TOTAL = Counter("rtvoice_stt_ws_connections_total", "Total /asr WS connections accepted")
EVENTS_TOTAL = Counter("rtvoice_stt_events_total", "Events emitted to client", ["type"])
DECODE_LATENCY = Histogram(
    "rtvoice_stt_decode_seconds",
    "sherpa-onnx decode_stream() per-call wall time",
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
)
Instrumentator(excluded_handlers=["/health", "/metrics", "/asr"]).instrument(app).expose(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if _recognizer is not None else "loading"}


@app.get("/info")
async def info() -> dict:
    # SP10 G4 — 4 service /info 统一返 service/version/capabilities/models
    return {
        "service": "stt-server",
        "version": "0.22.0",
        "capabilities": {
            "streaming": False,
            "architecture": "offline-non-autoregressive",
            "subprotocol_bearer": True,
            "endpoint_detection": False,
            "transcribe_endpoint": True,
        },
        "models": {
            "stt": MODEL_NAME,
            "sample_rate": SAMPLE_RATE,
        },
        "config": {
            "num_threads": NUM_THREADS,
            "language": STT_LANGUAGE,
            "use_itn": STT_USE_ITN,
            "partial_min_growth_s": PARTIAL_MIN_GROWTH_S,
            "soft_segment": STT_SOFT_SEGMENT,
            "soft_segment_max_s": STT_SOFT_SEGMENT_MAX_S,
            "trim_silence": STT_TRIM_SILENCE,
            "trim_silence_threshold": STT_TRIM_SILENCE_THRESHOLD,
        },
    }


@app.post("/v1/transcribe")
async def transcribe(request: Request) -> dict:
    """内部服务接口：一次性音频转写（供 TTS server 验证参考音频文本）。

    Body:    raw PCM int16 LE 16kHz mono bytes（不含 WAV header）
    Returns: {"text": "..."}
    Auth:    Bearer RTVOICE_API_KEY（空时不鉴权）
    """
    if _recognizer is None:
        raise HTTPException(status_code=503, detail="recognizer not loaded")

    if _RTVOICE_API_KEY:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != _RTVOICE_API_KEY:
            raise HTTPException(status_code=401, detail="unauthorized")

    raw = await request.body()
    if not raw:
        return {"text": ""}

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    with DECODE_LATENCY.time():
        text = await asyncio.to_thread(_decode_buffer, samples)
    log.info("[transcribe] %.2fs audio → %r", len(samples) / SAMPLE_RATE, text)
    return {"text": text}


async def _verify_ws_key(ws: WebSocket) -> Key | None:
    """三路 Bearer 验证；scope=stt。失败/缺失 token 返 None。"""
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
        return await verify_key(secret, scope="stt", store=ws.app.state.key_store)
    except AuthError:
        return None


@app.websocket("/v1/asr")
async def asr_ws(ws: WebSocket) -> None:
    """SenseVoice (offline) WS handler。

    协议与流式版本完全一致（client feed PCM / send EOS / recv partial+final），
    但内部改为 offline 缓冲解码：

        - 累积收到的 PCM 到一个 float32 缓冲
        - 缓冲每增长 PARTIAL_MIN_GROWTH_S 秒就在「当前全部缓冲」上重解码一次 → partial
          （SenseVoice 极快、句子短，整段重解码成本可忽略；节流避免高频抖动）
        - 收到 EOS：在最终缓冲上解码一次 → final，清空缓冲
        - RESET：清空缓冲
        - 单协程串行操作，无并发 race（offline 无内部 stream 状态，天然安全）
    """
    key = await _verify_ws_key(ws)
    if key is None:
        # close before accept → 4401（HTTP 路径上是 401，但 WS 协议得 close code）
        await ws.close(code=4401, reason="unauthorized")
        log.warning("WS 鉴权失败 client=%s", ws.client)
        return
    await ws.accept(subprotocol=pick_bearer_subprotocol(ws))
    if _recognizer is None:
        await ws.send_json({"type": "error", "message": "recognizer not loaded"})
        await ws.close()
        return

    buffer: list[np.ndarray] = []
    buffered_samples = 0
    decoded_at_samples = 0
    last_partial: str = ""
    committed_text: str = ""  # 软切分已提交前缀（软切分关时恒为空，行为不变）
    log.info("WS connected: %s", ws.client)
    WS_TOTAL.inc()
    WS_ACTIVE.inc()

    partial_growth_samples = int(PARTIAL_MIN_GROWTH_S * SAMPLE_RATE)
    soft_segment_samples = int(STT_SOFT_SEGMENT_MAX_S * SAMPLE_RATE)
    # 收 WS 消息的超时——超时则有机会跑一次 partial 解码
    RECV_TIMEOUT_S = 0.1

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=RECV_TIMEOUT_S)
            except asyncio.TimeoutError:
                msg = None

            if msg is not None:
                if msg.get("type") == "websocket.disconnect":
                    break

                data_bytes = msg.get("bytes")
                data_text = msg.get("text")

                if data_bytes:
                    # PCM int16 LE → float32 [-1, 1]
                    samples = np.frombuffer(data_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    if samples.size > 0:
                        buffer.append(samples)
                        buffered_samples += samples.size
                        # SP10 G3 — per-key audio seconds counter
                        try:
                            STT_AUDIO_SECONDS_TOTAL.labels(
                                key_id=safe_key_id(key),
                            ).inc(samples.size / SAMPLE_RATE)
                        except Exception:
                            pass
                elif data_text:
                    cmd = data_text.strip().upper()
                    if cmd == "EOS":
                        log.info("EOS received, decoding %d samples", buffered_samples)
                        text = committed_text
                        if buffered_samples > 0:
                            audio = np.concatenate(buffer)
                            with DECODE_LATENCY.time():
                                tail = await asyncio.to_thread(_decode_buffer, audio)
                            text = join_segments(committed_text, tail)
                            # 诊断 dump：将原始音频写入磁盘（分析重复幻觉用）
                            if STT_DEBUG_DUMP_DIR:
                                try:
                                    dump_dir = Path(STT_DEBUG_DUMP_DIR)
                                    dump_dir.mkdir(parents=True, exist_ok=True)
                                    ts = int(time.time() * 1000)
                                    dump_path = dump_dir / f"stt_eos_{ts}.wav"
                                    audio_int16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
                                    with wave.open(str(dump_path), "wb") as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(SAMPLE_RATE)
                                        wf.writeframes(audio_int16.tobytes())
                                    log.info("STT debug dump: %s text=%r", dump_path, text)
                                except Exception:
                                    log.exception("STT debug dump failed")
                        log.info("final after EOS: %r", text)
                        await ws.send_json({"type": "final", "text": text})
                        EVENTS_TOTAL.labels(type="final_eos").inc()
                        buffer = []
                        buffered_samples = 0
                        decoded_at_samples = 0
                        last_partial = ""
                        committed_text = ""
                        continue
                    elif cmd == "RESET":
                        log.info("RESET received")
                        buffer = []
                        buffered_samples = 0
                        decoded_at_samples = 0
                        last_partial = ""
                        committed_text = ""
                        continue
                    else:
                        log.warning("未知文本指令: %r", cmd)

            # 软切分：缓冲超过阈值且无 VAD 端点时，强制提交当前窗口、清空音频缓冲，
            # 把单次解码窗口限制在 N 秒内。已提交文本累积进 committed_text，等 EOS 一并发 final。
            if buffered_samples > 0 and should_soft_segment(
                buffered_samples, STT_SOFT_SEGMENT, soft_segment_samples
            ):
                audio = np.concatenate(buffer)
                with DECODE_LATENCY.time():
                    seg = await asyncio.to_thread(_decode_buffer, audio)
                committed_text = join_segments(committed_text, seg)
                log.info("soft-segment commit (%d samples): %r", buffered_samples, committed_text)
                buffer = []
                buffered_samples = 0
                decoded_at_samples = 0
                EVENTS_TOTAL.labels(type="soft_segment").inc()
                # 推一条带已提交前缀的 partial，避免界面文本回缩
                if committed_text and committed_text != last_partial:
                    last_partial = committed_text
                    try:
                        await ws.send_json({"type": "partial", "text": committed_text})
                        EVENTS_TOTAL.labels(type="partial").inc()
                    except Exception:
                        break
                continue

            # 节流 partial：缓冲较上次解码增长到阈值才重解码（同协程串行）
            if buffered_samples - decoded_at_samples >= partial_growth_samples:
                audio = np.concatenate(buffer)
                with DECODE_LATENCY.time():
                    seg = await asyncio.to_thread(_decode_buffer, audio)
                decoded_at_samples = buffered_samples
                text = join_segments(committed_text, seg)
                if text and text != last_partial:
                    last_partial = text
                    try:
                        await ws.send_json({"type": "partial", "text": text})
                        EVENTS_TOTAL.labels(type="partial").inc()
                    except Exception:
                        break
    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception:
        log.exception("WS handler 异常")
        try:
            await ws.send_json({"type": "error", "message": "internal error"})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        WS_ACTIVE.dec()
        log.info("WS closed: %s", ws.client)
