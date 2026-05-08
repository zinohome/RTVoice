"""统一错误响应 Pydantic schema + helper（per-service copy；与 CONVENTIONS.md §6 一致）"""

from typing import Literal
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None


def api_error(status: int, code: str, message: str) -> HTTPException:
    """raise api_error(404, 'stt.not_found', 'X')"""
    e = HTTPException(status_code=status, detail=message)
    e.error_code = code
    return e


def http_exception_handler():
    """returns FastAPI exception handler that uses ErrorResponse schema"""
    async def handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "error",
                "code": getattr(exc, "error_code", "internal.unknown"),
                "message": str(exc.detail),
                "request_id": request.headers.get("X-Request-ID"),
            },
        )
    return handler


def validation_exception_handler():
    """returns FastAPI handler for 422 Pydantic validation errors using ErrorResponse schema.

    Without this, FastAPI returns its default {detail: [{type, loc, msg, input}]} format
    which doesn't match CONVENTIONS.md §6. We collapse the first error into a human msg.
    """
    async def handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
            msg = f"{loc}: {first.get('msg', 'validation failed')}" if loc else first.get("msg", "validation failed")
        else:
            msg = "validation failed"
        return JSONResponse(
            status_code=422,
            content={
                "type": "error",
                "code": "validation.invalid_request",
                "message": msg,
                "request_id": request.headers.get("X-Request-ID"),
            },
        )
    return handler
