"""Admin Console 服务端代理（cookie 鉴权 → 注入内部 key → 转发各业务服务）。

为什么需要这一层：
  新版 Admin UI 只持有 HttpOnly 会话 cookie，不碰任何 secret。但 STT/TTS/Token/Voices
  分布在独立服务（stt-server / tts-server / token-server），它们只认 Bearer key、不认
  realtime-server 的会话 cookie。于是这里在 realtime-server 上开一族 /v1/console/* 端点：
    1. 用会话 cookie 鉴权（admin_key_from_session）；
    2. 转发到对应内部服务时注入 config.RTVOICE_API_KEY（跨服务调用 key），
       音色注册/删除注入 TTS_ADMIN_API_KEY（tts-server 独有的高权限 key）。
  前端因此既不持 secret、又能完整测试每条链路。
"""
from __future__ import annotations

import asyncio
import io
import logging
import struct
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from fastapi import HTTPException

import app.config as config
from app.auth_api import admin_key_from_session
from app.error_schema import ErrorResponse, api_error

log = logging.getLogger("rtvoice.console")

router = APIRouter(prefix="/v1/console", tags=["console"])

TTS_BASE_URL = config.TTS_BASE_URL.rstrip("/")
TOKEN_BASE_URL = config.TOKEN_BASE_URL.rstrip("/")
STT_WS_URL = config.STT_WS_URL
RTVOICE_API_KEY = config.RTVOICE_API_KEY
TTS_ADMIN_API_KEY = config.TTS_ADMIN_API_KEY


async def require_console_session(request: Request) -> str:
    """要求有效会话 cookie；否则 401。返回登录用户名（占位用途）。"""
    key = await admin_key_from_session(request)
    if key is None:
        raise api_error(401, "auth.not_authenticated", "未登录或会话已过期")
    return key.id


def _bearer(secret: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"} if secret else {}


# ──────────────────────────────────────────────────────────────────
# 服务监控
# ──────────────────────────────────────────────────────────────────
_MONITOR_TARGETS = [
    ("realtime-server", "http://realtime-server:9000", "/info", "/health"),
    ("stt-server", "http://stt-server:9090", "/info", "/health"),
    ("tts-server", "http://tts-server:9880", "/info", "/health"),
    ("token-server", "http://token-server:8000", "/info", "/health"),
    ("livekit-server", "http://livekit-server:7880", None, "/"),
    ("agent-worker", "http://agent-worker:9100", None, "/metrics"),
]


class ServiceStatus(BaseModel):
    name: str
    status: str  # "healthy" | "down"
    version: str | None = None
    detail: str | None = None


async def _probe_one(client: httpx.AsyncClient, name: str, base: str,
                     info: str | None, health: str | None) -> ServiceStatus:
    version: str | None = None
    detail: str | None = None
    # 优先 /info 拿 version；失败回落 /health
    if info:
        try:
            r = await client.get(f"{base}{info}", timeout=4.0)
            if r.status_code == 200:
                data = r.json()
                version = str(data.get("version") or "") or None
                caps = data.get("capabilities") or {}
                vc = (caps or {}).get("voice_count") if isinstance(caps, dict) else None
                if vc is not None:
                    detail = f"voices={vc}"
                return ServiceStatus(name=name, status="healthy", version=version, detail=detail)
        except Exception:
            pass
    if health:
        try:
            r = await client.get(f"{base}{health}", timeout=4.0)
            if r.status_code < 500:
                return ServiceStatus(name=name, status="healthy", version=version)
            detail = f"HTTP {r.status_code}"
        except Exception as e:
            detail = type(e).__name__
    return ServiceStatus(name=name, status="down", version=version, detail=detail)


@router.get("/services", response_model=list[ServiceStatus],
            summary="聚合各内部服务健康/版本",
            responses={401: {"model": ErrorResponse}})
async def services_status(_sess: str = Depends(require_console_session)) -> list[ServiceStatus]:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            _probe_one(client, n, b, i, h) for (n, b, i, h) in _MONITOR_TARGETS
        ])
    return list(results)


# ──────────────────────────────────────────────────────────────────
# TTS 合成测试（收齐 PCM → 封 WAV 返回，便于浏览器 <audio> 直接播放）
# ──────────────────────────────────────────────────────────────────
class TTSTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice: str = Field("default_zh_female")
    speed: float = Field(1.0, ge=0.5, le=2.0)
    lang: str = Field("cmn")


def _wrap_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_len = len(pcm)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm)
    return buf.getvalue()


