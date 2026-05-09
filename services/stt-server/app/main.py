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
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import sherpa_onnx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

from fastapi.exceptions import RequestValidationError
from app.error_schema import ErrorResponse, api_error, http_exception_handler, validation_exception_handler

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.stt")

# Bearer 鉴权（v0.6.1）：留空 = 鉴权关闭（dev 默认）
# WS 不能像 HTTP 那样轻易加 header，所以接受三种来源（按优先级）：
#   1) Sec-WebSocket-Protocol: bearer.<TOKEN>     （browser 友好，标准用法）
#   2) Authorization: Bearer <TOKEN>              （server-to-server）
#   3) ?token=<TOKEN>                             （query param fallback；URL log 风险）
RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip()

MODELS_DIR = Path(os.environ.get("STT_MODELS_DIR", "/app/models"))
MODEL_NAME = os.environ.get(
    "STT_MODEL", "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
)
MODEL_DIR = MODELS_DIR / MODEL_NAME
NUM_THREADS = int(os.environ.get("STT_NUM_THREADS", "2"))
PROVIDER = os.environ.get("STT_PROVIDER", "cpu")  # "cpu" | "cuda"
SAMPLE_RATE = 16000

# 端点检测参数（agent-worker 已有 VAD，这里参数偏宽松，兜底用）
RULE1_TRAILING_SILENCE_S = float(os.environ.get("STT_RULE1_SILENCE", "1.2"))
RULE2_TRAILING_SILENCE_S = float(os.environ.get("STT_RULE2_SILENCE", "0.8"))
RULE3_MIN_UTT_LEN_S = float(os.environ.get("STT_RULE3_MIN_UTT", "20.0"))


def _build_recognizer() -> sherpa_onnx.OnlineRecognizer:
    """加载 streaming Zipformer (transducer) 模型。

    与 Paraformer 不同，Zipformer 是 transducer 架构，需要 encoder/decoder/joiner 三件套。
    协议层（WS 输入输出）与 recognizer 内部架构无关——切换 ASR 模型不影响 stt 客户端。
    """
    encoder = MODEL_DIR / "encoder-epoch-99-avg-1.int8.onnx"
    decoder = MODEL_DIR / "decoder-epoch-99-avg-1.int8.onnx"
    joiner = MODEL_DIR / "joiner-epoch-99-avg-1.int8.onnx"
    tokens = MODEL_DIR / "tokens.txt"

    log.info("加载 sherpa-onnx Streaming Zipformer:")
    log.info("  encoder=%s (%.1fMB)", encoder, encoder.stat().st_size / 1e6)
    log.info("  decoder=%s (%.1fMB)", decoder, decoder.stat().st_size / 1e6)
    log.info("  joiner=%s  (%.1fMB)", joiner, joiner.stat().st_size / 1e6)
    log.info("  tokens=%s", tokens)
    log.info("  threads=%d provider=%s", NUM_THREADS, PROVIDER)

    # 关键决策（v0.5.3）：禁用 sherpa 的端点检测（is_endpoint）。
    # 客户端 agent 的 silero VAD 是唯一权威——它告诉我们一句话什么时候结束。
    # 如果 sherpa 自己也检测端点，会和 agent VAD 冲突 + 在 decode_loop 里
    # 触发 stream.reset() 引起 race condition。RULE1/2/3 参数保留为
    # 兼容字段，但 enable_endpoint_detection=False 时 sherpa 完全不用它们。
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        tokens=str(tokens),
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider=PROVIDER,
        enable_endpoint_detection=False,
        rule1_min_trailing_silence=RULE1_TRAILING_SILENCE_S,
        rule2_min_trailing_silence=RULE2_TRAILING_SILENCE_S,
        rule3_min_utterance_length=RULE3_MIN_UTT_LEN_S,
    )


# 全局单例（loadtime 即创建）
_recognizer: sherpa_onnx.OnlineRecognizer | None = None


def _get_text(stream) -> str:
    """sherpa-onnx 1.13+ 的 get_result 直接返回 str；旧版返回带 .text 的对象。

    兼容两种 API。
    """
    assert _recognizer is not None
    r = _recognizer.get_result(stream)
    return r if isinstance(r, str) else r.text


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _recognizer
    _recognizer = await asyncio.to_thread(_build_recognizer)
    log.info("recognizer ready")
    yield
    log.info("shutdown")


