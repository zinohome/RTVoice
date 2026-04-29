# token-server

**职责**：给浏览器/客户端签发 LiveKit JWT。客户端先 HTTP 请求 token，再用 token 通过 WebRTC 加入房间。

**技术栈**：Python 3.11 + FastAPI + livekit-api（livekit-server-sdk-python）

**端口**：`${TOKEN_SERVER_PORT}`（默认 8000）

**对外暴露**：dev 仅 127.0.0.1；prod 由用户在 `.env` 决定

**依赖环境变量**：`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `APP_API_KEY`（≥32）, `LIVEKIT_PUBLIC_URL`, `DEV_AUTO_INJECT_KEY`

**状态**：✅ v0.1 已实现

## 接口

| Method | Path | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/` | ❌ | 返回测试页（dev 模式自动注入 API key） |
| GET | `/health` | ❌ | 健康检查（供 docker / 监控） |
| POST | `/token` | ✅ Bearer | 签发 JWT |
| GET | `/static/*` | ❌ | 静态资源 |

### 鉴权

`/token` 要求 HTTP header：

```
Authorization: Bearer <APP_API_KEY>
```

`APP_API_KEY` 在 `.env` 中配置，**必须 ≥ 32 字符**。生成方式：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 或
openssl rand -base64 32
```

服务端用 `hmac.compare_digest` 做常量时间比较，防 timing 攻击。

### POST /token

请求：
```http
POST /token HTTP/1.1
Authorization: Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json

{ "room": "rtvoice-test", "identity": "user-1" }
```

响应（200）：
```json
{
  "token": "eyJhbGc...",
  "url": "ws://127.0.0.1:7880",
  "room": "rtvoice-test",
  "identity": "user-1"
}
```

错误响应（401）：
```json
{ "error": "Missing Authorization: Bearer header" }
```

**校验**：`room` 与 `identity` 仅允许 `[A-Za-z0-9_-]{1,64}`。

### 测试页注入机制

`GET /` 返回 `static/index.html`。当 `DEV_AUTO_INJECT_KEY=true` 时，
服务端把 `APP_API_KEY` 注入到 HTML `<meta>`，浏览器 JS 自动填充输入框。

仅 dev 模式开启此行为（依赖 `BIND_HOST=127.0.0.1` 防外泄）。
**生产部署必须 `DEV_AUTO_INJECT_KEY=false`**——客户端需手动配置 API key。

## v0.1 已知限制（生产前需进一步加固）

- ✅ ~~无身份认证~~ → v0.1 已加共享 API key Bearer 鉴权
- ⚠️ **无用户级身份**：APP_API_KEY 是单一共享密钥，所有客户端共享。无法吊销单个用户、无法审计"是谁加入了哪个房间"。v0.6+ 计划接真用户系统（OIDC/账号密码 + per-user API key）。
- ⚠️ **无 rate limit**：API key 泄露后可被滥用。生产加 nginx/Caddy 限流或代码层面 slowapi。
- ⚠️ **JWT TTL 1 小时硬编码**：生产应根据场景调整（短会议短 TTL、长任务长 TTL）。
- ⚠️ **room 名无白名单**：客户端可任意创建房间名，可能被刷资源。生产应配房间配额。

## 后续目录结构（v0.2+）

```
token-server/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py                # 当前 v0.1
│   ├── auth.py                # v0.6+ 用户鉴权
│   └── ratelimit.py           # v0.6+ 限流
├── static/
│   └── index.html             # 当前 v0.1 测试页
└── tests/
```
