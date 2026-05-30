# SP6 Multi-Tenant Auth — API Key per App + Quota + Admin CLI Design

**日期**：2026-05-10
**前置**：SP5 (v0.12.0) 已 prod；4 服务 + Web demo + monitoring + SDK；CORS 默认 `*`；单一 `RTVOICE_API_KEY` 共享。
**目标版本**：v0.13.0
**作用域**：把 RTVoice 从"单 key 共享"推进到"多 key 多租户 ready"——每下游应用独立 key + 元数据 + 配额 + admin CLI 管理。

---

## 1. 目标

SP1-SP5 全 platform-side 工程；SP5 后 platform 完整可用。SP6 是"真用户上线前的多租户准备"——CozyVoice 即将切 SDK，且未来会有更多下游应用，单一共享 `RTVOICE_API_KEY` 不够撑：

- 多个 app 用同一 key → 撤销不便、滥用难追溯、日志按 app 分不开
- 任意 app 误用即全 platform 挂掉风险
- prod 流量级别监控/限流缺基础（SP5 metrics 是按服务，不是按 key）

SP6 解决：
- 每 app 独立 key + 元数据（quota / scopes）
- admin CLI 管理（create / list / revoke / rotate）
- 强制基础执行：sessions_concurrent + sessions_per_hour
- Hard cutover + 自动迁移 legacy `RTVOICE_API_KEY`，prod 升级零停机

---

## 2. 关键决策（D-2026-05-10-D.1~D.6）

| ID | 决策 | 理由 |
|---|---|---|
| **D.1** | 模型：API Key per App（不做 OAuth2 / JWT 用户级） | 应用自管用户；RTVoice 不重复造用户系统；后续可加混合 |
| **D.2** | Store backend：YAML（dev）+ Redis（prod）双形态，env `RTVOICE_KEYS_BACKEND` 切换 | dev 简单零依赖；prod 跨 3 服务共享 store + counter 复用 |
| **D.3** | 管理：CLI 工具 `rtvoice-admin`（无 admin HTTP endpoint） | 攻击面小；`docker exec` 直跑；与 SDK / web 客户端解耦 |
| **D.4** | 兼容：Hard cutover + 服务启动时自动迁移 `RTVOICE_API_KEY` 为 legacy-default key | prod 升级零停机；admin 后续按需替换 |
| **D.5** | Quota 强度：基础执行（sessions_concurrent + sessions_per_hour），429 + `auth.quota_*` | 防滥用必需；token bucket 留 SP7+ |
| **D.6** | token-server 现有 slowapi（IP rate limit）+ 新 per-key quota 共存 | 不同维度：IP 防 DDoS / key 防业务滥用 |

---

## 3. 架构 & 文件布局

```
RTVoice/
├── services/
│   ├── rtvoice-admin/                  ← 新建：admin CLI
│   │   ├── pyproject.toml              hatchling，console_script: rtvoice-admin
│   │   ├── src/rtvoice_admin/
│   │   │   ├── __init__.py             __version__
│   │   │   ├── __main__.py             argparse 入口
│   │   │   ├── commands.py             create / list / revoke / rotate / show / import-legacy
│   │   │   └── format.py               table / json 输出
│   │   └── tests/
│   │
│   ├── common/                         ← 新建：3 服务共享 auth lib
│   │   └── rtvoice_auth/
│   │       ├── __init__.py
│   │       ├── models.py               Pydantic v2 Key
│   │       ├── store.py                抽象基类 + YAML backend
│   │       ├── store_redis.py          Redis backend
│   │       ├── verify.py               verify_key + require_key (FastAPI dep)
│   │       ├── quota.py                QuotaTracker (acquire/release/inc_hour)
│   │       ├── errors.py               AuthError / QuotaExceeded
│   │       └── tests/                  T1-T5 测试在此
│   │
│   ├── realtime-server/app/main.py     ★ POST /v1/sessions 用 require_key + quota
│   ├── realtime-server/app/session_manager.py  ★ cleanup release_session
│   ├── stt-server/app/main.py          ★ /v1/asr 加 require_key (scope=stt)
│   ├── tts-server/app/main.py          ★ scope=tts
│   ├── tts-server/app/main_cosyvoice.py ★ 同
│   ├── tts-server/app/main_cosyvoice3.py ★ 同（admin endpoints 走独立 admin key 不变）
│   └── token-server/app/main.py        ★ require_key 替代 hmac；slowapi 保留
│
├── data/keys.yaml                       dev 默认 store；compose volume mount
├── docker-compose.yml                   ★ +redis 容器（profile=auth-redis）；
│                                          3 服务 mount keys.yaml + env RTVOICE_KEYS_BACKEND
└── OPERATIONS.md                        ★ §7 admin CLI 用法 + 迁移 checklist
```

