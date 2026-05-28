"""SP14 — Admin HTTP API: keys 生命周期 over HTTP（替代 rtvoice-admin CLI）。

所有 endpoint 鉴权 scope='admin'：require_key 依赖会查 key.scopes 含 'admin' 否则 403。

UI 调用方式：localStorage 存 admin key secret，每请求带 Authorization: Bearer <secret>。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import InvalidToken, TokenRevoked, ScopeDenied

from app.error_schema import ErrorResponse, api_error

# admin CLI 已有逻辑，直接复用
from rtvoice_admin.commands import (
    cmd_create, cmd_list, cmd_show, cmd_revoke, cmd_rotate,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


async def require_admin_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Key:
    """Require Bearer key with scope='admin'，或 Admin Console 会话 cookie。"""
    # 延迟导入避免循环依赖（auth_api 仅 import error_schema）
    from app.auth_api import admin_key_from_session
    session_key = await admin_key_from_session(request)
    if session_key is not None:
        request.state.key_id = session_key.id
        return session_key
    if not authorization or not authorization.startswith("Bearer "):
        raise api_error(401, "auth.missing_token", "Authorization: Bearer required")
    secret = authorization[len("Bearer "):]
    try:
        key = await verify_key(secret, scope="admin",
                               store=request.app.state.key_store)
        request.state.key_id = key.id
        return key
    except InvalidToken as e:
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        raise api_error(403, e.code, e.message)


class KeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scopes: list[str] = Field(..., min_length=1,
                              description="任意子集 of [stt,tts,tokens,realtime,admin]")
    sessions_concurrent: int = Field(5, ge=1, le=100)
    sessions_per_hour: int = Field(100, ge=1, le=10000)
    notes: str = Field("", max_length=500)


class KeyCreateResponse(BaseModel):
    id: str
    secret: str  # only-once display
    name: str
    sessions_concurrent_max: int
    sessions_per_hour_max: int
    scopes: list[str]


class KeySummary(BaseModel):
    id: str
    name: str
    sessions_concurrent_max: int
    sessions_per_hour_max: int
    scopes: list[str]
    created_at: str
    revoked_at: str | None
    legacy: bool
    notes: str = ""


@router.get(
    "/keys",
    response_model=list[KeySummary],
    summary="List all keys (no secrets)",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_keys(
    request: Request,
    _admin: Key = Depends(require_admin_key),
) -> list[KeySummary]:
    rows = await cmd_list(request.app.state.key_store)
    return [KeySummary(**r) for r in rows]


@router.post(
    "/keys",
    response_model=KeyCreateResponse,
    status_code=201,
    summary="Create a new key (secret displayed only once)",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)
async def create_key(
    req: KeyCreateRequest,
    request: Request,
    _admin: Key = Depends(require_admin_key),
) -> KeyCreateResponse:
    result = await cmd_create(
        request.app.state.key_store,
        name=req.name,
        sessions_concurrent=req.sessions_concurrent,
        sessions_per_hour=req.sessions_per_hour,
        scopes=req.scopes,
        notes=req.notes,
    )
    return KeyCreateResponse(**result)


@router.get(
    "/keys/{key_id}",
    response_model=KeySummary,
    summary="Show single key detail",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               404: {"model": ErrorResponse}},
)
async def show_key(
    key_id: str,
    request: Request,
    _admin: Key = Depends(require_admin_key),
) -> KeySummary:
    row = await cmd_show(request.app.state.key_store, key_id=key_id)
    if row is None:
        raise api_error(404, "admin.key_not_found", f"key {key_id} not found")
    return KeySummary(**row)


@router.post(
    "/keys/{key_id}/revoke",
    summary="Revoke key (idempotent; revoked keys cannot auth)",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               404: {"model": ErrorResponse}},
)
async def revoke_key(
    key_id: str,
    request: Request,
    _admin: Key = Depends(require_admin_key),
) -> dict:
    ok = await cmd_revoke(request.app.state.key_store, key_id=key_id)
    if not ok:
        raise api_error(404, "admin.key_not_found", f"key {key_id} not found")
    return {"id": key_id, "revoked": True}


class KeyRotateResponse(BaseModel):
    id: str
    secret: str  # new secret, only-once display


@router.post(
    "/keys/{key_id}/rotate",
    response_model=KeyRotateResponse,
    summary="Rotate secret (new secret displayed only once; old secret invalid immediately)",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               404: {"model": ErrorResponse}},
)
async def rotate_key(
    key_id: str,
    request: Request,
    _admin: Key = Depends(require_admin_key),
) -> KeyRotateResponse:
    try:
        result = await cmd_rotate(request.app.state.key_store, key_id=key_id)
    except KeyError:
        raise api_error(404, "admin.key_not_found", f"key {key_id} not found")
    return KeyRotateResponse(**result)
