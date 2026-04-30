"""RTVoice agent worker.

v0.3：STT 切到独立 stt-server (sherpa-onnx Streaming Zipformer CPU)，
      LLM/TTS 仍是 mock 内嵌。
功能：
    - 启动用 LIVEKIT_API_KEY/SECRET 自签 token，加入指定房间
    - 启动连 stt-server WebSocket 长连接
    - 订阅参与者音频流，跑 silero-vad
    - LISTENING 状态下：PCM 实时推送给 stt-server (流式 partial)
    - VAD speech_end → 发 EOS，等 final 文本 → THINKING
    - mock LLM 流式 token → mock TTS 流式 PCM → publish 回 LiveKit
    - 用户在 agent 说话期间再次开口 → barge-in：取消 LLM/TTS，重置 STT

v0.4 计划：LLM 切独立 ollama (Qwen2.5-1.5B CPU)
v0.5 计划：TTS 切真引擎 (Kokoro CPU 或 CosyVoice 2 GPU)
v0.6 计划：迁移到 livekit-agents AgentSession 框架
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import timedelta

import numpy as np
from livekit import api, rtc

from app.mock_pipeline import (
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    mock_llm,
    mock_tts,
)
from app.state_machine import State, StateMachine
from app.stt_client import STTClient
from app.vad import (
    SileroVAD,
    SAMPLE_RATE as VAD_SAMPLE_RATE,
    WINDOW_SIZE_MS as VAD_FRAME_MS,
    WINDOW_SIZE_SAMPLES as VAD_FRAME_SAMPLES,
)

# ---------- 配置 -------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.agent")

LIVEKIT_URL = os.environ.get("LIVEKIT_INTERNAL_URL", "ws://livekit-server:7880")
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
STT_WS_URL = os.environ.get("STT_WS_URL", "ws://stt-server:9090/asr")
AGENT_ROOM = os.environ.get("AGENT_ROOM", "rtvoice-test")
AGENT_IDENTITY = os.environ.get("AGENT_IDENTITY", "rtvoice-agent")

STT_FINAL_TIMEOUT_S = float(os.environ.get("STT_FINAL_TIMEOUT_S", "5.0"))


# ---------- Agent ------------------------------------------------------------

class Agent:
    def __init__(self, room: rtc.Room) -> None:
        self.room = room
        self.fsm = StateMachine(on_change=self._on_state_change)
        self.vad = SileroVAD()
        # TTS audio source（agent 的"喉咙"）
        self.audio_source = rtc.AudioSource(SAMPLE_RATE, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "agent-tts", self.audio_source
        )
        # STT 客户端（长连接到 stt-server）
        self.stt = STTClient(STT_WS_URL, on_partial=self._on_stt_partial)
        # 当前 inflight pipeline 任务（barge-in 取消用）
        self._pipeline_task: asyncio.Task | None = None
        # 用户音频累积缓冲（VAD 按帧消费）
        self._user_audio_buf = np.zeros(0, dtype=np.int16)
        # 累积 user PCM bytes 队列（供推到 STT 用，仅 LISTENING 时填）
        self._stt_feed_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._stt_feeder_task: asyncio.Task | None = None

    # ----- 状态转移回调 -----

    def _on_state_change(self, prev: State, to: State) -> None:
        log.info("[FSM] %s -> %s", prev.value, to.value)

    async def _on_stt_partial(self, text: str) -> None:
        if text:
            log.debug("[STT partial] %s", text)

    # ----- LiveKit 接入 -----

    async def join(self, url: str, token: str) -> None:
        log.info("agent 加入 room=%s url=%s", AGENT_ROOM, url)
        self.room.on("track_subscribed", self._on_track_subscribed)
        self.room.on("participant_connected", self._on_participant_connected)
        self.room.on("participant_disconnected", self._on_participant_disconnected)
        self.room.on("disconnected", lambda *a: log.warning("room disconnected: %s", a))

        await self.room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        log.info("agent 已加入 room；本地参与者: %s", self.room.local_participant.identity)

        publication = await self.room.local_participant.publish_track(
            self.audio_track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        log.info("agent 已 publish track: %s", publication.sid)

        # 启动 STT feeder 任务（消费 _stt_feed_queue 推给 stt-server）
        self._stt_feeder_task = asyncio.create_task(self._stt_feeder_loop())

    def _on_participant_connected(self, p: rtc.RemoteParticipant) -> None:
        log.info("参与者加入: %s", p.identity)

    def _on_participant_disconnected(self, p: rtc.RemoteParticipant) -> None:
        log.info("参与者离开: %s", p.identity)

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        log.info("订阅到音频 track: %s from %s", track.sid, participant.identity)
        asyncio.create_task(self._consume_audio(track))

    # ----- STT feeder -----

    async def _stt_feeder_loop(self) -> None:
        """后台消费 _stt_feed_queue，把 PCM 推给 stt-server。"""
        while True:
            try:
                pcm = await self._stt_feed_queue.get()
                await self.stt.feed(pcm)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("stt_feeder 异常")

    def _enqueue_stt(self, pcm_bytes: bytes) -> None:
        """非阻塞入队；满了就丢（避免 VAD 帧背压拖慢主流程）。"""
        try:
            self._stt_feed_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            log.warning("STT feed queue 满，丢弃 %d bytes", len(pcm_bytes))

    # ----- 用户音频消费 + VAD -----

    async def _consume_audio(self, track: rtc.RemoteAudioTrack) -> None:
        """订阅用户麦克风，逐帧跑 VAD，触发状态机；LISTENING 状态下喂 STT。"""
        stream = rtc.AudioStream(track, sample_rate=VAD_SAMPLE_RATE, num_channels=1)
        async for ev in stream:
            frame = ev.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            self._user_audio_buf = np.concatenate([self._user_audio_buf, samples])

            while len(self._user_audio_buf) >= VAD_FRAME_SAMPLES:
                vad_frame = self._user_audio_buf[:VAD_FRAME_SAMPLES]
                self._user_audio_buf = self._user_audio_buf[VAD_FRAME_SAMPLES:]
                speech_start, speech_end, _prob = self.vad.feed(vad_frame)

                if speech_start:
                    await self._on_speech_start()

                # LISTENING 状态下把 PCM 推给 STT（含端点前的几帧静音，让 zipformer 看到边界）
                if self.fsm.state == State.LISTENING:
                    self._enqueue_stt(vad_frame.tobytes())

                if speech_end:
                    await self._on_speech_end()

    # ----- VAD 事件 → 状态机 -----

    async def _on_speech_start(self) -> None:
        log.info("[VAD] speech_start")
        if self.fsm.state == State.SPEAKING:
            log.info("[BARGE-IN] 用户打断，取消当前 LLM/TTS")
            self.fsm.transition(State.INTERRUPTED)
            if self._pipeline_task and not self._pipeline_task.done():
                self._pipeline_task.cancel()
            try:
                self.audio_source.clear_queue()
            except Exception:
                pass
            # 重置 STT，丢弃 SPEAKING 期间不存在的 stream 状态
            await self.stt.reset()
            self.fsm.transition(State.LISTENING)
        elif self.fsm.state == State.IDLE:
            self.fsm.transition(State.LISTENING)
        # LISTENING / THINKING：忽略

    async def _on_speech_end(self) -> None:
        log.info("[VAD] speech_end")
        if self.fsm.state != State.LISTENING:
            return
        self._pipeline_task = asyncio.create_task(self._run_pipeline())

    # ----- Pipeline: STT(WS) → mock LLM → mock TTS -----

    async def _run_pipeline(self) -> None:
        try:
            self.fsm.transition(State.THINKING)

            # 1) 等剩余 PCM 推完，发 EOS，拿 final
            # 等 feed queue 排空（避免 EOS 比最后帧先到）
            while not self._stt_feed_queue.empty():
                await asyncio.sleep(0.01)

            user_text = await self.stt.request_final(timeout=STT_FINAL_TIMEOUT_S)
            log.info("[STT final] %r", user_text)
            if not user_text.strip():
                log.info("STT 空结果，回 IDLE")
                self.fsm.transition(State.IDLE)
                return

            self.fsm.transition(State.SPEAKING)

            # 2) mock LLM + mock TTS（v0.4 / v0.5 才会换真）
            llm_stream = mock_llm(user_text)
            tts_stream = mock_tts(llm_stream)

            async for pcm_frame in tts_stream:
                frame = rtc.AudioFrame.create(
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=SAMPLES_PER_FRAME,
                )
                np.frombuffer(frame.data, dtype=np.int16)[:] = np.frombuffer(
                    pcm_frame, dtype=np.int16
                )
                await self.audio_source.capture_frame(frame)

            if self.fsm.state == State.SPEAKING:
                self.fsm.transition(State.IDLE)
        except asyncio.CancelledError:
            log.info("pipeline 被 cancel（barge-in）")
            raise
        except Exception:
            log.exception("pipeline 异常")
            self.fsm.force(State.IDLE)


# ---------- 启动 -------------------------------------------------------------

def make_agent_token() -> str:
    """agent 用服务端密钥自签 token（不通过 token-server）。"""
    grants = api.VideoGrants(
        room_join=True,
        room=AGENT_ROOM,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        agent=True,
    )
    return (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(AGENT_IDENTITY)
        .with_name(AGENT_IDENTITY)
        .with_grants(grants)
        .with_ttl(timedelta(hours=24))
        .to_jwt()
    )


async def amain() -> None:
    room = rtc.Room()
    agent = Agent(room)
    token = make_agent_token()

    stop = asyncio.Event()

    def _shutdown(*_):
        log.info("收到信号，shutdown...")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    try:
        # 先连 STT（如果连不上，至少 LiveKit 部分还能起来观察）
        try:
            await agent.stt.connect()
        except Exception:
            log.exception("STT 连接失败（继续启动，agent 状态机仍然会跑）")

        await agent.join(LIVEKIT_URL, token)
        await stop.wait()
    finally:
        log.info("断开...")
        if agent._stt_feeder_task and not agent._stt_feeder_task.done():
            agent._stt_feeder_task.cancel()
            try:
                await agent._stt_feeder_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await agent.stt.close()
        except Exception:
            log.exception("STT close 异常")
        try:
            await room.disconnect()
        except Exception:
            log.exception("room disconnect 异常")


def main() -> None:
    log.info("RTVoice agent worker v0.3 启动")
    log.info("room=%s identity=%s url=%s stt=%s",
             AGENT_ROOM, AGENT_IDENTITY, LIVEKIT_URL, STT_WS_URL)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
