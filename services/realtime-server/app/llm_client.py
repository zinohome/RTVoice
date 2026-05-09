"""LLM 客户端：OpenAI 兼容 streaming chat completions。

后端：
    v0.4 dev — ollama + Qwen2.5-1.5B (CPU, Q4)
    v0.5 prod — vLLM + Qwen2.5-3B (GPU)
    协议相同（OpenAI /v1/chat/completions），客户端代码零改动切换。

使用：
    llm = LLMClient(base_url="http://llm-server:11434/v1", model="qwen2.5:1.5b")
    async for delta in llm.stream([{"role": "user", "content": "你好"}]):
        print(delta, end="", flush=True)

设计：
    - 使用官方 openai SDK，保证 API 演进兼容
    - 流式 generator 可被外部 cancel（barge-in 关键）
    - 单 client 实例可并发多次调用（OpenAI SDK 内部维护连接池）
    - max_tokens 默认偏小（语音回复要短）
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

log = logging.getLogger("rtvoice.agent.llm")



# max_tokens 是 LLM 上限，不是"文本长度限制"——pipeline 是 token 流式→句切分→并发 TTS→顺序播放，
# 改大不影响首字节延迟，只让回复更长。
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_LLM_MAX_TOKENS", "80"))

# 超时分层（httpx 语义）：
#   connect_s: TCP 握手；prod ollama 一般 <1s，给 10s 应对容器 cold start
#   read_s:   两次数据接收之间最长间隔；流式时天然变成 per-chunk timeout
#             vLLM cold start 首 token 可能 ≤20s，这里默认 30s 兜底
#   write/pool 用宽松默认即可
LLM_CONNECT_TIMEOUT_S = float(os.environ.get("LLM_CONNECT_TIMEOUT_S", "10.0"))
LLM_READ_TIMEOUT_S = float(os.environ.get("LLM_READ_TIMEOUT_S", "30.0"))

# LLM 异常或 0 token 时给出的兜底回复（让用户听见声音继续对话，而不是沉默）
DEFAULT_FALLBACK_REPLY = (
    "抱歉，我现在没听清楚，麻烦再说一次。"
)
LLM_FALLBACK_REPLY = (
    os.environ.get("LLM_FALLBACK_REPLY", "").strip() or DEFAULT_FALLBACK_REPLY
)


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "ollama",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.6,
        connect_timeout_s: float = LLM_CONNECT_TIMEOUT_S,
        read_timeout_s: float = LLM_READ_TIMEOUT_S,
        fallback_reply: str = LLM_FALLBACK_REPLY,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.fallback_reply = fallback_reply
        # openai SDK 默认 max_retries=2 —— 已经会重试 connect 失败；mid-stream 失败
        # 不重试（避免 prompt 被 LLM 重新执行说两遍）。
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=10.0,
                pool=5.0,
            ),
        )

    async def _raw_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """yield delta 字符串；保证至少有一段输出避免沉默。

        messages 由 caller 组装：[{role:system,...}, ...history, {role:user,...}]。
        失败模式：与 SP2 同（cancel re-raise / 半句中止 / 0-token fallback）。
        """
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        log.info("[LLM] user=%r (msgs=%d)", last_user, len(messages))
        emitted = 0
        full: list[str] = []
        try:
            async for delta in self._raw_stream(messages):
                emitted += 1
                full.append(delta)
                yield delta
        except asyncio.CancelledError:
            log.info("[LLM] stream cancelled (emitted=%d)", emitted)
            raise
        except Exception as e:
            log.warning("[LLM] stream 异常 emitted=%d: %s", emitted, e)
            if emitted > 0:
                log.info("[LLM] 半句中止；reply_so_far=%r", "".join(full))
                return
        if emitted == 0:
            log.warning("[LLM] 0 token emitted → 发 fallback %r", self.fallback_reply)
            yield self.fallback_reply
        else:
            log.info("[LLM] reply=%r", "".join(full))

    async def close(self) -> None:
        await self._client.close()
