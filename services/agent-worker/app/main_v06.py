"""RTVoice agent worker — v0.6 experimental（livekit-agents 框架）

⚠️ 探索性：与 v0.5.1 main.py 并行存在；通过 AGENT_BACKEND=v06 启用。

差异：
    - 用 livekit-agents AgentSession 替代自写状态机
    - VAD 用 livekit.plugins.silero（替代我们手写的 onnxruntime 加载）
    - LLM 用 livekit.plugins.openai.LLM(base_url=...) 直连 ollama/vLLM
    - STT/TTS 通过 plugins.RTVoiceSTT / RTVoiceTTS 包装现有 client
    - turn detection / interruption / barge-in 由 framework 处理（不再手写 FSM）

Autonomous 验证天花板：
    - ✅ import 成功
    - ✅ AgentSession / WorkerOptions 实例化不报错
    - ⏳ 实际 entrypoint 调用 + 加入 room：需要 livekit worker 协议跑通
    - ⏳ 真实对话流：需要真音频测试

未通过的话回到 v0.5.1 (AGENT_BACKEND=v05 或不设)。
"""

from __future__ import annotations

import logging
import os

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import openai as openai_plugin
from livekit.plugins import silero

from app.plugins import RTVoiceSTT, RTVoiceTTS


log = logging.getLogger("rtvoice.agent.v06")


SYSTEM_PROMPT = (
    "你是一个语音助手 RTVoice。请用中文简洁回答用户问题。"
    "每次回复不超过 30 个字，直接说话，不要使用任何符号、emoji、列表或 markdown。"
    "用户的话来自 ASR 转写，可能有错别字，请智能理解。"
)


class RTVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)


async def entrypoint(ctx: JobContext) -> None:
    """每个 room 一个 entrypoint；由 livekit worker 自动 dispatch。"""
    log.info("[v0.6] entrypoint room=%s", ctx.room.name if ctx.room else "?")

    stt_url = os.environ.get("STT_WS_URL", "ws://stt-server:9090/asr")
    llm_url = os.environ.get("LLM_BASE_URL", "http://llm-server:11434/v1")
    llm_model = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
    llm_api_key = os.environ.get("LLM_API_KEY", "ollama")
    tts_url = os.environ.get("TTS_BASE_URL", "http://tts-server:9880")
    tts_voice = os.environ.get("TTS_VOICE", "zf_xiaobei")
    tts_lang = os.environ.get("TTS_LANG", "cmn")

    session = AgentSession(
        stt=RTVoiceSTT(ws_url=stt_url),
        llm=openai_plugin.LLM(
            api_key=llm_api_key,
            base_url=llm_url,
            model=llm_model,
            temperature=0.6,
        ),
        tts=RTVoiceTTS(
            base_url=tts_url,
            voice=tts_voice,
            lang=tts_lang,
        ),
        vad=silero.VAD.load(),
    )

    await ctx.connect()

    await session.start(
        agent=RTVoiceAgent(),
        room=ctx.room,
    )

    # 主动开场（可选；不要的话注释掉）
    # await session.generate_reply(instructions="向用户问好")


def main() -> None:
    log.info("RTVoice agent worker v0.6 (experimental, livekit-agents framework)")
    log.info("STT_WS_URL=%s", os.environ.get("STT_WS_URL"))
    log.info("LLM_BASE_URL=%s", os.environ.get("LLM_BASE_URL"))
    log.info("TTS_BASE_URL=%s", os.environ.get("TTS_BASE_URL"))

    # WorkerOptions: agent_name 不指定时所有 room 都接；指定后只接 dispatch 给 agent_name 的 room
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=os.environ.get("AGENT_IDENTITY", "rtvoice-agent"),
        ws_url=os.environ.get("LIVEKIT_INTERNAL_URL", "ws://livekit-server:7880"),
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    ))


if __name__ == "__main__":
    main()