@router.post("/tts", summary="TTS 合成（返回 WAV）",
             responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def tts_test(req: TTSTestRequest, _sess: str = Depends(require_console_session)) -> Response:
    payload = {"text": req.text, "voice": req.voice, "lang": req.lang, "speed": req.speed}
    url = f"{TTS_BASE_URL}/v1/tts/stream"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream("POST", url, json=payload, headers=_bearer(RTVOICE_API_KEY)) as resp:
                body = await resp.aread()
                if resp.status_code != 200:
                    raise api_error(502, "console.tts_upstream",
                                    f"TTS 服务 {resp.status_code}: {body.decode(errors='replace')[:200]}")
                sr = int(resp.headers.get("X-Sample-Rate", "24000"))
    except HTTPException:
        raise
    except Exception as e:
        raise api_error(502, "console.tts_upstream", f"TTS 转发失败：{e}")
    wav = _wrap_wav(body, sr)
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


# ──────────────────────────────────────────────────────────────────
# LiveKit Token 签发
# ──────────────────────────────────────────────────────────────────
class TokenTestRequest(BaseModel):
    room: str = Field(..., min_length=1, max_length=64)
    identity: str = Field(..., min_length=1, max_length=64)


@router.post("/tokens", summary="签发 LiveKit Token",
             responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def tokens_test(req: TokenTestRequest, _sess: str = Depends(require_console_session)) -> JSONResponse:
    url = f"{TOKEN_BASE_URL}/v1/tokens"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={"room": req.room, "identity": req.identity},
                                  headers=_bearer(RTVOICE_API_KEY))
    except Exception as e:
        raise api_error(502, "console.token_upstream", f"Token 转发失败：{e}")
    return JSONResponse(status_code=r.status_code, content=r.json())


# ──────────────────────────────────────────────────────────────────
# Voice 音色管理
# ──────────────────────────────────────────────────────────────────
@router.get("/voices", summary="音色列表",
            responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def voices_list(_sess: str = Depends(require_console_session)) -> JSONResponse:
    url = f"{TTS_BASE_URL}/v1/voices"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_bearer(RTVOICE_API_KEY))
    except Exception as e:
        raise api_error(502, "console.voices_upstream", f"音色列表转发失败：{e}")
    return JSONResponse(status_code=r.status_code, content=r.json())


@router.post("/voices/preview", summary="音频预处理预览（规范化 + STT 转写）",
             responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def voices_preview(
    file: UploadFile = File(...),
    _sess: str = Depends(require_console_session),
) -> JSONResponse:
    """处理音频，返回规范化后的 WAV（base64）+ STT 转写文本。"""
    import base64
    import wave as _wave

    if not TTS_ADMIN_API_KEY:
        raise api_error(403, "console.voices_admin_disabled",
                        "音色功能未启用（缺少 TTS_ADMIN_API_KEY）")

    raw = await file.read()
    files = {"file": (file.filename or "ref.wav", raw, file.content_type or "audio/wav")}

    # 1. TTS preview → 处理后的 WAV 字节
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{TTS_BASE_URL}/v1/voices/preview",
                files=files,
                headers=_bearer(TTS_ADMIN_API_KEY),
            )
        if r.status_code != 200:
            raise api_error(502, "console.voices_preview_upstream",
                            f"TTS 预处理失败 ({r.status_code}): {r.text[:200]}")
        processed_wav = r.content
        original_duration = float(r.headers.get("X-Original-Duration", "0"))
        effective_duration = float(r.headers.get("X-Effective-Duration", "0"))
    except HTTPException:
        raise
    except Exception as e:
        raise api_error(502, "console.voices_preview_upstream", f"TTS 预处理转发失败：{e}")

    # 2. 提取 PCM 裸数据（去掉 WAV 文件头），转发给 STT /v1/transcribe
    transcript = ""
    try:
        with _wave.open(io.BytesIO(processed_wav), "rb") as wf:
            pcm_data = wf.readframes(wf.getnframes())

        # 从 ws://stt-server:9090/v1/asr 派生 http://stt-server:9090/v1/transcribe
        from urllib.parse import urlparse
        parsed = urlparse(STT_WS_URL)
        stt_base = f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}"
        stt_transcribe_url = f"{stt_base}/v1/transcribe"

        async with httpx.AsyncClient(timeout=20.0) as client:
            r_stt = await client.post(
                stt_transcribe_url,
                content=pcm_data,
                headers={
                    "Content-Type": "application/octet-stream",
                    **_bearer(RTVOICE_API_KEY),
                },
            )
        if r_stt.status_code == 200:
            transcript = r_stt.json().get("text", "").strip()
    except Exception:
        log.warning("[console] STT preview 转写失败，transcript 留空", exc_info=True)

    return JSONResponse(content={
        "audio_b64": base64.b64encode(processed_wav).decode(),
        "transcript": transcript,
        "original_duration": round(original_duration, 3),
        "effective_duration": round(effective_duration, 3),
    })