**新文件**：~16（admin 包 + auth lib + 测试 + keys.yaml 占位）
**修改**：6 服务 main.py + compose
**新依赖**：`pyyaml`（FastAPI 间接依赖已含）；prod profile 加 `redis>=5`；admin CLI 用 `argparse`（stdlib，零外部）+ `pyyaml`
**copy-paste vs shared lib**：rtvoice_auth 是真共享 lib（验证逻辑必须 3 服务一致）；通过 Dockerfile `COPY services/common /app/common` + `PYTHONPATH=/app/common:/app` 让所有 service 能 `import rtvoice_auth`

---

## 4. 子项详细设计

### 4.1 Key 数据模型

```python
class Key(BaseModel):
    id: str               # "key_<token_urlsafe(12)>" — 公开标识
    secret_hash: str      # sha256(plaintext_secret) hex
    name: str             # 人可读，如 "cozyvoice"
    sessions_concurrent_max: int = 5
    sessions_per_hour_max: int = 100
    scopes: list[str] = ["stt", "tts", "realtime", "tokens"]
    created_at: datetime
    revoked_at: datetime | None = None
    notes: str = ""
    legacy: bool = False  # auto-migrated from RTVOICE_API_KEY
```

**安全模型**：
- 创建时返 plaintext secret 一次（用户必须立即保存）
- store 仅存 sha256 hex；验证用 `hmac.compare_digest`
- secret 用 `secrets.token_urlsafe(32)`（256 位熵）

### 4.2 YAML 文件 / Redis schema

YAML（`data/keys.yaml`）：
```yaml
version: 1
keys:
  - id: key_aBc123XyZ_45
    secret_hash: 8f3a4b1c...
    name: legacy-default
    sessions_concurrent_max: 10
    sessions_per_hour_max: 1000
    scopes: [stt, tts, realtime, tokens]
    created_at: "2026-05-10T08:00:00Z"
    legacy: true
```

Redis：
- `HSET rtvoice:key:{id} secret_hash {hex} name {name} ...`
- `SET rtvoice:hash2id:{secret_hash} {key_id}` —— 反查 O(1)
- `INCR rtvoice:quota:{key_id}:hour:{YYYYMMDDHH}` + EXPIRE 7200
- `INCR/DECR rtvoice:concurrent:{key_id}`

### 4.3 admin CLI 命令

```bash
rtvoice-admin create --name cozyvoice \
    --sessions-concurrent 5 --sessions-per-hour 200 \
    --scopes stt,tts,realtime
# 返：key_id + plaintext secret（仅此一次）

rtvoice-admin list                           # 表格输出（不显 secret）
rtvoice-admin show key_xK9mNp2Q_88           # 详情 + 当前 quota 用量
rtvoice-admin revoke key_xK9mNp2Q_88         # 软删除（revoked_at 设 now）
rtvoice-admin rotate key_xK9mNp2Q_88         # 重生成 secret，旧立即失效
rtvoice-admin import-legacy                  # RTVOICE_API_KEY → keys store
```

backend 切换：`RTVOICE_KEYS_BACKEND=redis|yaml rtvoice-admin ...`

### 4.4 验证流程

```python
async def verify_key(secret: str, *, scope: str, store: KeyStore) -> Key:
    """
    1. provided_hash = sha256(secret).hex
    2. record = store.find_by_hash(provided_hash)
    3. None → AuthError(invalid_token)
    4. revoked_at → AuthError(token_revoked)
    5. scope not in record.scopes → AuthError(scope_denied)
    6. return record
    """

# FastAPI dep（每 service main.py 用）
async def require_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Key:
    if not authorization or not authorization.startswith("Bearer "):
        raise api_error(401, "auth.missing_token", "Authorization: Bearer required")
    secret = authorization[7:]
    return await verify_key(secret,
                             scope=request.app.state.scope,
                             store=request.app.state.key_store)
```