app = FastAPI(title="RTVoice STT Server", version="0.5.0", lifespan=lifespan)

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
    return {
        "model": MODEL_NAME,
        "sample_rate": SAMPLE_RATE,
        "num_threads": NUM_THREADS,
        "endpoint_rules": {
            "rule1_silence_s": RULE1_TRAILING_SILENCE_S,
            "rule2_silence_s": RULE2_TRAILING_SILENCE_S,
            "rule3_min_utt_s": RULE3_MIN_UTT_LEN_S,
        },
    }


def _ws_auth_ok(ws: WebSocket) -> bool:
    """三路 Bearer 校验。RTVOICE_API_KEY 留空时直接通过。"""
    if not RTVOICE_API_KEY:
        return True
    # 1) Sec-WebSocket-Protocol: bearer.<TOKEN>
    proto = ws.headers.get("sec-websocket-protocol", "")
    for p in (s.strip() for s in proto.split(",")):
        if p.startswith("bearer.") and p[len("bearer."):] == RTVOICE_API_KEY:
            return True
    # 2) Authorization: Bearer <TOKEN>
    auth = ws.headers.get("authorization", "")
    if auth == f"Bearer {RTVOICE_API_KEY}":
        return True
    # 3) ?token=<TOKEN>
    if ws.query_params.get("token") == RTVOICE_API_KEY:
        return True
    return False


@app.websocket("/v1/asr")
async def asr_ws(ws: WebSocket) -> None:
    """v0.5.3：单线程消费循环，杜绝 stream 并发访问。

    旧版（v0.5.2）有两个 task 同时操作 sherpa-onnx Stream（decode_loop +
    EOS handler），加上 sherpa 自己的端点检测会异步 reset，三方 race
    导致 'STT 连接已关闭' WS crash。

    新版：
        - 取消独立 decode_loop task
        - 单一循环：receive WS msg（带超时）→ accept_waveform 或 EOS 处理
        - 每个循环周期检查 is_ready → 同线程内 decode → emit partial
        - sherpa endpoint detection 在 _build_recognizer 已禁用
        - 全程一个协程操作 stream，无并发，无 race
    """
    if not _ws_auth_ok(ws):
        # close before accept → 4401（HTTP 路径上是 401，但 WS 协议得 close code）
        await ws.close(code=4401, reason="unauthorized")
        log.warning("WS 鉴权失败 client=%s", ws.client)
        return
    await ws.accept()
    if _recognizer is None:
        await ws.send_json({"type": "error", "message": "recognizer not loaded"})
        await ws.close()
        return

    stream = _recognizer.create_stream()
    last_partial: str = ""
    log.info("WS connected: %s", ws.client)
    WS_TOTAL.inc()
    WS_ACTIVE.inc()

    # decode 节奏：每 50ms 跑一次（与旧 _decode_loop 一致）
    DECODE_INTERVAL_S = 0.05
    last_decode_t = 0.0

    try:
        while True:
            # ws.receive() 带超时——超时返回 None 让我们能周期性 decode
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=DECODE_INTERVAL_S)
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
                        # accept_waveform 是同步快操作，无需 to_thread
                        stream.accept_waveform(SAMPLE_RATE, samples)
                elif data_text:
                    cmd = data_text.strip().upper()
                    if cmd == "EOS":
                        log.info("EOS received, flushing")
                        stream.input_finished()
                        while _recognizer.is_ready(stream):
                            with DECODE_LATENCY.time():
                                await asyncio.to_thread(_recognizer.decode_stream, stream)
                        text = _get_text(stream)
                        log.info("final after EOS: %r", text)
                        await ws.send_json({"type": "final", "text": text})
                        EVENTS_TOTAL.labels(type="final_eos").inc()
                        await asyncio.to_thread(_recognizer.reset, stream)
                        last_partial = ""
                        last_decode_t = 0.0
                        continue
                    elif cmd == "RESET":
                        log.info("RESET received")
                        await asyncio.to_thread(_recognizer.reset, stream)
                        last_partial = ""
                        last_decode_t = 0.0
                        continue
                    else:
                        log.warning("未知文本指令: %r", cmd)

            # 周期性 decode + 推 partial（同协程，与上面的处理 100% 串行）
            now = asyncio.get_event_loop().time()
            if (now - last_decode_t) >= DECODE_INTERVAL_S and _recognizer.is_ready(stream):
                with DECODE_LATENCY.time():
                    await asyncio.to_thread(_recognizer.decode_stream, stream)
                last_decode_t = now
                text = _get_text(stream)
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
