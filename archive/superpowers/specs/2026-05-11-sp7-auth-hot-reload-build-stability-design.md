# SP7 Auth Hot Reload + Build Stability Design

**日期**：2026-05-11
**前置**：SP6 (v0.13.0) prod；多租户 auth + admin CLI + Redis/YAML 双 backend；2 fix（fix-1 装 admin CLI + fix-2 dir mount）；prod 6 + monitoring 2 + redis 1 = 9 容器（如启用）。
**目标版本**：v0.14.0
**作用域**：SP6 反馈的 2 大痛点修补——admin CLI 写后服务侧 stale + build wget 0-byte 导致容器 unhealthy。

---

## 1. 目标

SP6 prod 验收暴露：
1. **YAML 无 hot reload** —— `revoke` 后服务侧 in-memory store 不更新；客户端旧 secret 仍能用直到 service restart。安全痛点（revoke 应实时）
2. **wget 0-byte 不检测** —— stt-server build 时 HF 抖动下载到空 onnx，容器启动 RuntimeError "ModelProto does not have a graph"。基础设施防御

SP7 解决：
- **R · Hot Reload**：YAML watchdog + Redis pubsub 自动检测变更；服务侧无需 restart 即刻 pickup
- **B · Build 稳定性**：`scripts/download_model.sh` helper 加 size check + retry × 3；各 Dockerfile 改用

预期 prod 体验：
- admin CLI `revoke` 后 < 500ms 全 4 服务失效（YAML）/ < 200ms（Redis）
- HF 抖动时 build 重试 3 次，失败 fast-fail 而非容器 unhealthy

---

## 2. 关键决策（D-2026-05-11-E.1~E.4）

| ID | 决策 | 理由 |
|---|---|---|
| **E.1** | YAML 用 `watchdog` file watcher；Redis 用 PUBSUB | 双 backend 各自最优；YAML 自动 inotify/FSEvents；Redis 单次 PUBLISH 多服务订阅 |
| **E.2** | wget 替换为 `scripts/download_model.sh`：`--tries=3 --timeout=60 -O` + `[ -s file ] && >= min_bytes` | 解抖动 + 验内容真实下载 |
| **E.3** | 服务侧整盘 reload + 100ms debounce | 整盘 cheap（YAML <10ms / Redis <50ms）；防 admin 短期连写多次触发 |
| **E.4** | `scripts/download_model.sh` 在 monorepo root，各 Dockerfile COPY 进容器调用 | SP6 build context 已为 monorepo root；helper 集中维护 |

---

## 3. 架构 & 文件布局

```
RTVoice/
├── services/common/rtvoice_auth/
│   ├── watcher.py                    ← 新：YamlFileWatcher + RedisPubSubListener + _Debouncer
│   ├── store.py                      ★ 加 reload() (cleanly re-load file)
│   ├── store_redis.py                ★ 加 publish_change()
│   └── tests/
│       ├── test_watcher_yaml.py      ← 新：3 tests（fire on write / debounce / stop）
│       └── test_watcher_redis.py     ← 新：3 tests（pubsub fire / debounce / reconnect）
│
├── services/rtvoice-admin/src/rtvoice_admin/
│   └── commands.py                   ★ create/revoke/rotate 末尾 _maybe_publish_change()
│
├── services/{realtime,stt,tts,token}-server/app/main.py  ★ lifespan 启 watcher；shutdown 停
│
├── services/stt-server/Dockerfile{,gpu}    ★ 改用 download_model.sh
├── services/tts-server/Dockerfile.cosyvoice{,3}  ★ 同（如 build 时下载 HF 模型）
│
└── scripts/
    ├── download_model.sh             ← 新：wget + size check + retry 3 次
    └── tests/test_download_helper.py ← 新：2 subprocess tests
```

**新文件**：~5（watcher.py / download_model.sh / 3 test files）
**修改**：~10（4 服务 main.py + 3-5 Dockerfile + store 双 backend + admin commands）
**新依赖**：0（`watchdog>=4.0` SP6 已 in deps；Redis client 已 in admin/auth）

**SP6 兼容**：纯增量；hot reload + size check 是"防御性增强"，不破坏现有 API/契约

