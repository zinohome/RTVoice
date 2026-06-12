"""Per-turn pipeline (SP3): STT final → LLM stream w/ memory → TTS → client.

新增于 SP3：
  - 组 messages = [system(prompt), ...memory, {user:final}] 喂给 llm_client
  - LLM delta 按句子边界缓冲后逐句发文本、合成 TTS、发音频
  - response.done 带 text=完整 assistant 回复
  - 成功 turn → memory.append_turn(user, assistant)
  - 全程 audit.write(event) 异步落 JSONL

逐句交错模式（SP-TTS-SYNC）：
  以标点符号（。！？!?；;…\\n）断句，对每个完整句子：
    1. 先向前端发送句子文本（response.text）
    2. 再通过 TTS HTTP 接口合成并流式发送 PCM 音频
  消除"文字全部先出、音频再播"的感知延迟问题。
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app import config
from app.tts_client import TTSClient
from app.metrics import TURNS_TOTAL

if TYPE_CHECKING:
    from app.session_manager import Session

log = logging.getLogger("rtvoice.realtime.pipeline")

# 与 TTS server 保持一致的强终止标点集合
_SENT_BOUNDARY = frozenset("。！？!?；;…\n")


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """从累积缓冲中切出完整句子（以强标点结尾），返回 (完整句列表, 剩余片段)。"""
    sentences: list[str] = []
    start = 0
    for i, ch in enumerate(buf):
        if ch in _SENT_BOUNDARY:
            seg = buf[start:i + 1].strip()
            if seg:
                sentences.append(seg)
            start = i + 1
    return sentences, buf[start:]


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


async def _send_sentence(ws, tts_client, sentence: str) -> None:
    """向前端发送一个完整句子的文本，然后合成并发送对应音频。"""
    await ws.send_json({"type": "response.text", "text": sentence})
    async for pcm in tts_client.stream(sentence):
        if pcm:
            await ws.send_bytes(pcm)


async def run_turn(sess, ws):
    """SP3 single turn with memory + streaming + audit.

    采用逐句交错模式：LLM 文本按标点断句，每句先发文本再发音频，
    避免"文字全出完、音频才开始"的感知问题。
    """
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

        # 3. LLM stream → 逐句交错：文本句 N → 音频句 N → 文本句 N+1 → 音频句 N+1
        buf = ""
        async for delta in sess.llm_client.stream(messages):
            if delta:
                assistant_chunks.append(delta)
                buf += delta
                sentences, buf = _split_sentences(buf)
                for sentence in sentences:
                    await _send_sentence(ws, sess.tts_client, sentence)

        # 最后一段（无强终止标点的残余文本，如回复末尾没有句号）
        if buf.strip():
            await _send_sentence(ws, sess.tts_client, buf)

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
        status = "ok" if assistant_chunks else "error"
        TURNS_TOTAL.labels(status=status).inc()
        sess.current_turn_task = None
        sess.last_activity = datetime.now(timezone.utc)
