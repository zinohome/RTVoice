"""LLM 客户端：OpenAI 兼容 streaming chat completions。

后端：
    v0.4 dev — ollama + Qwen2.5-1.5B (CPU, Q4)
    v0.5 prod — vLLM + Qwen2.5-3B (GPU)
    协议相同（OpenAI /v1/chat/completions），客户端代码零改动切换。

使用：
    llm = LLMClient(base_url="http://llm-server:11434/v1", model="qwen2.5:1.5b")
    async for delta in llm.stream(user_text="你好"):
        print(delta, end="", flush=True)

设计：
    - 使用官方 openai SDK，保证 API 演进兼容
    - 流式 generator 可被外部 cancel（barge-in 关键）
    - 单 client 实例可并发多次调用（OpenAI SDK 内部维护连接池）
    - max_tokens 默认偏小（语音回复要短）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

log = logging.getLogger("rtvoice.agent.llm")


SYSTEM_PROMPT = (
    "你是一个语音助手 RTVoice。请用中文简洁回答用户问题。"
    "每次回复不超过 30 个字，直接说话，不要使用任何符号、emoji、列表或 markdown 格式。"
    "用户说的话可能是 ASR 转写，可能有错别字，请你智能理解。"
)


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "ollama",
        max_tokens: int = 80,
        temperature: float = 0.6,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system_prompt = system_prompt
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30.0)

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """yield delta 字符串。可被 cancel；CancelledError 会终止 stream。"""
        log.info("[LLM] user=%r", user_text)
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_text},
                ],
                stream=True,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            full = []
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    full.append(delta)
                    yield delta
            log.info("[LLM] reply=%r", "".join(full))
        except Exception:
            log.exception("LLM stream 异常")
            raise

    async def close(self) -> None:
        await self._client.close()