---

## 4. 子项详细设计

### 4.1 R · Watcher 接口（`rtvoice_auth/watcher.py`）

```python
from typing import Callable, Awaitable
import asyncio

ReloadCallback = Callable[[], Awaitable[None]]


class _Debouncer:
    """短时间多次 fire 只触一次 callback（最后一次后 delay_ms 才执行）."""

    def __init__(self, callback: ReloadCallback, delay_ms: int = 100):
        self._cb = callback
        self._delay = delay_ms / 1000
        self._task: asyncio.Task | None = None

    def fire(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            await asyncio.sleep(self._delay)
            await self._cb()
        except asyncio.CancelledError:
            pass

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class YamlFileWatcher:
    """监 keys.yaml 路径变更，触发 callback。"""

    def __init__(self, path: str, on_change: ReloadCallback, debounce_ms: int = 100):
        self.path = path
        self._debouncer = _Debouncer(on_change, debounce_ms)
        self._observer = None  # watchdog.Observer
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        """非 async：watchdog Observer 是 background thread。"""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        self._loop = asyncio.get_event_loop()
        debouncer = self._debouncer
        loop = self._loop
        watched_path = self.path

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.src_path == watched_path or event.src_path.endswith(watched_path.split("/")[-1]):
                    loop.call_soon_threadsafe(debouncer.fire)
            def on_created(self, event):
                self.on_modified(event)

        import os
        parent = os.path.dirname(self.path)
        self._observer = Observer()
        self._observer.schedule(_Handler(), parent, recursive=False)
        self._observer.start()

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        await self._debouncer.stop()


class RedisPubSubListener:
    """订阅 'rtvoice:keys:changed'，触发 callback；自带 reconnect."""

    def __init__(self, redis_client, on_change: ReloadCallback,
                 channel: str = "rtvoice:keys:changed", debounce_ms: int = 100):
        self._r = redis_client
        self._channel = channel
        self._debouncer = _Debouncer(on_change, debounce_ms)
        self._task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self._closed:
            try:
                pubsub = self._r.pubsub()
                await pubsub.subscribe(self._channel)
                async for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        self._debouncer.fire()
            except asyncio.CancelledError:
                raise
            except Exception:
                # reconnect
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._debouncer.stop()
```

### 4.2 R · 服务侧 lifespan 集成

每 service main.py 在 SP6 init key_store 之后追加：

```python
    async def _on_keys_changed():
        await app.state.key_store.load()
        log.info("key store hot-reloaded")

    from rtvoice_auth.watcher import YamlFileWatcher, RedisPubSubListener
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.store_redis import RedisKeyStore
    if isinstance(app.state.key_store, YamlKeyStore):
        app.state.key_watcher = YamlFileWatcher(
            path=str(app.state.key_store.path),
            on_change=_on_keys_changed,
        )
        app.state.key_watcher.start()
    elif isinstance(app.state.key_store, RedisKeyStore):
        app.state.key_watcher = RedisPubSubListener(
            redis_client=app.state.key_store._r,
            on_change=_on_keys_changed,
        )
        await app.state.key_watcher.start()
```

`finally`（yield 之后）：
```python
    if hasattr(app.state, "key_watcher"):
        await app.state.key_watcher.stop()
```

### 4.3 R · admin CLI 通知 Redis

`services/rtvoice-admin/src/rtvoice_admin/commands.py`：

```python
async def _maybe_publish_change(store, key_id: str) -> None:
    """如 Redis backend，PUBLISH 通知；YAML 不需要（watcher 自动监 file write）."""
    try:
        from rtvoice_auth.store_redis import RedisKeyStore
        if isinstance(store, RedisKeyStore):
            await store._r.publish("rtvoice:keys:changed", key_id)
    except Exception:
        pass  # 写已成功；publish 失败不影响 admin 结果（next reload 会自愈）


# 每个写操作末尾追加：
async def cmd_create(store, ..., notes=""):
    # ... existing put logic ...
    await _maybe_publish_change(store, k.id)
    return {...}


async def cmd_revoke(store, *, key_id: str) -> bool:
    ok = await store.revoke(key_id)
    if ok:
        await _maybe_publish_change(store, key_id)
    return ok


async def cmd_rotate(store, *, key_id: str) -> dict:
    # ... existing rotate logic ...
    await _maybe_publish_change(store, key_id)
    return {"id": key_id, "secret": new_secret}
```

