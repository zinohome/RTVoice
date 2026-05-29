"""软切分纯逻辑（无 sherpa_onnx 依赖，便于单测）。

SenseVoice 是 offline 整段解码：解码延迟≈0.33×窗口秒数，且超长窗口会互相吞字。
长独白若一直没有 VAD 静音端点，缓冲会无限增长 → final 越来越慢、还掉字。
软切分在缓冲超过阈值时强制提交当前窗口、清空音频缓冲，把单次解码窗口限制在 N 秒内。
服务端内部累积「已提交前缀」，对外仍每个 EOS 只发一次 final，协议/下游零改动。
"""

from __future__ import annotations


def should_soft_segment(buffered_samples: int, enabled: bool, max_samples: int) -> bool:
    """缓冲是否已达到强制提交阈值。max_samples<=0 视为关闭。"""
    return enabled and max_samples > 0 and buffered_samples >= max_samples


def join_segments(prefix: str, seg: str) -> str:
    """拼接已提交前缀与新片段。

    SenseVoice 中文无空格、英文有空格：仅当拼接处两侧都是 ASCII 字母数字时插入空格，
    避免中文里出现多余半角空格（"你好"+"世界"→"你好世界"，"hello"+"world"→"hello world"）。
    """
    prefix = prefix.strip()
    seg = seg.strip()
    if not prefix:
        return seg
    if not seg:
        return prefix
    a, b = prefix[-1], seg[0]
    if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
        return f"{prefix} {seg}"
    return f"{prefix}{seg}"
