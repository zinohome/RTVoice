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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.stt")

MODELS_DIR = Path(os.environ.get("STT_MODELS_DIR", "/app/models"))
MODEL_NAME = os.environ.get(
    "STT_MODEL", "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
)
MODEL_DIR = MODELS_DIR / MODEL_NAME
NUM_THREADS = int(os.environ.get("STT_NUM_THREADS", "2"))
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
    log.info("  threads=%d", NUM_THREADS)

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        tokens=str(tokens),
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
        enable_endpoint_detection=True,
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


app = FastAPI(title="RTVoice STT Server", version="0.3.0", lifespan=lifespan)


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


async def _decode_loop(ws: WebSocket, stream, last_partial: list[str]) -> None:
    """异步循环：从 stream 拉 partial/final 事件推给客户端。

    last_partial 是单元素列表（用于在外部协程间共享状态，避免 nonlocal 闭包问题）。
    """
    assert _recognizer is not None
    while True:
        await asyncio.sleep(0.05)  # decode 节奏 50ms
        if not _recognizer.is_ready(stream):
            continue
        # decode 是 CPU 密集，丢线程池避免阻塞 event loop
        await asyncio.to_thread(_recognizer.decode_stream, stream)
        text = _get_text(stream)
        if text != last_partial[0]:
            last_partial[0] = text
            try:
                await ws.send_json({"type": "partial", "text": text})
            except Exception:
                return
        # 端点检测自然 final（兜底；正常路径靠客户端 EOS）
        if _recognizer.is_endpoint(stream):
            log.info("endpoint detected, text=%r", text)
            try:
                await ws.send_json({"type": "final", "text": text})
            except Exception:
                return
            await asyncio.to_thread(_recognizer.reset, stream)
            last_partial[0] = ""


@app.websocket("/asr")
async def asr_ws(ws: WebSocket) -> None:
    await ws.accept()
    if _recognizer is None:
        await ws.send_json({"type": "error", "message": "recognizer not loaded"})
        await ws.close()
        return

    stream = _recognizer.create_stream()
    last_partial = [""]
    log.info("WS connected: %s", ws.client)

    decoder_task = asyncio.create_task(_decode_loop(ws, stream, last_partial))

    try:
        while True:
            msg = await ws.receive()
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
                    # 把剩余特征 decode 完
                    while _recognizer.is_ready(stream):
                        await asyncio.to_thread(_recognizer.decode_stream, stream)
                    text = _get_text(stream)
                    log.info("final after EOS: %r", text)
                    await ws.send_json({"type": "final", "text": text})
                    await asyncio.to_thread(_recognizer.reset, stream)
                    last_partial[0] = ""
                elif cmd == "RESET":
                    log.info("RESET received")
                    await asyncio.to_thread(_recognizer.reset, stream)
                    last_partial[0] = ""
                else:
                    log.warning("未知文本指令: %r", cmd)
    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception:
        log.exception("WS handler 异常")
        try:
            await ws.send_json({"type": "error", "message": "internal error"})
        except Exception:
            pass
    finally:
        decoder_task.cancel()
        try:
            await decoder_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await ws.close()
        except Exception:
            pass
        log.info("WS closed: %s", ws.client)
