"""ConversationMemory: 滑动窗口对话历史 (per spec D-2026-05-09-A.2)."""
from __future__ import annotations
from collections import deque
from collections.abc import Iterator


class ConversationMemory:
    """N 轮 user/assistant 滑动窗口；每轮 = 2 条消息（user + assistant）。

    用法：
        m = ConversationMemory(max_turns=6)
        m.append_turn(user_text, assistant_text)
        messages = [{"role":"system","content":prompt}, *list(m), {"role":"user","content":new_text}]
    """

    def __init__(self, max_turns: int = 6, assistant_max_chars: int = 4000) -> None:
        self._maxlen = max_turns * 2
        self._buf: deque = deque(maxlen=self._maxlen)
        self._assistant_max_chars = assistant_max_chars

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        """成对 append；deque maxlen 自动驱逐最早 2 条（保持成对）。

        assistant_text 超 cap 截断；user 不截（STT 长度受说话时长限制）。
        """
        self._buf.append({"role": "user", "content": user_text})
        clipped = assistant_text[: self._assistant_max_chars]
        self._buf.append({"role": "assistant", "content": clipped})

    def __iter__(self) -> Iterator[dict]:
        return iter(self._buf)

    def __len__(self) -> int:
        return len(self._buf)

    def clear(self) -> None:
        """清空当前历史；prompt 不动."""
        self._buf.clear()
