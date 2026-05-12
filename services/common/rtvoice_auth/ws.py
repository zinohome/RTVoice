"""WebSocket helper: 从客户端 Sec-WebSocket-Protocol 头挑出 bearer.<token>
项以回传给 websocket.accept(subprotocol=...)。

RFC 6455 §4.2.2：服务器在 101 响应中必须 echo 客户端提案过的某个 subprotocol。
不 echo → Chrome / Firefox 按规范关闭连接 (close code 1006)。

历史 bug (SP8 D4-F4)：3 个 service 用 sec-websocket-protocol 收 bearer.<token>
但 accept() 不传 subprotocol，浏览器永远连不上。这个 helper 是唯一修法。
"""
from __future__ import annotations

from typing import Protocol


class _WSLike(Protocol):
    headers: dict[str, str] | object  # starlette Headers 等价


def pick_bearer_subprotocol(ws: _WSLike) -> str | None:
    """从 ws.headers["sec-websocket-protocol"] 中取首个 bearer.* 项返回。

    返回值直接传给 `await ws.accept(subprotocol=...)`：
    - 含 bearer.<token> → 返回 "bearer.<token>" 原文，浏览器握手成功
    - 无 bearer 子协议（如 Bearer 走 Authorization header / query token） → 返 None
    """
    raw = ws.headers.get("sec-websocket-protocol", "")
    if not raw:
        return None
    for p in (s.strip() for s in raw.split(",")):
        if p.startswith("bearer."):
            return p
    return None
