"""Per-turn pipeline (SP3): STT final → LLM stream w/ memory → TTS → client.

新增于 SP3：
  - 组 messages = [system(prompt), ...memory, {user:final}] 喂给 llm_client
  - LLM delta 同时 ws.send_json(response.text) 和 tts_ws.send_text
  - response.done 带 text=完整 assistant 回复
  - 成功 turn → memory.append_turn(user, assistant)
  - 全程 audit.write(event) 异步落 JSONL
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app import config
from app.tts_client import TTSClient

if TYPE_CHECKING:
    from app.session_manager import Session

log = logging.getLogger("rtvoice.realtime.pipeline")


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "turn.timeout"
    s = str(exc).lower()
    if "stt" in s:
        return "stt.failed"
    if "tts" in s:
        return "tts.failed"
    if "llm" in s or "openai" in s or "ollama" in s:
        return "llm.failed"
    return "internal.unknown"


async def _audit(sess, event: dict) -> None:
    """audit_writer 可选；写错不抛."""
    if sess.audit_writer is None:
        return
    try:
        await sess.audit_writer.write(event)
    except Exception:
        log.exception("audit.write failed (continuing)")


async def run_turn(sess, ws):
    """SP3 single turn with memory + streaming + audit."""
    sess.current_turn_task = asyncio.current_task()

    # SP4 K: voice/speed 热改 → pipeline 这里重建 TTS client
    if getattr(sess, "tts_client_dirty", False):
        try:
            old = sess.tts_client
            if old is not None and hasattr(old, "close"):
                res = old.close()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            log.exception("close old tts_client failed (continuing)")
        try:
            sess.tts_client = TTSClient(
                base_url=config.TTS_BASE_URL,
                voice=sess.voice,
                speed=sess.speed,
                api_key=config.RTVOICE_API_KEY or None,
            )
            sess.tts_client_dirty = False
            log.info("session %s rebuilt tts_client (voice=%s speed=%.2f)",
                     sess.id, sess.voice, sess.speed)
        except Exception:
            log.exception("rebuild tts_client failed")
            sess.tts_client_dirty = False

    user_text = ""
    assistant_chunks: list[str] = []
    try:
        # 1. STT final
        try:
            user_text = await sess.stt_client.request_final(
                timeout=config.STT_FINAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "code": "stt.timeout",
                                "message": "STT final timeout", "request_id": None})
            return

        if not user_text or not user_text.strip():
            await ws.send_json({"type": "error", "code": "stt.empty",
                                "message": "no speech detected", "request_id": None})
            return

        await ws.send_json({"type": "transcript.final", "text": user_text})
        await _audit(sess, {"event": "transcript.final", "text": user_text})

        # 2. 组 messages = [system, ...memory, user]
        messages: list[dict] = []
        if sess.prompt:
            messages.append({"role": "system", "content": sess.prompt})
        messages.extend(list(sess.memory))
        messages.append({"role": "user", "content": user_text})

        # 3. LLM stream + 并行 TTS feed + ws.response.text emit
        tts_ws = await sess.tts_client.open_ws()
        try:
            async def feeder():
                try:
                    async for delta in sess.llm_client.stream(messages):
                        if delta:
                            assistant_chunks.append(delta)
                            await ws.send_json({"type": "response.text", "text": delta})
                            await tts_ws.send_text(delta)
                finally:
                    await tts_ws.eos()

            feed_task = asyncio.create_task(feeder())
            try:
                async for pcm in tts_ws.audio_chunks():
                    if pcm:
                        await ws.send_bytes(pcm)
                await feed_task
            finally:
                if not feed_task.done():
                    feed_task.cancel()
                    try:
                        await feed_task
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            await tts_ws.aclose()

        # 4. response.done + memory + audit
        assistant_text = "".join(assistant_chunks)
        await ws.send_json({"type": "response.done", "text": assistant_text})
        await _audit(sess, {"event": "response.done", "text": assistant_text})

        if assistant_text:
            sess.memory.append_turn(user_text, assistant_text)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("turn failed: %s", e)
        try:
            await ws.send_json({"type": "error", "code": _classify_error(e),
                                "message": str(e)[:200], "request_id": None})
        except Exception:
            pass
        await _audit(sess, {"event": "error", "code": _classify_error(e),
                            "message": str(e)[:200]})
    finally:
        sess.current_turn_task = None
        sess.last_activity = datetime.now(timezone.utc)
