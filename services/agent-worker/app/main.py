"""RTVoice agent worker.

v0.5：STT/LLM/TTS 三个独立服务全部接入。
功能：
    - 启动用 LIVEKIT_API_KEY/SECRET 自签 token，加入指定房间
    - 启动连 stt-server WebSocket 长连接
    - 订阅参与者音频流，跑 silero-vad（输入 16kHz）
    - LISTENING 状态下：PCM 实时推送给 stt-server (流式 partial)
    - VAD speech_end → 发 EOS → 拿 final 文本 → THINKING
    - LLM (ollama Qwen2.5-1.5B) 流式 token
    - TTS (Kokoro 82M ONNX, 24kHz) HTTP 流式合成 → publish 24kHz audio track
    - 用户在 agent 说话期间再次开口 → barge-in：取消 LLM/TTS、reset STT、清音频队列

音频采样率：
    - 输入侧（用户麦克风 → VAD/STT）：16000Hz mono int16
    - 输出侧（agent → 浏览器）：24000Hz mono int16（Kokoro 原生输出，避免重采样）

v0.6 计划：迁移到 livekit-agents AgentSession 框架
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import timedelta

import time

import numpy as np
from livekit import api, rtc

from app.llm_client import LLMClient
from app import metrics as M
from app.phrase_split import stream_to_phrases
from app.state_machine import State, StateMachine
from app.stt_client import STTClient
from app.tts_client import TTSClient
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
STT_WS_URL = os.environ.get("STT_WS_URL", "ws://stt-server:9090/v1/asr")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://llm-server:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")
TTS_BASE_URL = os.environ.get("TTS_BASE_URL", "http://tts-server:9880")
TTS_VOICE = os.environ.get("TTS_VOICE", "zf_xiaobei")
TTS_LANG = os.environ.get("TTS_LANG", "cmn")
# 与 stt-server / tts-server 共享：留空 = 鉴权关闭（dev）
RTVOICE_API_KEY = os.environ.get("RTVOICE_API_KEY", "").strip() or None
AGENT_ROOM = os.environ.get("AGENT_ROOM", "rtvoice-test")
AGENT_IDENTITY = os.environ.get("AGENT_IDENTITY", "rtvoice-agent")

STT_FINAL_TIMEOUT_S = float(os.environ.get("STT_FINAL_TIMEOUT_S", "5.0"))

# v0.5.1：LLM 流式 → 句切分 → TTS pipeline 并发度
# 1=纯串行（夯实顺序播放，资源最少）
# 2-3=并行合成隐藏 synth 间隙（前提：CPU 够强 / GPU）
TTS_PIPELINE_CONCURRENCY = int(os.environ.get("TTS_PIPELINE_CONCURRENCY", "2"))

# 输出音频参数（与 Kokoro 原生输出对齐，避免重采样）
TTS_SAMPLE_RATE = 24000
TTS_FRAME_MS = 20
TTS_SAMPLES_PER_FRAME = TTS_SAMPLE_RATE * TTS_FRAME_MS // 1000   # 480


# ---------- Agent ------------------------------------------------------------

class Agent:
    def __init__(self, room: rtc.Room) -> None:
        self.room = room
        self.fsm = StateMachine(on_change=self._on_state_change)
        self.vad = SileroVAD()
        # TTS audio source（agent 的"喉咙"）— 24kHz 对齐 Kokoro 输出
        self.audio_source = rtc.AudioSource(TTS_SAMPLE_RATE, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "agent-tts", self.audio_source
        )
        # STT 客户端（长连接到 stt-server）
        self.stt = STTClient(STT_WS_URL, on_partial=self._on_stt_partial,
                             api_key=RTVOICE_API_KEY)
        # LLM 客户端（OpenAI 兼容，连 llm-server）
        self.llm = LLMClient(base_url=LLM_BASE_URL, model=LLM_MODEL, api_key=LLM_API_KEY)
        # TTS 客户端（HTTP 流式，连 tts-server）
        self.tts = TTSClient(base_url=TTS_BASE_URL, voice=TTS_VOICE, lang=TTS_LANG,
                             api_key=RTVOICE_API_KEY)
        # 启动时探测 TTS server 能力，决定是否走 v0.7 双向流式（150ms 端到端）
        # None = 未探测；True/False = 探测后结果。失败默认 False（fallback HTTP）
        self._tts_text_streaming: bool | None = None
        # 当前 inflight pipeline 任务（barge-in 取消用）
        self._pipeline_task: asyncio.Task | None = None
        # 用户音频累积缓冲（VAD 按帧消费）
        self._user_audio_buf = np.zeros(0, dtype=np.int16)
        # 累积 user PCM bytes 队列（供推到 STT 用，仅 LISTENING 时填）
        self._stt_feed_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._stt_feeder_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        # shutdown 标志（区分手动 shutdown vs RoomClosed 触发的 disconnect）
        self._shutdown: asyncio.Event = asyncio.Event()

    # ----- 状态转移回调 -----

    def _on_state_change(self, prev: State, to: State) -> None:
        log.info("[FSM] %s -> %s", prev.value, to.value)
        M.set_state(to.value)

    async def _on_stt_partial(self, text: str) -> None:
        if text:
            log.debug("[STT partial] %s", text)
            M.STT_PARTIALS_TOTAL.inc()

    # ----- LiveKit 接入 -----

    async def heartbeat_loop(self, path: str = "/tmp/agent-heartbeat") -> None:
        """每 5s 触摸心跳文件；docker healthcheck 看这个 mtime 判活。"""
        while True:
            try:
                with open(path, "w") as f:
                    f.write(str(asyncio.get_running_loop().time()))
            except Exception:
                pass
            await asyncio.sleep(5)

    def _on_disconnected(self, *args) -> None:
        """LiveKit room 断开 → 调度异步重连。

        触发场景：RoomClosed（LiveKit 内部回收）、网络抖动、livekit-server 重启等。
        v0.5.1 之前只 log，agent 卡死；v0.5.2 加自动重连（指数退避，最多 5 次）。
        """
        log.warning("room disconnected: %s, scheduling reconnect", args)
        if not self._shutdown.is_set():
            asyncio.create_task(self._reconnect())

    async def _reconnect(self, max_attempts: int = 5) -> None:
        for attempt in range(1, max_attempts + 1):
            if self._shutdown.is_set():
                return
            backoff = min(2 ** attempt, 30)
            log.info("[RECONNECT] 第 %d/%d 次尝试，等 %ds", attempt, max_attempts, backoff)
            await asyncio.sleep(backoff)
            try:
                # 重建 Room（旧 Room 实例已经断开，事件 listener 失效）
                self.room = rtc.Room()
                self.audio_source = rtc.AudioSource(TTS_SAMPLE_RATE, 1)
                self.audio_track = rtc.LocalAudioTrack.create_audio_track(
                    "agent-tts", self.audio_source
                )
                token = make_agent_token()
                await self.join(LIVEKIT_URL, token)
                log.info("[RECONNECT] ✅ 成功（第 %d 次尝试）", attempt)
                return
            except Exception:
                log.exception("[RECONNECT] 第 %d 次失败", attempt)
        log.error("[RECONNECT] %d 次后放弃；agent 进入空闲（重启容器恢复）", max_attempts)

    async def join(self, url: str, token: str) -> None:
        log.info("agent 加入 room=%s url=%s", AGENT_ROOM, url)
        self.room.on("track_subscribed", self._on_track_subscribed)
        self.room.on("participant_connected", self._on_participant_connected)
        self.room.on("participant_disconnected", self._on_participant_disconnected)
        self.room.on("disconnected", self._on_disconnected)

        await self.room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        log.info("agent 已加入 room；本地参与者: %s", self.room.local_participant.identity)

        publication = await self.room.local_participant.publish_track(
            self.audio_track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        log.info("agent 已 publish track: %s", publication.sid)

        # 探测 TTS 能力（一次性；失败 fallback HTTP）
        if self._tts_text_streaming is None:
            try:
                info = await self.tts.probe_capabilities()
                self._tts_text_streaming = bool(info.get("text_streaming"))
                log.info("[TTS-probe] backend=%s text_streaming=%s",
                         info.get("backend"), self._tts_text_streaming)
            except Exception as e:
                log.warning("[TTS-probe] 失败 %s；fallback HTTP", e)
                self._tts_text_streaming = False

        # 启动 STT feeder 任务（消费 _stt_feed_queue 推给 stt-server）
        if self._stt_feeder_task is None or self._stt_feeder_task.done():
            self._stt_feeder_task = asyncio.create_task(self._stt_feeder_loop())
        # 启动心跳任务（健康检查依据）
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self.heartbeat_loop())

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
        log.info("[AUDIO] 开始消费 track %s", track.sid)
        stream = rtc.AudioStream(track, sample_rate=VAD_SAMPLE_RATE, num_channels=1)
        frame_count = 0
        max_amp_seen = 0
        async for ev in stream:
            frame = ev.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            frame_count += 1
            # 每 100 帧打一次诊断（约每 2 秒）
            if frame_count <= 5 or frame_count % 100 == 0:
                amp = int(np.abs(samples).max()) if samples.size > 0 else 0
                max_amp_seen = max(max_amp_seen, amp)
                log.info("[AUDIO] track %s frame#%d samples=%d max_amp=%d (历史 max=%d)",
                         track.sid, frame_count, samples.size, amp, max_amp_seen)
            self._user_audio_buf = np.concatenate([self._user_audio_buf, samples])

            while len(self._user_audio_buf) >= VAD_FRAME_SAMPLES:
                vad_frame = self._user_audio_buf[:VAD_FRAME_SAMPLES]
                self._user_audio_buf = self._user_audio_buf[VAD_FRAME_SAMPLES:]
                speech_start, speech_end, prob = self.vad.feed(vad_frame)

                # 每 100 个 VAD 帧打一次最高 prob，诊断 silero 看到的概率
                if not hasattr(self, '_vad_log_counter'):
                    self._vad_log_counter = 0
                    self._vad_max_prob = 0.0
                self._vad_log_counter += 1
                self._vad_max_prob = max(self._vad_max_prob, prob)
                if self._vad_log_counter % 100 == 0:
                    log.info("[VAD diag] frames=%d 历史 max prob=%.3f (阈值 0.5)",
                             self._vad_log_counter, self._vad_max_prob)
                    self._vad_max_prob = 0.0

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
            M.BARGE_INS_TOTAL.inc()
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

    async def _publish_pcm_bytes(self, pcm: bytes) -> None:
        """把 PCM int16 LE bytes 切成 20ms 帧推到 LiveKit AudioSource。

        长度不是 frame 整数倍时，尾段补零（半静音 < 20ms 不会有可听咔哒）。
        """
        bytes_per_frame = TTS_SAMPLES_PER_FRAME * 2  # int16 = 2 bytes
        n_full = len(pcm) // bytes_per_frame
        for i in range(n_full):
            seg = pcm[i * bytes_per_frame : (i + 1) * bytes_per_frame]
            frame = rtc.AudioFrame.create(
                sample_rate=TTS_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=TTS_SAMPLES_PER_FRAME,
            )
            np.frombuffer(frame.data, dtype=np.int16)[:] = np.frombuffer(seg, dtype=np.int16)
            await self.audio_source.capture_frame(frame)
        leftover = pcm[n_full * bytes_per_frame :]
        if leftover:
            pad = bytes(bytes_per_frame - len(leftover))
            seg = leftover + pad
            frame = rtc.AudioFrame.create(
                sample_rate=TTS_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=TTS_SAMPLES_PER_FRAME,
            )
            np.frombuffer(frame.data, dtype=np.int16)[:] = np.frombuffer(seg, dtype=np.int16)
            await self.audio_source.capture_frame(frame)

    async def _synth_phrase(self, phrase: str, sem: asyncio.Semaphore) -> bytes:
        """合成一个 phrase 的全部 PCM。受 sem 限流。"""
        async with sem:
            chunks: list[bytes] = []
            async for chunk in self.tts.stream(phrase):
                chunks.append(chunk)
            return b"".join(chunks)

    async def _run_pipeline_ws(self, user_text: str, round_t0: float) -> None:
        """v0.7 双向流式：LLM token → TTS WS → PCM 顺序 publish。

        理论端到端首字节 ~150ms（vs v0.6 的 phrase-level ~300-500ms）。
        失败时不静默 fallback —— 直接抛 → 上层 cancel；下一轮 join 再探测。
        """
        ws = await self.tts.open_ws()
        first_audio: bool = False
        chunks_recv = 0
        t_llm = time.time()

        async def feeder() -> None:
            try:
                async for delta in self.llm.stream(user_text):
                    if delta:
                        await ws.send_text(delta)
            finally:
                await ws.eos()

        feed_task = asyncio.create_task(feeder())
        try:
            async for pcm in ws.audio_chunks():
                if not first_audio:
                    first_audio = True
                    if self.fsm.state == State.THINKING:
                        self.fsm.transition(State.SPEAKING)
                    log.info("[WS-pipeline] first_audio_ms=%.0f",
                             (time.time() - round_t0) * 1000)
                chunks_recv += 1
                await self._publish_pcm_bytes(pcm)
        finally:
            # barge-in 时（外层 task.cancel）这里要保证 close frame 发出去，
            # 否则 server 只能靠 TCP RST 才察觉，inference 多浪费 1-2 个 chunk。
            # shield 让 aclose 不被外层 cancel 中断。
            if not feed_task.done():
                feed_task.cancel()
            try:
                await asyncio.shield(asyncio.wait_for(ws.aclose(), timeout=2.0))
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass

        round_s = time.time() - round_t0
        log.info("[WS-pipeline] done chunks=%d llm_to_done=%.1fs round=%.1fs",
                 chunks_recv, time.time() - t_llm, round_s)
        M.ROUNDS_TOTAL.inc()
        M.ROUND_SECONDS.observe(round_s)

    async def _run_pipeline(self) -> None:
        """v0.5.1 streaming pipeline：
            STT final → LLM 流式 → 句切分 → 并发 TTS → 顺序 publish

        关键时序：
          - LLM 第一句完成 → 立刻 fire TTS task → 进入 SPEAKING
          - 后续 LLM 句子并发合成（受 TTS_PIPELINE_CONCURRENCY 限流）
          - publisher 严格按 phrase 顺序 await 已完成 task → 推音频

        v0.7：tts-server 报 text_streaming=true 时改走 _run_pipeline_ws。
        """
        round_t0 = time.time()
        prod_task: asyncio.Task | None = None

        try:
            self.fsm.transition(State.THINKING)

            while not self._stt_feed_queue.empty():
                await asyncio.sleep(0.01)

            t_stt_start = time.time()
            user_text = await self.stt.request_final(timeout=STT_FINAL_TIMEOUT_S)
            t_stt_done = time.time()
            log.info("[STT final] %r (%.0fms)", user_text, (t_stt_done - t_stt_start) * 1000)
            M.STT_FINALS_TOTAL.inc()
            if not user_text.strip():
                log.info("STT 空结果，回 IDLE")
                self.fsm.transition(State.IDLE)
                return

            # v0.7：能力探测显示支持双向流式 → 走 ws 路径（150ms TTFB）
            if self._tts_text_streaming:
                try:
                    await self._run_pipeline_ws(user_text, round_t0)
                    if self.fsm.state == State.SPEAKING:
                        self.fsm.transition(State.IDLE)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("[WS-pipeline] 异常，本轮失败；下次仍尝试 ws")
                    if self.fsm.state in (State.THINKING, State.SPEAKING):
                        self.fsm.transition(State.IDLE)
                    return

            # ---- 流式 LLM → phrase → TTS pipeline ----
            tasks_q: asyncio.Queue[asyncio.Task | None] = asyncio.Queue()
            sem = asyncio.Semaphore(TTS_PIPELINE_CONCURRENCY)
            metrics: dict = {"phrases": 0, "first_phrase_ready_ms": None,
                             "first_audio_ms": None, "round_ms": None}
            t_llm_start = time.time()

            async def producer() -> None:
                try:
                    llm_iter = self.llm.stream(user_text)
                    async for phrase in stream_to_phrases(llm_iter):
                        metrics["phrases"] += 1
                        log.info("[phrase %d] %r", metrics["phrases"], phrase)
                        task = asyncio.create_task(self._synth_phrase(phrase, sem))
                        await tasks_q.put(task)
                finally:
                    await tasks_q.put(None)

            prod_task = asyncio.create_task(producer())

            # 第一个 phrase 就绪即转 SPEAKING（开播）
            first = True
            while True:
                task = await tasks_q.get()
                if task is None:
                    break
                pcm = await task
                now = time.time()
                if first:
                    metrics["first_phrase_ready_ms"] = (now - t_llm_start) * 1000
                    metrics["first_audio_ms"] = (now - round_t0) * 1000
                    if self.fsm.state == State.THINKING:
                        self.fsm.transition(State.SPEAKING)
                    first = False
                if pcm:
                    await self._publish_pcm_bytes(pcm)

            # 自然结束 → IDLE
            if self.fsm.state == State.SPEAKING:
                self.fsm.transition(State.IDLE)
            round_s = time.time() - round_t0
            metrics["round_ms"] = round_s * 1000
            log.info("[ROUND METRIC] %s", metrics)

            # Prometheus
            M.ROUNDS_TOTAL.inc()
            M.ROUND_SECONDS.observe(round_s)
            M.ROUND_PHRASES.observe(metrics["phrases"])
            if metrics["first_audio_ms"] is not None:
                M.FIRST_AUDIO_SECONDS.observe(metrics["first_audio_ms"] / 1000.0)
        except asyncio.CancelledError:
            log.info("pipeline 被 cancel（barge-in）")
            if prod_task and not prod_task.done():
                prod_task.cancel()
            raise
        except Exception:
            log.exception("pipeline 异常")
            M.PIPELINE_ERRORS_TOTAL.inc()
            self.fsm.force(State.IDLE)
            if prod_task and not prod_task.done():
                prod_task.cancel()


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
        agent._shutdown.set()
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
            await agent.llm.close()
        except Exception:
            log.exception("LLM close 异常")
        try:
            await agent.tts.close()
        except Exception:
            log.exception("TTS close 异常")
        try:
            await room.disconnect()
        except Exception:
            log.exception("room disconnect 异常")


def main() -> None:
    log.info("RTVoice agent worker v0.5.1 启动")
    log.info("room=%s identity=%s livekit=%s",
             AGENT_ROOM, AGENT_IDENTITY, LIVEKIT_URL)
    log.info("stt=%s llm=%s model=%s",
             STT_WS_URL, LLM_BASE_URL, LLM_MODEL)
    log.info("tts=%s voice=%s lang=%s sr=%dHz pipeline_concurrency=%d",
             TTS_BASE_URL, TTS_VOICE, TTS_LANG, TTS_SAMPLE_RATE, TTS_PIPELINE_CONCURRENCY)
    M.init_state_gauge()
    M.start_metrics_server()
    asyncio.run(amain())


if __name__ == "__main__":
    main()
