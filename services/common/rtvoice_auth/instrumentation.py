"""SP10 G3 — FastAPI middleware: 录 rtvoice_requests_total + rtvoice_request_duration_seconds。

集成方式（每个 service main.py）::

    from rtvoice_auth.instrumentation import RequestMetricsMiddleware
    app.add_middleware(RequestMetricsMiddleware, service_name="stt-server")

key_id 来源约定：
- 业务依赖（require_key）里 `request.state.key_id = key.id`
- middleware 在 response 阶段读 `request.state.key_id`；不存在 → "anonymous"
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from rtvoice_auth.metrics import REQUESTS_TOTAL, REQUEST_DURATION_SECONDS
from rtvoice_auth.metrics_labels import safe_key_id, ANONYMOUS

# /metrics /health 不应跑指标记录（避免自激励 + 噪声）
_EXCLUDED_PATHS = {"/metrics", "/health"}


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str) -> None:
        super().__init__(app)
        self.service = service_name

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path in _EXCLUDED_PATHS:
            return await call_next(request)
        t0 = time.perf_counter()
        status = "500"
        try:
            resp = await call_next(request)
            status = str(resp.status_code)
            return resp
        except Exception:
            status = "500"
            raise
        finally:
            elapsed = time.perf_counter() - t0
            key_id = safe_key_id(getattr(request.state, "key_id", None))
            # endpoint 取 path（不取 route.path_template，防止 schema 变化 break label）
            REQUESTS_TOTAL.labels(
                service=self.service, endpoint=path, key_id=key_id, status=status,
            ).inc()
            REQUEST_DURATION_SECONDS.labels(
                service=self.service, endpoint=path, key_id=key_id,
            ).observe(elapsed)
