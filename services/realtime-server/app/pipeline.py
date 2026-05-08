"""Per-turn pipeline: STT final → LLM → TTS → client PCM (copy-paste from
agent-worker `_run_pipeline_ws`, simplified for SP2 = no memory)."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.session_manager import Session
    from fastapi import WebSocket

log = logging.getLogger("rtvoice.realtime.pipeline")


def _classify_error(exc: Exception) -> str:
    """Python exception → CONVENTIONS.md §6 error code"""
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


async def run_turn(sess, ws):
    """Single turn: STT final → LLM → TTS → client PCM + done.

    Per spec §6.1 (SP2; no memory).
    """
    sess.current_turn_task = asyncio.current_task()
    try:
        try:
            final_text = await sess.stt_client.request_final(
                timeout=config.STT_FINAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            await ws.send_json({
                "type": "error",
                "code": "stt.timeout",
                "message": "STT did not return final in time",
                "request_id": None,
            })
            return

        if not final_text or not final_text.strip():
            await ws.send_json({
                "type": "error",
                "code": "stt.empty",
                "message": "no speech detected",
                "request_id": None,
            })
            return

        await ws.send_json({
            "type": "transcript.final",
            "text": final_text,
        })

        tts_ws = await sess.tts_client.open_ws()
        try:
            async def feeder():
                try:
                    async for delta in sess.llm_client.stream(final_text):
                        if delta:
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

        await ws.send_json({
            "type": "response.done",
        })

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("turn failed: %s", e)
        try:
            await ws.send_json({
                "type": "error",
                "code": _classify_error(e),
                "message": str(e)[:200],
                "request_id": None,
            })
        except Exception:
            pass
    finally:
        sess.current_turn_task = None
        sess.last_activity = datetime.now(timezone.utc)
