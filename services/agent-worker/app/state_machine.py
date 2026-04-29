"""Agent 状态机。

实现 ARCHITECTURE.md §5 定义的状态：
    Idle → Listening → Thinking → Speaking → Interrupted

状态转换由外部事件驱动（VAD speech_start/speech_end、LLM 完成、TTS 完成）。
本类只管状态转换合法性与回调通知，不做副作用——副作用由 agent.py 统一处理。
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable

log = logging.getLogger("rtvoice.agent.fsm")


class State(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


# 合法转换：from -> {to}（参考 ARCHITECTURE.md §5）
_LEGAL: dict[State, set[State]] = {
    State.IDLE: {State.LISTENING},
    State.LISTENING: {State.IDLE, State.THINKING},
    State.THINKING: {State.SPEAKING, State.IDLE},
    State.SPEAKING: {State.IDLE, State.INTERRUPTED},
    State.INTERRUPTED: {State.LISTENING, State.IDLE},
}


class StateMachine:
    def __init__(self, on_change: Callable[[State, State], None] | None = None) -> None:
        self._state: State = State.IDLE
        self._on_change = on_change

    @property
    def state(self) -> State:
        return self._state

    def transition(self, to: State) -> bool:
        """尝试转移；非法转移返回 False 并打 warning，不抛异常。"""
        if to not in _LEGAL.get(self._state, set()):
            log.warning("非法状态转移: %s -> %s（已忽略）", self._state, to)
            return False
        prev = self._state
        self._state = to
        log.info("状态: %s -> %s", prev, to)
        if self._on_change:
            try:
                self._on_change(prev, to)
            except Exception:
                log.exception("on_change 回调异常")
        return True

    def force(self, to: State) -> None:
        """强制重置（出错或 shutdown 时用）。"""
        log.warning("强制状态: %s -> %s", self._state, to)
        prev = self._state
        self._state = to
        if self._on_change:
            try:
                self._on_change(prev, to)
            except Exception:
                log.exception("on_change 回调异常")
