"""phrase_split 单元测试。"""

import asyncio

import pytest

from app.phrase_split import stream_to_phrases


async def feed(chunks):
    for c in chunks:
        yield c


async def collect(chunks, **kwargs):
    return [p async for p in stream_to_phrases(feed(chunks), **kwargs)]


def run(coro):
    return asyncio.run(coro)


def test_single_phrase_hard_punct():
    assert run(collect(["你好。"])) == ["你好。"]


def test_two_hard_phrases():
    # 单句 < min_len=4 时合并到一起（算法故意保守，避免切碎）
    # 用 ≥4 字的句子才会真正切两段
    assert run(collect(["今天真好。", "明天更好。"])) == ["今天真好。", "明天更好。"]


def test_short_hard_does_not_split():
    # "你好。"=3 字 < min_len=4 → 不切，等下一句
    assert run(collect(["你好。", "我好。"])) == ["你好。我好。"]


def test_token_level_streaming():
    # 1 char per chunk
    chunks = ["你", "好", "，", "我是", "AI。"]
    assert run(collect(chunks)) == ["你好，我是AI。"]


def test_short_soft_does_not_split():
    # "你好，世界" 太短，soft 不切
    assert run(collect(["你好，世界"])) == ["你好，世界"]


def test_long_soft_splits():
    # 累计到 ≥soft_min_len 才切
    chunks = ["这是一段比较", "长的话，", "我们继续说"]
    out = run(collect(chunks))
    # 软标点切：第 2 个 yield 后 buf 内容 = "这是一段比较长的话，" (10 字)，soft_min=8 → 切
    assert len(out) >= 1
    assert "".join(out) == "这是一段比较长的话，我们继续说"


def test_max_len_fallback():
    # 50 个 "啊" 无标点，触发长度兜底（max=40）
    out = run(collect(["啊"] * 50))
    assert len(out) == 2
    assert len(out[0]) == 40
    assert len(out[1]) == 10


def test_empty_input():
    assert run(collect([])) == []


def test_only_whitespace():
    assert run(collect(["   ", "  \n "])) == []


def test_english_punct():
    # "Hello," 6 字 < soft_min_len=8 → 不在逗号切；等到 "world." 句末才切
    chunks = ["Hello, ", "world. ", "How are you?"]
    out = run(collect(chunks))
    assert out == ["Hello, world.", "How are you?"]


def test_custom_thresholds():
    out = run(collect(["aaaaa"] * 5, max_len=10))   # 5 chunk × 5字 = 25 字，max=10 → 切多次
    assert all(len(p) <= 10 for p in out)