3 服务 lifespan 设 `app.state.scope = "stt"|"tts"|"realtime"|"tokens"` + `app.state.key_store = init_store()`。

### 4.5 Quota 执行

```python
class QuotaTracker:
    async def acquire_session(self, key: Key) -> None:
        """create session 前调；超限 raise QuotaExceeded（rollback counter）."""

    async def release_session(self, key_id: str) -> None:
        """session cleanup 时调；DECR concurrent."""
```

**Realtime-server 集成**（`POST /v1/sessions`）：

```python
async def create_session(req, key: Key = Depends(require_key)):
    try:
        await app.state.quota.acquire_session(key)
    except QuotaExceeded as e:
        raise api_error(429, e.code, e.message)
    try:
        sess = await session_mgr.create(creator_key_hash=key.id, ...)
        sess.key_id = key.id
    except CapacityFull as e:
        await app.state.quota.release_session(key.id)
        raise api_error(503, "session.capacity_full", str(e))
    return ...
```

`SessionManager.cleanup()` 末尾：

```python
if sess.key_id:
    await self._quota.release_session(sess.key_id)
```

### 4.6 错误码（CONVENTIONS.md §6 新增）

| Code | HTTP | 含义 |
|---|---|---|
| `auth.missing_token` | 401 | 沿用 |
| `auth.invalid_token` | 401 | sha256 不匹配 |
| `auth.token_revoked` | 401 | revoked_at 已设 |
| `auth.scope_denied` | 403 | 不含当前 service scope |
| `auth.quota_per_hour` | 429 | 1 小时新建 session 数超 |
| `auth.quota_concurrent` | 429 | 当前活跃 session 超 |

### 4.7 Auto-migrate Legacy（lifespan 启动时）

```python
async def init_key_store():
    store = make_store(...)
    await store.load()
    legacy = os.environ.get("RTVOICE_API_KEY", "")
    if not store.any_keys() and legacy:
        await store.put(Key(
            id=f"key_{secrets.token_urlsafe(12)}",
            secret_hash=hashlib.sha256(legacy.encode()).hexdigest(),
            name="legacy-default",
            sessions_concurrent_max=10,
            sessions_per_hour_max=1000,
            scopes=["stt", "tts", "realtime", "tokens"],
            created_at=datetime.utcnow(),
            legacy=True,
        ))
        log.warning("migrated RTVOICE_API_KEY → legacy-default key; recommend rtvoice-admin create per-app key then revoke legacy")
    return store
```

竞态：3 服务并行启动同时检测，用 store 原子 op（Redis SETNX；YAML 启动锁文件）。

### 4.8 token-server 集成（slowapi 共存）

`/v1/tokens` 加 `Depends(require_key, scope="tokens")` 替代旧 hmac.compare_digest；slowapi `@limiter.limit("30/min")` 保留作 IP 防线。

---

## 5. 测试矩阵

| 类别 | 文件 | # |
|---|---|---|
| Pydantic Key model | `services/common/rtvoice_auth/tests/test_models.py` | 3 |
| YAML store CRUD + load + watcher | `tests/test_store_yaml.py` | 6 |
| Redis store（fakeredis mock） | `tests/test_store_redis.py` | 5 |
| verify (valid/invalid/revoked/scope) | `tests/test_verify.py` | 5 |
| quota (concurrent/per_hour/rollback) | `tests/test_quota.py` | 6 |
| admin CLI（6 commands） | `services/rtvoice-admin/tests/test_admin.py` | 8 |
| realtime-server endpoint require_key + quota | 扩 `test_endpoints.py` | +5 |
| token-server endpoint require_key + slowapi | 扩 / 新建 `test_app.py` | 3 |
| stt/tts CORS + auth（沙盒无 tests dir，prod E2E） | — | 0 |
| **新增小计** | | **41** |

