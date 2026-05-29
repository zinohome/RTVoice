# Admin API

> API Key 生命周期管理。鉴权 scope 必须包含 `admin`。

Admin Console UI（`/admin-v2/`）内置了这些操作的可视化界面，API 文档面向程序化调用场景。

## 认证

所有 Admin API 端点需要以下任一方式鉴权：

1. **Bearer Token**（程序化调用）：`Authorization: Bearer <admin_key_secret>`
2. **Admin Console 会话 Cookie**（浏览器登录后自动携带，无需额外处理）

> **说明**：Admin Console 登录端点在 `/v1/auth/login`，登录成功后设置 HttpOnly 会话 Cookie，浏览器自动携带，不需要手动管理 Token。

## Endpoints 速查

| 用途 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 列出所有 Key | GET | `/v1/admin/keys` | 不含 secret |
| 创建新 Key | POST | `/v1/admin/keys` | secret 仅展示一次 |
| 查询单个 Key | GET | `/v1/admin/keys/{key_id}` | |
| 吊销 Key | POST | `/v1/admin/keys/{key_id}/revoke` | 幂等 |
| 轮转 Key Secret | POST | `/v1/admin/keys/{key_id}/rotate` | 旧 secret 立即失效 |

---

## GET /v1/admin/keys

列出所有 API Key（不含 secret）。

### 响应

```json
[
  {
    "id": "key_abc123",
    "name": "前端应用",
    "sessions_concurrent_max": 5,
    "sessions_per_hour_max": 100,
    "scopes": ["realtime"],
    "created_at": "2026-05-01T00:00:00Z",
    "revoked_at": null,
    "legacy": false,
    "notes": ""
  }
]
```

---

## POST /v1/admin/keys

创建新 Key。**secret 字段仅在此响应中展示一次，请立即保存**。

### 请求 Body

```json
{
  "name": "前端应用",
  "scopes": ["realtime"],
  "sessions_concurrent": 5,
  "sessions_per_hour": 100,
  "notes": "可选备注"
}
```

### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | — | 必填，1-64 字符 |
| `scopes` | string[] | — | 必填，从 `[stt, tts, tokens, realtime, admin]` 中选 |
| `sessions_concurrent` | int | 5 | 1-100，该 key 最大并发 session 数 |
| `sessions_per_hour` | int | 100 | 1-10000，该 key 每小时最大 session 数 |
| `notes` | string | "" | 备注，最多 500 字符 |

### 响应（201 Created）

```json
{
  "id": "key_abc123",
  "secret": "rtv_xxxxxxxxxxxxxxxx",
  "name": "前端应用",
  "sessions_concurrent_max": 5,
  "sessions_per_hour_max": 100,
  "scopes": ["realtime"]
}
```

### Scope 说明

| Scope | 用途 |
|-------|------|
| `stt` | 访问 STT WebSocket `/v1/asr` |
| `tts` | 访问 TTS API `/v1/tts/stream`，`/v1/voices` |
| `tokens` | 访问 Token Server `/v1/tokens` |
| `realtime` | 创建 Realtime Session，访问 `/v1/sessions` + `/v1/realtime/*` |
| `admin` | 访问 Admin API `/v1/admin/*`，拥有全部权限 |

---

## GET /v1/admin/keys/{key_id}

查询单个 Key 详情。

```bash
curl https://SERVER_IP/v1/admin/keys/key_abc123 \
  -H "Authorization: Bearer <admin_secret>"
```

---

## POST /v1/admin/keys/{key_id}/revoke

吊销 Key（幂等）。被吊销的 Key 立即无法鉴权。

```bash
curl -X POST https://SERVER_IP/v1/admin/keys/key_abc123/revoke \
  -H "Authorization: Bearer <admin_secret>"
```

响应：

```json
{"id": "key_abc123", "revoked": true}
```

---

## POST /v1/admin/keys/{key_id}/rotate

轮转 Key 的 secret。旧 secret **立即失效**，新 secret 仅展示一次。

```bash
curl -X POST https://SERVER_IP/v1/admin/keys/key_abc123/rotate \
  -H "Authorization: Bearer <admin_secret>"
```

响应：

```json
{
  "id": "key_abc123",
  "secret": "rtv_new_xxxxxxxxxxxxxxxx"
}
```

---

## Admin Console 登录 API

> 通常无需直接调用，浏览器访问 `/admin-v2/` 自动处理登录流程。

### POST /v1/auth/login

```json
POST /v1/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "RTVoice@2026"
}
```

成功响应 200，设置 HttpOnly `rtvoice_session` Cookie，有效期 24 小时。

### POST /v1/auth/logout

清除会话 Cookie：

```bash
curl -X POST https://SERVER_IP/v1/auth/logout \
  -b "rtvoice_session=<cookie_value>"
```

---

## 错误码

| Code | HTTP | 含义 |
|------|------|------|
| `auth.missing_token` | 401 | 缺少 Authorization header |
| `auth.invalid_token` | 401 | token 无效 |
| `auth.revoked_token` | 401 | token 已吊销 |
| `auth.scope_denied` | 403 | token 缺少 admin scope |
| `admin.key_not_found` | 404 | key_id 不存在 |
| `validation.invalid_request` | 422 | 请求体格式错误 |
