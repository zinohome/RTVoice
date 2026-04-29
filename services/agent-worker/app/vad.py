"""Silero VAD 直加载（不依赖 silero-vad pip 包）。

silero VAD v5 ONNX 接口：
    输入：
        - input: float32 tensor [batch=1, samples=512] (16kHz) 或 [1, 256] (8kHz)
        - state: float32 tensor [2, 1, 128] （初始全 0）
        - sr: int64 tensor scalar，采样率
    输出：
        - output: float32 tensor [1, 1] —— speech 概率
        - stateN: float32 tensor [2, 1, 128] —— 下一次状态
"""

from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort

log = logging.getLogger("rtvoice.agent.vad")

SAMPLE_RATE = 16000
WINDOW_SIZE_SAMPLES = 512        # silero v5 16kHz 固定窗口
WINDOW_SIZE_MS = WINDOW_SIZE_SAMPLES * 1000 // SAMPLE_RATE   # 32ms
SPEECH_THRESHOLD = 0.5
SILENCE_END_MS = 600             # 静音持续 600ms → speech_end


class SileroVAD:
    def __init__(self, model_path: str = "/app/models/silero_vad.onnx") -> None:
        log.info("加载 silero VAD ONNX: %s", model_path)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._silence_ms = 0
        self._speaking = False

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._silence_ms = 0
        self._speaking = False

    def feed(self, frame_int16: np.ndarray) -> tuple[bool, bool, float]:
        """喂入一帧（必须是 WINDOW_SIZE_SAMPLES=512 个 int16 sample）。

        返回 (speech_start, speech_end, prob)。
        """
        if frame_int16.shape[0] != WINDOW_SIZE_SAMPLES:
            raise ValueError(
                f"VAD 输入需 {WINDOW_SIZE_SAMPLES} samples，收到 {frame_int16.shape[0]}"
            )
        x = (frame_int16.astype(np.float32) / 32768.0).reshape(1, WINDOW_SIZE_SAMPLES)
        prob_arr, self._state = self._sess.run(
            ["output", "stateN"],
            {"input": x, "state": self._state, "sr": self._sr},
        )
        prob = float(prob_arr[0, 0])

        is_speech = prob >= SPEECH_THRESHOLD
        speech_start = False
        speech_end = False

        if is_speech:
            if not self._speaking:
                self._speaking = True
                speech_start = True
            self._silence_ms = 0
        else:
            if self._speaking:
                self._silence_ms += WINDOW_SIZE_MS
                if self._silence_ms >= SILENCE_END_MS:
                    self._speaking = False
                    speech_end = True
                    self._silence_ms = 0

        return speech_start, speech_end, prob
