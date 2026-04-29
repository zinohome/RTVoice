"""RTVoice agent worker.

v0.2：低层 livekit-rtc + 自写状态机，mock STT/LLM/TTS 内嵌。
功能：
    - 启动时用 LIVEKIT_API_KEY/SECRET 自签 token，加入指定房间
    - 订阅参与者音频流，跑 silero-vad
    - 状态机驱动 mock STT → LLM → TTS 流水线
    - 把 mock TTS 输出（sine wave PCM）publish 回 LiveKit
    - 用户在 agent 说话期间再次开口 → barge-in：取消当前 LLM/TTS

v0.4+ 计划迁移到 livekit-agents AgentSession 框架。
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
    mock_stt,
    mock_tts,
)
from app.state_machine import State, StateMachine
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
AGENT_ROOM = os.environ.get("AGENT_ROOM", "rtvoice-test")
AGENT_IDENTITY = os.environ.get("AGENT_IDENTITY", "rtvoice-agent")

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
        # 当前 inflight 任务（barge-in 取消用）
        self._pipeline_task: asyncio.Task | None = None
        # 收到的用户音频累积缓冲（VAD 按帧消费）
        self._user_audio_buf = np.zeros(0, dtype=np.int16)
        # 累积的 user PCM 长度（用于 mock STT 的"假装根据音频长度返回"）
        self._user_audio_samples_in_turn = 0

    # ----- 状态转移回调 -----

    def _on_state_change(self, prev: State, to: State) -> None:
        log.info("[FSM] %s -> %s", prev.value, to.value)

    # ----- LiveKit 接入 -----

    async def join(self, url: str, token: str) -> None:
        log.info("agent 加入 room=%s url=%s", AGENT_ROOM, url)
        self.room.on("track_subscribed", self._on_track_subscribed)
        self.room.on("participant_connected", self._on_participant_connected)
        self.room.on("participant_disconnected", self._on_participant_disconnected)
        self.room.on("disconnected", lambda *a: log.warning("room disconnected: %s", a))

        await self.room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        log.info("agent 已加入 room；本地参与者: %s", self.room.local_participant.identity)

        # publish agent audio track（即使现在不发声，也提前 publish 让浏览器订阅）
        publication = await self.room.local_participant.publish_track(
            self.audio_track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        log.info("agent 已 publish track: %s", publication.sid)

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

    # ----- 用户音频消费 + VAD -----

    async def _consume_audio(self, track: rtc.RemoteAudioTrack) -> None:
        """订阅用户麦克风，逐帧跑 VAD，触发状态机。"""
        # AudioStream 自动重采样到指定 rate
        stream = rtc.AudioStream(track, sample_rate=VAD_SAMPLE_RATE, num_channels=1)
        async for ev in stream:
            frame = ev.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            self._user_audio_buf = np.concatenate([self._user_audio_buf, samples])
            # 按 VAD_FRAME_SAMPLES 切片喂 VAD
            while len(self._user_audio_buf) >= VAD_FRAME_SAMPLES:
                vad_frame = self._user_audio_buf[:VAD_FRAME_SAMPLES]
                self._user_audio_buf = self._user_audio_buf[VAD_FRAME_SAMPLES:]
                speech_start, speech_end, _prob = self.vad.feed(vad_frame)
                if speech_start:
                    self._user_audio_samples_in_turn = 0
                    await self._on_speech_start()
                if self.fsm.state in (State.LISTENING, State.SPEAKING):
                    self._user_audio_samples_in_turn += VAD_FRAME_SAMPLES
                if speech_end:
                    await self._on_speech_end()

    # ----- VAD 事件 → 状态机 -----

    async def _on_speech_start(self) -> None:
        log.info("[VAD] speech_start")
        if self.fsm.state == State.SPEAKING:
            # Barge-in
            log.info("[BARGE-IN] 用户打断，取消当前 LLM/TTS")
            self.fsm.transition(State.INTERRUPTED)
            if self._pipeline_task and not self._pipeline_task.done():
                self._pipeline_task.cancel()
            # 清空 audio source 队列（防残留）
            try:
                self.audio_source.clear_queue()
            except Exception:
                pass
            self.fsm.transition(State.LISTENING)
        elif self.fsm.state == State.IDLE:
            self.fsm.transition(State.LISTENING)
        # LISTENING / THINKING：忽略（已在听）

    async def _on_speech_end(self) -> None:
        log.info("[VAD] speech_end (%d samples in turn)", self._user_audio_samples_in_turn)
        if self.fsm.state != State.LISTENING:
            return
        # 启动 pipeline；保留 task 句柄供 barge-in 取消
        samples_at_turn_end = self._user_audio_samples_in_turn
        self._user_audio_samples_in_turn = 0
        self._pipeline_task = asyncio.create_task(self._run_pipeline(samples_at_turn_end))

    # ----- Pipeline: STT → LLM → TTS -----

    async def _run_pipeline(self, audio_samples: int) -> None:
        try:
            self.fsm.transition(State.THINKING)
            user_text = await mock_stt(audio_samples)
            self.fsm.transition(State.SPEAKING)

            llm_stream = mock_llm(user_text)
            tts_stream = mock_tts(llm_stream)

            # 持续 publish 到 audio source
            async for pcm_frame in tts_stream:
                # rtc.AudioFrame.create 期望 samples_per_channel
                frame = rtc.AudioFrame.create(
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=SAMPLES_PER_FRAME,
                )
                # 把我们的 PCM bytes 拷进 frame.data
                # frame.data 是 memoryview，长度 = samples_per_channel * 2 (int16)
                np.frombuffer(frame.data, dtype=np.int16)[:] = np.frombuffer(
                    pcm_frame, dtype=np.int16
                )
                await self.audio_source.capture_frame(frame)

            # TTS 自然结束 → 回 IDLE
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
        # agent 标识，未来可以做权限隔离
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

    # 优雅退出
    stop = asyncio.Event()

    def _shutdown(*_):
        log.info("收到信号，shutdown...")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    try:
        await agent.join(LIVEKIT_URL, token)
        await stop.wait()
    finally:
        log.info("断开 room...")
        try:
            await room.disconnect()
        except Exception:
            log.exception("disconnect 异常")


def main() -> None:
    log.info("RTVoice agent worker v0.2 启动")
    log.info("room=%s identity=%s url=%s", AGENT_ROOM, AGENT_IDENTITY, LIVEKIT_URL)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