@router.post("/voices", status_code=201, summary="注册音色（上传 wav + transcript）",
             responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def voices_add(
    spk_id: str = Form(...),
    prompt_text: str = Form(...),
    file: UploadFile = File(...),
    _sess: str = Depends(require_console_session),
) -> JSONResponse:
    if not TTS_ADMIN_API_KEY:
        raise api_error(403, "console.voices_admin_disabled",
                        "音色注册未启用（realtime-server 缺少 TTS_ADMIN_API_KEY）")
    raw = await file.read()
    files = {"file": (file.filename or "ref.wav", raw, file.content_type or "audio/wav")}
    data = {"spk_id": spk_id, "prompt_text": prompt_text}
    url = f"{TTS_BASE_URL}/v1/voices"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, data=data, files=files, headers=_bearer(TTS_ADMIN_API_KEY))
    except Exception as e:
        raise api_error(502, "console.voices_upstream", f"音色注册转发失败：{e}")
    return JSONResponse(status_code=r.status_code, content=r.json())


# ──────────────────────────────────────────────────────────────────
# 系统配置（运行时动态修改，无需重启；重启后恢复环境变量值）
# ──────────────────────────────────────────────────────────────────
class VoiceConfigResponse(BaseModel):
    default_voice: str


class PatchVoiceRequest(BaseModel):
    voice: str = Field(..., min_length=1, max_length=64)


@router.get("/config", response_model=VoiceConfigResponse,
            summary="查询系统配置（default_voice 等）",
            responses={401: {"model": ErrorResponse}})
async def get_config(_sess: str = Depends(require_console_session)) -> VoiceConfigResponse:
    return VoiceConfigResponse(default_voice=config.DEFAULT_VOICE)


@router.patch("/config/voice", response_model=VoiceConfigResponse,
              summary="动态设置默认音色（立即生效，重启后恢复环境变量值）",
              responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                         502: {"model": ErrorResponse}})
async def patch_default_voice(
    req: PatchVoiceRequest,
    _sess: str = Depends(require_console_session),
) -> VoiceConfigResponse:
    url = f"{TTS_BASE_URL}/v1/voices"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_bearer(RTVOICE_API_KEY))
        voices_data = r.json().get("voices", [])
        if req.voice not in voices_data:
            raise api_error(404, "console.voice_not_found", f"音色 {req.voice!r} 不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise api_error(502, "console.voices_upstream", f"验证音色失败：{e}")
    config.DEFAULT_VOICE = req.voice
    log.info("[config] default_voice → %s", req.voice)
    return VoiceConfigResponse(default_voice=config.DEFAULT_VOICE)


@router.delete("/voices/{spk_id}", summary="删除音色",
               responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def voices_delete(spk_id: str, _sess: str = Depends(require_console_session)) -> JSONResponse:
    if not TTS_ADMIN_API_KEY:
        raise api_error(403, "console.voices_admin_disabled",
                        "音色删除未启用（realtime-server 缺少 TTS_ADMIN_API_KEY）")
    url = f"{TTS_BASE_URL}/v1/voices/{spk_id}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.delete(url, headers=_bearer(TTS_ADMIN_API_KEY))
    except Exception as e:
        raise api_error(502, "console.voices_upstream", f"音色删除转发失败：{e}")
    return JSONResponse(status_code=r.status_code, content=r.json())


# ──────────────────────────────────────────────────────────────────
# STT WebSocket 代理（cookie 鉴权 → 注入内部 key → 双向中继 stt-server）
# 客户端：发二进制 PCM int16 LE 16kHz + 文本 "EOS"/"RESET"
# 服务端：回 JSON {type: partial|final, text}
# ──────────────────────────────────────────────────────────────────
@router.websocket("/asr")
async def console_asr(ws: WebSocket) -> None:
    session_key = await admin_key_from_session(ws)
    if session_key is None:
        await ws.close(code=4401, reason="unauthorized")
        return
    await ws.accept()

    extra_headers: dict[str, str] = {}
    subprotocols = None
    if RTVOICE_API_KEY:
        extra_headers["Authorization"] = f"Bearer {RTVOICE_API_KEY}"
        subprotocols = [f"bearer.{RTVOICE_API_KEY}"]
    try:
        upstream = await websockets.connect(
            STT_WS_URL,
            additional_headers=extra_headers or None,
            subprotocols=subprotocols,
            max_size=None,
            ping_interval=20,
            ping_timeout=10,
        )
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": f"STT 上游连接失败：{e}"})
        except Exception:
            pass
        await ws.close(code=1011, reason="upstream_failed")
        return

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])
        except Exception:
            pass
        finally:
            try:
                await upstream.close()
            except Exception:
                pass

    async def upstream_to_client() -> None:
        # 上游中途异常时，先给前端发一条 error 事件再关，避免前端永久卡在「识别中」。
        try:
            async for m in upstream:
                if isinstance(m, (bytes, bytearray)):
                    continue
                await ws.send_text(m)
        except Exception as e:
            try:
                await ws.send_json({"type": "error", "message": f"STT 上游中断：{e}"[:200]})
            except Exception:
                pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    await asyncio.gather(client_to_upstream(), upstream_to_client())
