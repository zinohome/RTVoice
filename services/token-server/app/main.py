"""RTVoice Token Server.

给浏览器/客户端签发 LiveKit JWT。

鉴权（v0.1）：
    /token 端点要求 HTTP header `Authorization: Bearer <APP_API_KEY>`。
    APP_API_KEY 从环境变量读取，长度 ≥ 32。
    这是简单的"共享 API key"模型，不是用户级身份。
    v0.6+ 计划替换为真实用户认证。

环境变量：
    LIVEKIT_API_KEY        LiveKit 服务端密钥（用于签 JWT）
    LIVEKIT_API_SECRET     LiveKit 服务端密钥
    APP_API_KEY            客户端访问 /token 的共享 key（≥ 32 字符）
    LIVEKIT_PUBLIC_URL     浏览器侧用的 ws/wss URL
    DEV_AUTO_INJECT_KEY    "true" 时把 APP_API_KEY 注入测试页（仅 dev 用）
    LOG_LEVEL              DEBUG/INFO/WARNING/ERROR
"""

# 不能加 `from __future__ import annotations`！
# FastAPI + Pydantic v2 用 forward ref 解析时会把 Annotated[X, Body()] 当成 Query，
# 导致 schema 生成 500 + /token 422。Python 3.11 下 Annotated 原生可用，无须 future。

import hmac
import logging
import os
import re
from datetime import timedelta
from pathlib import Path

from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel, Field
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("rtvoice.token")


def _require_env(name: str, min_len: int = 1) -> str:
    val = os.environ.get(name, "")
    if len(val) < min_len:
        raise RuntimeError(
            f"环境变量 {name} 未设置或过短（需 ≥{min_len} 字符）。"
            "请检查 .env 文件。"
        )
    return val


LIVEKIT_API_KEY = _require_env("LIVEKIT_API_KEY", min_len=4)
LIVEKIT_API_SECRET = _require_env("LIVEKIT_API_SECRET", min_len=16)
APP_API_KEY = _require_env("APP_API_KEY", min_len=32)
LIVEKIT_PUBLIC_URL = os.environ.get("LIVEKIT_PUBLIC_URL", "ws://127.0.0.1:7880")
DEV_AUTO_INJECT_KEY = os.environ.get("DEV_AUTO_INJECT_KEY", "false").lower() == "true"
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="RTVoice Token Server",
    version="0.5.0",
    description="为客户端签发 LiveKit JWT。共享 API key 鉴权 + slowapi rate limit。",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _rate_limit_dep(request: Request) -> None:
    """限流占位（v0.5+ 暂禁用）。

    历史：尝试过 @limiter.limit 装饰器（破坏 FastAPI 内省）和
    limiter._check_request_limit（私有 API 签名错，'str' has no __module__）。
    都翻车。slowapi 0.1.9 + FastAPI 0.115 的稳定 Depends 模式需要更深整合。

    voice agent 场景下限流应该放在反向代理（nginx/Caddy）层，
    应用内限流是次优方案。v0.7+ 上 Caddy 后这个 Depends 直接删除。
    """
    return None

# Prometheus：自动 http_request_duration / http_requests_total + 自定义 counter
TOKENS_ISSUED = Counter("rtvoice_tokens_issued_total", "Total LiveKit JWTs issued",
                        ["room"])
AUTH_FAILURES = Counter("rtvoice_token_auth_failures_total", "401 responses on /token",
                        ["reason"])
Instrumentator(
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# auto_error=False：自己控制 401 返回格式，统一错误结构
_bearer = HTTPBearer(auto_error=False, description="APP_API_KEY")


def require_api_key(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if creds is None or creds.scheme.lower() != "bearer":
        AUTH_FAILURES.labels(reason="missing").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 常量时间比较，防 timing attack
    if not hmac.compare_digest(creds.credentials, APP_API_KEY):
        AUTH_FAILURES.labels(reason="invalid").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


class TokenRequest(BaseModel):
    room: str = Field(..., description="房间名，[A-Za-z0-9_-]{1,64}")
    identity: str = Field(..., description="参与者唯一标识，[A-Za-z0-9_-]{1,64}")


class TokenResponse(BaseModel):
    token: str
    url: str
    room: str
    identity: str


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
def index() -> HTMLResponse:
    """返回测试页面。

    若 DEV_AUTO_INJECT_KEY=true，把 APP_API_KEY 注入页面 meta，
    页面 JS 会自动使用，无需用户手动粘贴。仅 dev 安全。
    """
    index_html = STATIC_DIR / "index.html"
    if not index_html.is_file():
        return HTMLResponse(
            "<h1>RTVoice token-server</h1><p>静态测试页未找到。</p>",
            status_code=200,
        )
    html = index_html.read_text(encoding="utf-8")
    if DEV_AUTO_INJECT_KEY:
        # 注入到一个 meta 标签，JS 读取
        injected = (
            f'<meta name="rtvoice-dev-api-key" content="{APP_API_KEY}">\n'
            '<meta name="rtvoice-dev-mode" content="true">\n'
        )
        html = html.replace("<!--RTVOICE_DEV_INJECT-->", injected)
    return HTMLResponse(html)


@app.get("/health")
def health() -> dict[str, str]:
    """无鉴权健康检查（供 docker healthcheck / 监控）。"""
    return {"status": "ok"}


@app.post(
    "/token",
    response_model=TokenResponse,
    dependencies=[Depends(require_api_key), Depends(_rate_limit_dep)],
)
def issue_token(
    request: Request,
    req: Annotated[TokenRequest, Body()],
) -> TokenResponse:
    if not _NAME_RE.match(req.room):
        raise HTTPException(400, "room 仅允许 [A-Za-z0-9_-]，长度 1-64")
    if not _NAME_RE.match(req.identity):
        raise HTTPException(400, "identity 仅允许 [A-Za-z0-9_-]，长度 1-64")

    grants = api.VideoGrants(
        room_join=True,
        room=req.room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(req.identity)
        .with_name(req.identity)
        .with_grants(grants)
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )

    TOKENS_ISSUED.labels(room=req.room).inc()
    log.info(
        "token issued: room=%s identity=%s client=%s",
        req.room,
        req.identity,
        request.client.host if request.client else "?",
    )
    return TokenResponse(
        token=token,
        url=LIVEKIT_PUBLIC_URL,
        room=req.room,
        identity=req.identity,
    )


@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=exc.headers or {},
    )
