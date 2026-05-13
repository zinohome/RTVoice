# SP14 — AdminUI v1 设计

**目标**：内部运维 + 三方对接调试一站式 UI；任何新功能上线先在这里测。

**部署**：realtime-server `/static/admin/`（B 方案）；Caddy 路由 `/admin/*` + 4 个 service `/docs` 反代。

**鉴权**：admin key（新 scope=`admin`）+ localStorage（前端纯客户端）。CSRF 不需要（admin key === API token）。

**技术栈**：单文件 HTML + Alpine.js（CDN，无 build step）+ Tailwind CDN。

## 范围

### Backend (rtvoice_auth + realtime-server)

- 新增 scope **`admin`**（rtvoice_auth/verify.py 已通用化，无需改 — admin CLI create 时加 `--scopes admin`）
- realtime-server 加新模块 `app/admin_api.py`：
  - `GET /v1/admin/keys` — list（不含 secret_hash；require admin scope）
  - `POST /v1/admin/keys` — create + 返 secret（only-once）
  - `GET /v1/admin/keys/{id}` — show
  - `POST /v1/admin/keys/{id}/revoke` — revoke
  - 全部 require_key with scope="admin"

### Caddy

- 加路由：
  - `/admin/*` → realtime-server `/static/admin/*`（已 mount，加一层即可）
  - `/v1/admin/*` → realtime-server
  - `/swagger/{token|realtime|stt|tts}` → 各 service `/docs` (反代 + rewrite)

### Frontend `services/realtime-server/static/admin/`（实际从 `clients/web-admin/` 拷贝进镜像）

- `index.html` + `app.js` + `style.css`
- 7 个 tab（左侧菜单切换）：
  1. **Monitor** — 4 service /info 状态卡 + Prometheus 关键 query 摘要 + Grafana link
  2. **Keys** — table list + 创建表单 + revoke 按钮
  3. **Voices** (TTS admin) — 上传 wav + 列音色 + 删
  4. **Test STT** — 上传 wav 或浏览器录音 → 显示 partial/final
  5. **Test TTS** — 文本 + voice + speed → 播放
  6. **Test Realtime** — 完整对话流（复用 clients/web/realtime.js 逻辑）
  7. **Test Tokens** — 调 /v1/tokens 看 JWT decode
  8. **Swagger** — 4 个 service link 卡片

## Done 标准

- ✅ realtime tests + common tests + e2e smoke 全过
- ✅ prod 部署后 `https://192.168.66.163/admin/` 浏览器打开看到 UI
- ✅ 用 admin scope key 登录后能 list / create / revoke key
- ✅ Monitor tab 显示 4 service 状态
- ✅ Swagger link 跳得通

## 不做

- 多用户 / 多组织（v1 单 admin key 模型）
- 角色 RBAC（admin = all-or-nothing）
- 历史 audit log UI（数据在 Prometheus + JSONL audit 文件里，先 link Grafana）
- 多语言（中文 only）

## 估时

~3 天 / 9 T。
