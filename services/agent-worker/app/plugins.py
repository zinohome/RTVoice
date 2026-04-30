"""livekit-agents 1.5+ 自定义 STT/TTS plugin 子类。

包装现有 stt_client / tts_client，使其符合 framework 接口。

LLM 不需要自定义 plugin —— 直接用 livekit.plugins.openai.LLM(base_url=...) 指向
我们的 ollama/vLLM。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import numpy as np
import websockets
from livekit import rtc
from livekit.agents import (
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    NotGivenOr,
    stt,
    tts,
    utils,
)
from websockets.asyncio.client import connect as ws_connect

log = logging.getLogger("rtvoice.agent.plugins")


# =============================================================
# STT plugin — 包装 stt-server WS 协议
# =============================================================

@dataclass
class _STTOptions:
    ws_url: str
    sample_rate: int = 16000


class RTVoiceSTT(stt.STT):
    """sherpa-onnx WS STT plugin。

    协议：见 services/stt-server/app/main.py
        bytes(PCM int16 LE 16k mono) → JSON {type: partial|final, text}
        text "EOS" 触发 final
    """

    def __init__(self, *, ws_url: str, sample_rate: int = 16000) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True),
        )
        self._opts = _STTOptions(ws_url=ws_url, sample_rate=sample_rate)

    async def _recognize_impl(
        self,
        buffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ):
        # 非 streaming 路径（framework 主要用 stream，这里走批量回退）
        raise NotImplementedError("RTVoiceSTT 仅支持 streaming")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        return _RTVoiceRecognizeStream(stt=self, opts=self._opts, conn_options=conn_options)


class _RTVoiceRecognizeStream(stt.RecognizeStream):
    def __init__(self, *, stt, opts: _STTOptions, conn_options) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts

    async def _run(self) -> None:
        """主循环：从 self._input_ch 读 push_frame'd 的音频帧，通过 WS 转发，
        把 server 返回的 partial/final 翻译成 SpeechEvent 推回。"""
        async with ws_connect(self._opts.ws_url, max_size=None,
                              ping_interval=20, ping_timeout=10) as ws:
            log.info("[v0.6 STT] WS 连接 %s", self._opts.ws_url)

            # 后台 reader：读 server JSON 事件 → 推 SpeechEvent
            async def reader() -> None:
                try:
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            continue
                        ev = json.loads(msg)
                        t = ev.get("type")
                        text = ev.get("text", "")
                        if t == "partial":
                            self._event_ch.send_nowait(stt.SpeechEvent(
                                type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                alternatives=[stt.SpeechData(language="zh-CN", text=text)],
                            ))
                        elif t == "final":
                            self._event_ch.send_nowait(stt.SpeechEvent(
                                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                alternatives=[stt.SpeechData(language="zh-CN", text=text)],
                            ))
                except websockets.exceptions.ConnectionClosed:
                    pass

            reader_task = asyncio.create_task(reader())

            # 主循环：从 input_ch 读音频帧 / "EOS" sentinel
            try:
                async for data in self._input_ch:
                    if isinstance(data, self._FlushSentinel):
                        # framework flush（一轮 utterance 结束）
                        await ws.send("EOS")
                        continue
                    # data: rtc.AudioFrame
                    if not isinstance(data, rtc.AudioFrame):
                        continue
                    # 重采样到 16kHz mono int16（framework 已尽量保证；这里防御性）
                    samples = np.frombuffer(data.data, dtype=np.int16)
                    await ws.send(samples.tobytes())
            finally:
                reader_task.cancel()
                try:
                    await reader_task
                except (asyncio.CancelledError, Exception):
                    pass


# =============================================================
# TTS plugin — 包装 tts-server HTTP 流式协议
# =============================================================

@dataclass
class _TTSOptions:
    base_url: str
    voice: str = "zf_xiaobei"
    lang: str = "cmn"
    speed: float = 1.0
    sample_rate: int = 24000


class RTVoiceTTS(tts.TTS):
    """Kokoro/CosyVoice HTTP TTS plugin。

    协议：见 services/tts-server/app/main.py
        POST /tts/stream {text, voice, lang, speed} → chunked PCM int16 LE 24k mono
    """

    def __init__(
        self,
        *,
        base_url: str,
        voice: str = "zf_xiaobei",
        lang: str = "cmn",
        speed: float = 1.0,
        sample_rate: int = 24000,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._opts = _TTSOptions(
            base_url=base_url, voice=voice, lang=lang, speed=speed,
            sample_rate=sample_rate,
        )

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _RTVoiceChunkedStream(tts=self, input_text=text,
                                     opts=self._opts, conn_options=conn_options)


class _RTVoiceChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts, input_text, opts: _TTSOptions, conn_options) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._opts = opts

    async def _run(self, output_emitter) -> None:
        import httpx

        log.info("[v0.6 TTS] synthesize %r", self._input_text[:40])
        payload = {
            "text": self._input_text,
            "voice": self._opts.voice,
            "lang": self._opts.lang,
            "speed": self._opts.speed,
        }
        url = f"{self._opts.base_url.rstrip('/')}/tts/stream"

        # 初始化 emitter（告诉 framework 采样率/声道数）
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"tts-server {resp.status_code}: {body[:200]!r}")
                # bytes 块直接 push 给 emitter；framework 内部分帧
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        output_emitter.push(chunk)
        output_emitter.flush()