总测试 SP5 后 119 → SP6 后 160+。

---

## 6. 验收

### 6.1 autonomous（沙盒 + prod）

- A1 `rtvoice-admin create --name x` 返 secret + key_id；keys.yaml 含新条目
- A2 `rtvoice-admin list` 不含 secret 列
- A3 revoke 后旧 secret → 401 `auth.token_revoked`
- A4 rotate 后旧 secret 立即失效；新 secret 工作
- A5 启动时 keys.yaml 空 + RTVOICE_API_KEY 设 → legacy-default 自动注册
- A6 sessions_concurrent_max=2 第 3 个 → 429 `auth.quota_concurrent`
- A7 sessions_per_hour_max=5 第 6 个 → 429 `auth.quota_per_hour`
- A8 scopes=[stt] key 调 /v1/sessions → 403 `auth.scope_denied`
- A9 YAML hot reload：admin CLI 改 keys.yaml → 服务无需重启 pickup
- A10 Redis backend：3 服务同 key 一致；Redis 重启不丢
- A11 slowapi IP 限 + per-key 限共存
- A12 cleanup 时 release_session 减 concurrent

### 6.2 user-participation

- B1 admin CLI 创"cozyvoice" key；CozyVoice 切到此 key
- B2 旧 RTVOICE_API_KEY 仍工作（legacy migration）
- B3 Grafana 看按 key metric 分布（如做 metric label）

---

## 7. 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| YAML file watcher 跨平台不一致 | M | `watchdog` 库 + 5s polling fallback |
| Redis 单点 | H | docker-compose healthcheck；in-memory cache fallback |
| 启动竞态（3 服务同时 auto-migrate） | M | Redis SETNX；YAML 启动 lock |
| Quota 漏 release（异常路径） | M | finally 必调 release；启动时审计清 stale concurrent |
| sha256 字典攻击 | L | secrets.token_urlsafe(32) 256 位熵 |
| 误删 legacy 致 prod 失联 | M | CLI 删除 legacy 强警告 + 二次确认 |
| metric 加 key_id 高 cardinality | M | 不加 label；用 logs 按 key 统计；如要 dashboard → SP7 |

---

## 8. 范围外（NOT in SP6）

- OAuth2 / 用户级 JWT（应用自管用户）
- 计费 / 账单
- per-second token bucket
- key 自动过期 / 续期
- 审计日志独立服务（仅 log + warn）
- prometheus metric 加 key_id label
- admin web UI
- 二级 scope（stt:asr-only 等）

---

## 9. 实施切片建议（供 writing-plans 参考）

| Task | 子项 | What | Tests |
|---|---|---|---|
| T1 | common | rtvoice_auth 包骨架 + Pydantic Key model | 3 |
| T2 | common | store.py YAML backend + watcher | 6 |
| T3 | common | store_redis.py | 5 |
| T4 | common | verify.py + require_key dep | 5 |
| T5 | common | quota.py | 6 |
| T6 | admin | rtvoice-admin 包骨架 + pyproject | 0 |
| T7 | admin | create / list / revoke / rotate / show 命令 | 5 |
| T8 | admin | import-legacy + 服务侧 lifespan auto-migrate | 3 |
| T9 | realtime | main.py / session_manager 集成 | +5 |
| T10 | stt | scope=stt | — |
| T11 | tts | 3 entry scope=tts（admin 端独立） | — |
| T12 | token | require_key 替换 hmac；slowapi 保留 | +3 |
| T13 | compose | redis 容器 + keys.yaml mount + env | — |
| T14 | docs | OPERATIONS §7 + CONVENTIONS §6 错误码 | — |
| T15 | release | CHANGELOG v0.13.0 + push | — |
| T16 | prod | A1-A12 + Grafana + user-participation | — |

**16 任务**；新增测试 41。

---

## 附录：相关文档

- 前置：[SP5 spec](./2026-05-09-sp5-adoption-bridge-design.md) / [SP5 plan](../plans/2026-05-09-sp5-adoption-bridge.md)
- API：[CONVENTIONS.md](../../api/CONVENTIONS.md)
- SDK：[clients/python/README.md](../../../clients/python/README.md)