YAML backend `cmd_create` 内 `store.put` 已通过 `_flush()` atomic rename 写文件 → watchdog 自动捕获，无需 publish。

### 4.4 B · `scripts/download_model.sh`

```bash
#!/usr/bin/env sh
# wget + size check + retry 3 次
set -e
URL="$1"
DEST="$2"
MIN_BYTES="${3:-1024}"

if [ -z "$URL" ] || [ -z "$DEST" ]; then
    echo "Usage: download_model.sh <url> <dest_path> [min_bytes]" >&2
    exit 2
fi

mkdir -p "$(dirname "$DEST")"

for attempt in 1 2 3; do
    echo "[download_model] attempt $attempt/3: $URL → $DEST"
    if wget --tries=1 --timeout=60 --quiet -O "$DEST" "$URL"; then
        actual=$(wc -c < "$DEST" 2>/dev/null || echo 0)
        if [ "$actual" -ge "$MIN_BYTES" ]; then
            echo "[download_model] OK: $DEST ($actual bytes)"
            exit 0
        fi
        echo "[download_model] WARN attempt $attempt: $DEST is $actual bytes, need >= $MIN_BYTES" >&2
        rm -f "$DEST"
    else
        echo "[download_model] WARN attempt $attempt: wget failed for $URL" >&2
    fi
    sleep 3
done

echo "[download_model] FAIL: 3 attempts exhausted for $URL" >&2
exit 1
```

### 4.5 B · Dockerfile 改造（stt-server 为例）

`services/stt-server/Dockerfile.gpu`：

把现有 `wget -q -O "$f" "$base/$f" || (echo "FAILED: $f"; exit 1)` 多行 for 循环改为：

```dockerfile
COPY scripts/download_model.sh /usr/local/bin/download_model.sh
RUN chmod +x /usr/local/bin/download_model.sh

RUN mkdir -p /app/models/${MODEL_NAME} && cd /app/models/${MODEL_NAME} && \
    base="https://huggingface.co/${HF_REPO}/resolve/main" && \
    /usr/local/bin/download_model.sh "${base}/encoder-epoch-99-avg-1.int8.onnx" "encoder-epoch-99-avg-1.int8.onnx" 1048576 && \
    /usr/local/bin/download_model.sh "${base}/decoder-epoch-99-avg-1.int8.onnx" "decoder-epoch-99-avg-1.int8.onnx" 102400 && \
    /usr/local/bin/download_model.sh "${base}/joiner-epoch-99-avg-1.int8.onnx" "joiner-epoch-99-avg-1.int8.onnx" 1048576 && \
    /usr/local/bin/download_model.sh "${base}/tokens.txt" "tokens.txt" 1024
```

`services/stt-server/Dockerfile`（CPU）同款。

tts-server 3 Dockerfile 多半在 entrypoint.sh 内运行时 download CosyVoice 模型，不在 build；如发现 build 时也有 wget 一并改。

### 4.6 验收 SLA

- YAML write → service reload 完成：< 500ms（watchdog ≤100ms + debounce 100ms + load ~10ms + buffer 200ms）
- Redis PUBLISH → service reload 完成：< 200ms（PUBSUB ≤10ms + debounce 100ms + load ~50ms）
- Debounce 验：100ms 内 3 次 admin CLI 写 → 实际 log 看 reload 触发 1 次（不是 3 次）
- build 防御：人为篡改 HF 返 0-byte → build attempt × 3 都失败 → exit non-0 → docker build 早期失败而非容器 unhealthy

---

## 5. 测试矩阵

| 类别 | 文件 | # |
|---|---|---|
| YamlFileWatcher（写 file → callback 被调） | `services/common/rtvoice_auth/tests/test_watcher_yaml.py` | 3 |
| RedisPubSubListener（PUBLISH → callback） | `tests/test_watcher_redis.py` | 3 |
| Store integration: YAML 写 + watcher → reload | 扩 `test_store_yaml.py` | +2 |
| Store integration: Redis PUBLISH → reload | 扩 `test_store_redis.py` | +2 |
| Admin commands Redis backend 自动 publish | 扩 `test_commands.py` | +1 |
| download_model.sh helper（succeed + fail-too-small） | `tests/test_download_helper.py` | 2 |
| **新增小计** | | **13** |

