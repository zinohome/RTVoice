"""Test ConversationMemory: deque-based sliding window."""
from app.memory import ConversationMemory


def test_empty_starts_zero():
    m = ConversationMemory(max_turns=3)
    assert list(m) == []
    assert len(m) == 0


def test_append_pair_grows_two_messages():
    m = ConversationMemory(max_turns=3)
    m.append_turn("hi", "hello")
    msgs = list(m)
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert len(m) == 2


def test_evicts_oldest_pair_when_full():
    m = ConversationMemory(max_turns=2)
    m.append_turn("u1", "a1")
    m.append_turn("u2", "a2")
    m.append_turn("u3", "a3")  # 该驱逐 u1/a1
    msgs = list(m)
    assert msgs == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]


def test_assistant_text_truncated_at_size_cap():
    m = ConversationMemory(max_turns=3, assistant_max_chars=10)
    m.append_turn("u", "a" * 50)
    msgs = list(m)
    assert msgs[1]["content"] == "a" * 10


def test_clear_empties_buffer():
    m = ConversationMemory(max_turns=3)
    m.append_turn("u1", "a1")
    m.append_turn("u2", "a2")
    assert len(m) == 4
    m.clear()
    assert len(m) == 0
    assert list(m) == []


def test_clear_preserves_max_turns_after_clear():
    """clear 后还能继续 append."""
    m = ConversationMemory(max_turns=2)
    m.append_turn("u1", "a1")
    m.clear()
    m.append_turn("u2", "a2")
    m.append_turn("u3", "a3")
    m.append_turn("u4", "a4")  # 该驱逐 u2/a2（cap=2）
    msgs = list(m)
    assert msgs[0]["content"] == "u3"
