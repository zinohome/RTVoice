# token-server

**职责**：给浏览器/客户端签发 LiveKit JWT。客户端先 HTTP 请求 token，再用 token 通过 WebRTC 加入房间。

**技术栈**：Python 3.11 + FastAPI + livekit-server-sdk-python

**端口**：`${TOKEN_SERVER_PORT}`（默认 8000）

**对外暴露**：dev 仅 127.0.0.1；prod 由用户在 `.env` 决定

**依赖环境变量**：`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

**待实现**：v0.1
