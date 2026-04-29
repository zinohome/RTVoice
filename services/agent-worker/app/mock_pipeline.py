"""Mock STT / LLM / TTS。

v0.2 阶段全在进程内，避免引入额外网络依赖。
v0.3+ 真引擎到位后，这里替换为 HTTP/WS 客户端。

设计原则：
    - 用 asyncio + asyncio.sleep 模拟真实延迟，让状态机走出真实节奏
    - TTS 输出 PCM 16kHz mono int16（与 LiveKit AudioSource 对齐）
    - 所有 generator 都是异步、可取消（barge-in 关键）
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import AsyncIterator

import numpy as np

log = logging.getLogger("rtvoice.agent.mock")


# ---------- Mock STT ----------------------------------------------------------

# 几条假转写，根据 PCM 长度伪随机选一条；让对话有一点变化
_FAKE_TRANSCRIPTS = [
    "你好",
    "今天天气怎么样",
    "讲个笑话",
    "现在几点了",
    "帮我查一下",
]


async def mock_stt(_audio_samples: int) -> str:
    """模拟 STT：固定 50ms"延迟"返回一条假转写。"""
    await asyncio.sleep(0.05)
    text = random.choice(_FAKE_TRANSCRIPTS)
    log.info("[mock STT] -> %s", text)
    return text


# ---------- Mock LLM ----------------------------------------------------------

# 简单关键词响应，让对话看起来有点上下文
def _canned_reply(user_text: str) -> str:
    if "你好" in user_text:
        return "你好，我是 RTVoice。"
    if "天气" in user_text:
        return "今天晴朗，气温二十度。"
    if "笑话" in user_text:
        return "为什么程序员喜欢黑色？因为他们害怕白屏。"
    if "几点" in user_text:
        return "现在是测试时间。"
    return "我听到你说话了。"


async def mock_llm(user_text: str) -> AsyncIterator[str]:
    """流式吐 token；每 30-80ms 一个汉字，模拟真实首 token 延迟约 200ms。"""
    reply = _canned_reply(user_text)
    log.info("[mock LLM] reply=%s", reply)
    # 首 token 延迟
    await asyncio.sleep(0.2)
    for ch in reply:
        yield ch
        await asyncio.sleep(0.04)


# ---------- Mock TTS ----------------------------------------------------------

# LiveKit AudioSource 用 16kHz mono int16；我们也用这个采样率，避免重采样
SAMPLE_RATE = 16000
FRAME_DURATION_MS = 20  # 每帧 20ms，符合 WebRTC 习惯
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 320

# 不同字符映射到不同频率，让"语音"听起来有变化（虽然还是 sine wave）
_BASE_FREQ = 220.0
_FREQ_STEP = 18.0


def _pcm_for_char(char_index: int, num_frames: int) -> np.ndarray:
    """为单个字符生成 num_frames 帧的 sine wave PCM。"""
    freq = _BASE_FREQ + (char_index % 8) * _FREQ_STEP
    total_samples = num_frames * SAMPLES_PER_FRAME
    t = np.arange(total_samples, dtype=np.float32) / SAMPLE_RATE
    # 0.3 amplitude 防爆音
    wave = 0.3 * np.sin(2.0 * math.pi * freq * t)
    # 帧首尾加渐入渐出（10ms），减少咔哒声
    fade = min(SAMPLES_PER_FRAME, len(wave) // 8)
    if fade > 0:
        wave[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        wave[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return (wave * 32767).astype(np.int16)


async def mock_tts(token_stream: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """流式 TTS：每收到一个字符吐 ~150ms 的音频（约等于一个汉字播放时长）。

    yield 的是单帧 PCM bytes（int16 LE），调用方按 20ms 节奏喂给 LiveKit。
    可被外部 cancel；asyncio.CancelledError 会自然向上传播实现 barge-in。
    """
    char_index = 0
    frames_per_char = 8  # 8 帧 × 20ms = 160ms
    async for ch in token_stream:
        log.debug("[mock TTS] synthesize char='%s'", ch)
        pcm = _pcm_for_char(char_index, frames_per_char)
        char_index += 1
        # 切分成 20ms 帧吐出（让播放端可以平滑接收 + 中断点细）
        for i in range(0, len(pcm), SAMPLES_PER_FRAME):
            frame = pcm[i : i + SAMPLES_PER_FRAME]
            if len(frame) < SAMPLES_PER_FRAME:
                pad = np.zeros(SAMPLES_PER_FRAME - len(frame), dtype=np.int16)
                frame = np.concatenate([frame, pad])
            yield frame.tobytes()
            # 让出事件循环；不要 sleep 真 20ms（消费端节奏控制）
            await asyncio.sleep(0)
