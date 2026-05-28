"""Admin Console 鉴权层：用户名/密码登录 + HttpOnly 会话 cookie。

设计要点（对应验收讨论）：
- 登录用环境变量里的用户名/密码（常量时间比较），不再让 admin 贴 secret。
- 登录成功下发 HMAC 签名的无状态会话 cookie（见 rtvoice_auth.session）。
- 会话 cookie 解析到一枚「全权限内部 admin key」——该 key 由服务端 provision 且自愈：
  启动时校验、缺失/被吊销则立即补建，绝不被 keys.yaml 清理误删导致 admin 掉权限。
- 前端只带 cookie（credentials:'include'），不碰任何 secret。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from rtvoice_auth.models import Key
from rtvoice_auth.session import (
    COOKIE_NAME, DEFAULT_TTL_SECONDS, sign_session, verify_session,
)

from app.error_schema import ErrorResponse, api_error

log = logging.getLogger("rtvoice.auth.console")

router = APIRouter(prefix="/auth", tags=["auth"])

# ── 配置（环境变量）────────────────────────────────────────────────
ADMIN_USERNAME = os.environ.get("RTVOICE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("RTVOICE_ADMIN_PASSWORD", "rtvoice-admin")
_SESSION_SECRET_RAW = os.environ.get("RTVOICE_SESSION_SECRET", "").strip()
if not _SESSION_SECRET_RAW:
    log.warning(
        "RTVOICE_SESSION_SECRET 未设置，使用内置默认值（仅供开发）。"
        "生产环境务必设置一个随机长字符串，且各服务保持一致。"
    )
    _SESSION_SECRET_RAW = "rtvoice-dev-session-secret-change-me"
SESSION_SECRET = _SESSION_SECRET_RAW
COOKIE_SECURE = os.environ.get("RTVOICE_SESSION_COOKIE_SECURE", "true").lower() not in ("0", "false", "no")

# 全权限内部 admin key —— 会话映射目标，永不应被吊销
ADMIN_KEY_ID = "admin-console-internal"
ADMIN_KEY_SCOPES = ["stt", "tts", "tokens", "realtime", "admin"]


async def ensure_admin_key(store: Any) -> Key:
    """保证「全权限内部 admin key」存在、未吊销、scope 齐全；否则即时补建。

    会话 cookie 解析到的就是这枚 key 的 record（直接返 record，不走 secret 校验），
    所以它的 secret 用不到——随机生成一个 hash 占位即可。
    """
    existing = store.find_by_id(ADMIN_KEY_ID)
    if (
        existing is not None
        and existing.revoked_at is None
        and all(s in existing.scopes for s in ADMIN_KEY_SCOPES)
    ):
        return existing

    secret = secrets.token_urlsafe(32)  # 用不到，仅用于占位 hash
    key = Key(
        id=ADMIN_KEY_ID,
        secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        name="Admin Console (internal)",
        sessions_concurrent_max=10000,
        sessions_per_hour_max=1000000,
        scopes=list(ADMIN_KEY_SCOPES),
        created_at=datetime.now(timezone.utc),
        revoked_at=None,
        notes="auto-provisioned for admin console session; self-healing, do not revoke",
    )
    await store.put(key)
    log.warning("admin-console internal key (%s) (re)provisioned (self-heal)", ADMIN_KEY_ID)
    return key


# 跨服务调用 key（realtime 管线 + Admin Console 代理转发 STT/TTS/Token 时作 Bearer）。
# 它的 secret 就是 env RTVOICE_API_KEY；下游各服务用 verify_key(secret) 校验，
# 因此必须保证「与该 secret 同 hash 的 key」始终活跃且 scope 齐全——否则像 00:01
# 那次 keys 清理一样，会再次把它误吊销，导致 admin / realtime 链路集体掉权限。
INTERNAL_SERVICE_KEY_ID = "internal-service-auto"
INTERNAL_SERVICE_SCOPES = ["stt", "tts", "tokens", "realtime"]


async def ensure_internal_service_key(store: Any) -> Key | None:
    """保证 env RTVOICE_API_KEY 对应的跨服务 key 始终活跃、scope 齐全（自愈）。

    若该 hash 已有 key（如 internal-realtime）→ 复用其 id，仅在被吊销/缺 scope 时修复；
    否则新建 internal-service-auto。RTVOICE_API_KEY 未设置（dev 无鉴权）则跳过。
    """
    secret = os.environ.get("RTVOICE_API_KEY", "").strip()
    if not secret:
        return None
    h = hashlib.sha256(secret.encode()).hexdigest()
    existing = store.find_by_hash(h)
    if (
        existing is not None
        and existing.revoked_at is None
        and all(s in existing.scopes for s in INTERNAL_SERVICE_SCOPES)
    ):
        return existing

    key = Key(
        id=existing.id if existing is not None else INTERNAL_SERVICE_KEY_ID,
        secret_hash=h,
        name=existing.name if existing is not None else "internal-service (auto)",
        sessions_concurrent_max=10000,
        sessions_per_hour_max=1000000,
        scopes=list(INTERNAL_SERVICE_SCOPES),
        created_at=existing.created_at if existing is not None else datetime.now(timezone.utc),
        revoked_at=None,
        notes="auto-provisioned cross-service key (RTVOICE_API_KEY); self-healing, do not revoke",
    )
    await store.put(key)
    log.warning("internal-service key (%s) (re)provisioned (self-heal)", key.id)
    return key


async def admin_key_from_session(scope_holder: Any) -> Key | None:
    """若请求/WS 带有效会话 cookie，返回自愈后的全权限 admin key；否则 None。

    scope_holder 需有 .cookies（Request 或 WebSocket 均满足）和 .app.state.key_store。
    """
    token = None
    try:
        token = scope_holder.cookies.get(COOKIE_NAME)
    except Exception:
        return None
    payload = verify_session(token, SESSION_SECRET)
    if payload is None:
        return None
    store = scope_holder.app.state.key_store
    return await ensure_admin_key(store)


# ── HTTP 端点 ──────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class MeResponse(BaseModel):
    username: str


@router.post(
    "/login",
    response_model=MeResponse,
    summary="Admin 用户名/密码登录，下发 HttpOnly 会话 cookie",
    responses={401: {"model": ErrorResponse, "description": "凭证错误"}},
)
async def login(req: LoginRequest, request: Request, response: Response) -> MeResponse:
    user_ok = hmac.compare_digest(req.username, ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(req.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise api_error(401, "auth.invalid_credentials", "用户名或密码错误")
    # 登录即确保内部 admin key 就绪
    await ensure_admin_key(request.app.state.key_store)
    token = sign_session(ADMIN_USERNAME, SESSION_SECRET)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return MeResponse(username=ADMIN_USERNAME)


@router.post("/logout", summary="清除会话 cookie")
async def logout(response: Response) -> dict:
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.get(
    "/me",
    response_model=MeResponse,
    summary="返回当前登录的 admin（用于前端鉴权守卫）",
    responses={401: {"model": ErrorResponse, "description": "未登录或会话过期"}},
)
async def me(request: Request) -> MeResponse:
    payload = verify_session(request.cookies.get(COOKIE_NAME), SESSION_SECRET)
    if payload is None:
        raise api_error(401, "auth.not_authenticated", "未登录或会话已过期")
    return MeResponse(username=str(payload.get("sub", ADMIN_USERNAME)))