总测试 SP6 后 165+ → SP7 后 178+。

---

## 6. 验收

### 6.1 autonomous

- A1 YAML：admin CLI create → < 500ms service in-memory store 含新 key
- A2 YAML：admin CLI revoke → < 500ms service 用旧 secret → 401 `auth.token_revoked`
- A3 Redis：admin CLI create → < 200ms service reload
- A4 Redis：admin CLI revoke → < 200ms 失效
- A5 Debounce：100ms 内 3 次 write → reload 触发 1 次（log 计数）
- A6 download_model.sh：URL 返 size < min → 3 次重试 + 最终 exit non-0
- A7 download_model.sh：URL 返正常 → 1 次成功

### 6.2 user-participation（prod）

- B1 admin CLI create cozyvoice2 → 1 秒内 SDK 用新 secret 工作（无 restart）
- B2 admin CLI revoke key → 1 秒内 4 服务全失效
- B3 stt-server rebuild（不切镜像源时）→ 偶发 HF 抖动 build 失败 fast，error log 清晰指出哪个文件

---

## 7. 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| watchdog inotify limit | L | 单文件监听不占 watches |
| Redis PUBSUB 断线丢消息 | M | listener auto-reconnect；reload 整盘 self-heal |
| 跨平台 file watcher 差异 | M | watchdog PollingObserver fallback |
| Debounce 配置不灵活 | L | env `RTVOICE_KEYS_RELOAD_DEBOUNCE_MS=100` |
| Service shutdown watcher 泄漏 | M | lifespan finally `await watcher.stop()` |
| HF 长期 down | M | 文档化 HF mirror（hf-mirror.com）+ env `HF_ENDPOINT` |
| Redis backend admin CLI 跟服务侧 channel 名不一致 | L | 硬编码 `rtvoice:keys:changed` + 单测验 |
| YAML watcher 监 mount 父目录（per SP6-fix-2）触发其它无关文件变更 | M | 在 handler 内只响应目标 path 名（`endswith("keys.yaml")`）|

---

## 8. 范围外（NOT in SP7）

- per-second token bucket
- key 自动过期 / 续期
- admin web UI
- prometheus metric key_id label
- HF mirror **自动** fallback（仅文档化）
- BuildKit cache invalidation
- tts-server 运行时 CosyVoice 模型 download size check
- RAG / Tool calls / GPU 调度 / WebRTC

---

## 9. 实施切片建议（供 writing-plans 参考）

| Task | 子项 | What | Tests |
|---|---|---|---|
| T1 | R | watcher.py: _Debouncer + YamlFileWatcher | 3 |
| T2 | R | watcher.py: RedisPubSubListener | 3 |
| T3 | R | YamlKeyStore + RedisKeyStore reload integration | +4 |
| T4 | R | admin commands.py: _maybe_publish_change（create/revoke/rotate 末尾） | +1 |
| T5 | R | realtime-server lifespan 集成 watcher start/stop | 0 |
| T6 | R | stt + tts + token 3 服务 lifespan 同款 | 0 |
| T7 | B | scripts/download_model.sh + test_download_helper.py | 2 |
| T8 | B | stt-server Dockerfile + Dockerfile.gpu 改用 helper | 0 |
| T9 | docs | OPERATIONS.md §8 hot reload + §6 HF mirror 备份 | 0 |
| T10 | release | CHANGELOG v0.14.0 + push | 0 |
| T11 | prod | autonomous A1-A7 + user-participation B1-B3 | 0 |

**11 任务**；新增测试 13。

---

## 附录

- 前置：[SP6 spec](./2026-05-10-sp6-multi-tenant-auth-design.md) / [SP6 plan](../plans/2026-05-10-sp6-multi-tenant-auth.md)
- 风险表中提及的 watchdog dep 已在 SP6 加（`pyproject.toml` `watchdog>=4.0`）
