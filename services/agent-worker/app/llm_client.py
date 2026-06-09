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
    - Ollama 原生 API 路径：当 base_url 含 11434 时走 /api/chat + think=false，
      绕过 Ollama OpenAI 兼容层不支持 think=false 的限制（0.30.x 已验证）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

log = logging.getLogger("rtvoice.agent.llm")


DEFAULT_SYSTEM_PROMPT = (
    "你是一个语音助手 RTVoice。请用中文简洁回答用户问题。"
    "每次回复不超过 30 个字，直接说话，不要使用任何符号、emoji、列表或 markdown 格式。"
    "用户说的话可能是 ASR 转写，可能有错别字，请你智能理解。"
)

# 允许 .env 用 AGENT_SYSTEM_PROMPT 覆盖（不需要 rebuild 镜像，重启 agent-worker 即生效）
SYSTEM_PROMPT = os.environ.get("AGENT_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT

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


def _is_ollama_url(base_url: str) -> tuple[bool, str]:
    """检测 base_url 是否为 Ollama 实例，返回 (is_ollama, ollama_root)。

    Ollama 原生 API 端口为 11434。OpenAI 兼容层 /v1 不支持 think=false，
    但原生 /api/chat 支持，因此对 Ollama 走不同代码路径。
    """
    if ":11434" not in base_url:
        return False, ""
    # base_url 形如 http://host:11434/v1，取 /v1 之前的部分
    root = base_url.split(":11434")[0] + ":11434"
    return True, root


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "ollama",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.6,
        system_prompt: str = SYSTEM_PROMPT,
        connect_timeout_s: float = LLM_CONNECT_TIMEOUT_S,
        read_timeout_s: float = LLM_READ_TIMEOUT_S,
        fallback_reply: str = LLM_FALLBACK_REPLY,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.fallback_reply = fallback_reply
        self._is_ollama, self._ollama_root = _is_ollama_url(base_url)
        timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=10.0,
            pool=5.0,
        )
        # openai SDK 默认 max_retries=2 —— 已经会重试 connect 失败；mid-stream 失败
        # 不重试（避免 prompt 被 LLM 重新执行说两遍）。
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        # Ollama 原生 API 路径专用的 httpx 客户端
        self._http = httpx.AsyncClient(timeout=timeout) if self._is_ollama else None
        if self._is_ollama:
            log.info("[LLM] Ollama 模式：将使用原生 /api/chat + think=false (root=%s)", self._ollama_root)

    async def _raw_stream_ollama(self, user_text: str) -> AsyncIterator[str]:
        """Ollama 原生 /api/chat streaming，带 think=false。

        Ollama 0.30.x 的 OpenAI 兼容层不支持 think=false 参数，
        但原生 API 完整支持，避免 qwen3 系列模型陷入无限思考链导致 realtime 延迟暴增。
        """
        url = f"{self._ollama_root}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text},
            ],
            "stream": True,
            "think": False,
            "options": {
                "num_predict": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        async with self._http.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message", {})
                delta = msg.get("content", "")
                if delta:
                    yield delta
                if data.get("done"):
                    break

    async def _raw_stream_openai(self, user_text: str) -> AsyncIterator[str]:
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
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def _raw_stream(self, user_text: str) -> AsyncIterator[str]:
        if self._is_ollama:
            async for delta in self._raw_stream_ollama(user_text):
                yield delta
        else:
            async for delta in self._raw_stream_openai(user_text):
                yield delta

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """yield delta 字符串；保证至少有一段话输出，避免 agent 沉默。

        失败模式与处理：
          - cancel（barge-in）：直接 re-raise，不发兜底
          - 已发部分 token 后异常：停止（接 fallback 会跟在半句后面很怪）
          - 完全没发就异常 / 正常结束 0 token：发兜底回复让用户继续对话
        """
        log.info("[LLM] user=%r", user_text)
        emitted = 0
        full: list[str] = []
        try:
            async for delta in self._raw_stream(user_text):
                emitted += 1
                full.append(delta)
                yield delta
        except asyncio.CancelledError:
            log.info("[LLM] stream cancelled (emitted=%d)", emitted)
            raise
        except Exception as e:
            log.warning("[LLM] stream 异常 emitted=%d: %s", emitted, e)
            if emitted > 0:
                # 已经在播音；fallback 拼半句后面会很奇怪，宁可截断
                log.info("[LLM] 半句中止；reply_so_far=%r", "".join(full))
                return
            # 完全没产出 → 走兜底
        if emitted == 0:
            log.warning("[LLM] 0 token emitted → 发 fallback %r", self.fallback_reply)
            # 把 fallback 整段一次 yield；下游 phrase splitter 会按标点切
            yield self.fallback_reply
        else:
            log.info("[LLM] reply=%r", "".join(full))

    async def close(self) -> None:
        await self._client.close()
        if self._http is not None:
            await self._http.aclose()
