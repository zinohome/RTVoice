"""统一错误响应 Pydantic schema + helper（与 CONVENTIONS.md §6 一致）"""

from typing import Literal
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None


def api_error(status: int, code: str, message: str) -> HTTPException:
    e = HTTPException(status_code=status, detail=message)
    e.error_code = code
    return e


def http_exception_handler():
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
