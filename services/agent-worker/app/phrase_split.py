"""LLM 流式 token → 句级 phrase 切分。

策略（中英文混合 voice agent 通用启发式）：
    1. 优先在硬标点（。！？.!?\n）切，要求 phrase ≥ MIN_LEN
    2. 其次在软标点（，；：,;:）切，要求 phrase ≥ MIN_LEN×2（避免太碎）
    3. 长度兜底：buf ≥ MAX_LEN 时强制 flush
    4. LLM 流结束后剩余 buf 整段输出

为什么这套阈值：
    - 太短的 phrase 单独合成开销不划算（HTTP 往返 + Kokoro 启动开销）
    - 太长会让首包延迟接近"等整个 LLM 输出"
    - 4-40 字范围在中文里大致是"半句到一句话"
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

# 硬标点：句末，强分割
_HARD_PUNCT = re.compile(r"[。！？\.\!\?\n]")
# 软标点：中等停顿，弱分割
_SOFT_PUNCT = re.compile(r"[，；：,;:]")


async def stream_to_phrases(
    token_stream: AsyncIterator[str],
    min_len: int = 4,
    soft_min_len: int = 8,
    max_len: int = 40,
) -> AsyncIterator[str]:
    """LLM token 流 → phrase 流。

    参数：
        token_stream  上游 token/delta 异步迭代器（可能 1-N 字符/chunk）
        min_len       hard punct 切分时 phrase 的最小字符数
        soft_min_len  soft punct 切分时 phrase 的最小字符数
        max_len       buf 达此长度强制 flush（无论是否有标点）
    """
    buf = ""
    async for delta in token_stream:
        if not delta:
            continue
        buf += delta
        # 内层尽量切尽 buf 中可切位置
        while True:
            split_at = -1
            # 1) 硬标点
            m = _HARD_PUNCT.search(buf)
            if m and m.end() >= min_len:
                split_at = m.end()
            elif (m2 := _SOFT_PUNCT.search(buf)) and m2.end() >= soft_min_len:
                # 2) 软标点（且 phrase 已有足够字符）
                split_at = m2.end()
            elif len(buf) >= max_len:
                # 3) 长度兜底
                split_at = max_len

            if split_at < 0:
                break

            phrase = buf[:split_at].strip()
            buf = buf[split_at:]
            if phrase:
                yield phrase

    # 4) 收尾：剩余 buf
    tail = buf.strip()
    if tail:
        yield tail
