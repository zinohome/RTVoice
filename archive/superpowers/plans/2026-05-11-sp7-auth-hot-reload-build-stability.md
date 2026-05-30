# SP7 Auth Hot Reload + Build Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修补 SP6 反馈两大痛点——v0.14.0 加 (1) watchdog file watcher + Redis PUBSUB 让 admin CLI 写后服务侧 <500ms 自动 pickup，(2) `scripts/download_model.sh` helper 加 wget size check + retry × 3 防 HF 抖动。

**Architecture:** `rtvoice_auth.watcher` 模块加 `_Debouncer` + `YamlFileWatcher`（watchdog Observer 后台线程通过 `loop.call_soon_threadsafe` 唤起 asyncio debouncer）+ `RedisPubSubListener`（自带 reconnect）。admin CLI 写操作末尾 PUBLISH（Redis backend only；YAML 走 file watcher）。4 服务 lifespan 启 watcher start/stop。`scripts/download_model.sh` 取代 Dockerfile 内 `wget -q -O`。

**Tech Stack:** watchdog>=4.0（SP6 已加）/ redis>=5 asyncio pubsub / wget shell + sh-portable size check

**Spec:** [docs/superpowers/specs/2026-05-11-sp7-auth-hot-reload-build-stability-design.md](../specs/2026-05-11-sp7-auth-hot-reload-build-stability-design.md)

---

## Task 1: watcher.py — _Debouncer + YamlFileWatcher

**Files:**
- Create: `services/common/rtvoice_auth/watcher.py`
- Create: `services/common/rtvoice_auth/tests/test_watcher_yaml.py`

- [ ] **Step 1: 写测试**

`services/common/rtvoice_auth/tests/test_watcher_yaml.py`:

```python
"""Test YamlFileWatcher + _Debouncer."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_debouncer_fires_once_after_delay():
    """fire() 后 delay_ms ms 内不 fire 第二次 → callback 跑一次."""
    from rtvoice_auth.watcher import _Debouncer
    calls = []

    async def cb():
        calls.append(1)

    d = _Debouncer(cb, delay_ms=50)
    d.fire()
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    await d.stop()


@pytest.mark.asyncio
async def test_debouncer_coalesces_rapid_fires():
    """100ms 内 3 次 fire 只触一次 callback."""
    from rtvoice_auth.watcher import _Debouncer
    calls = []

    async def cb():
        calls.append(1)

    d = _Debouncer(cb, delay_ms=50)
    d.fire()
    await asyncio.sleep(0.01)
    d.fire()
    await asyncio.sleep(0.01)
    d.fire()
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    await d.stop()


@pytest.mark.asyncio
async def test_yaml_file_watcher_fires_on_modify(tmp_path):
    """File 被改写 → callback 被调."""
    from rtvoice_auth.watcher import YamlFileWatcher

    p = tmp_path / "keys.yaml"
    p.write_text("version: 1\nkeys: []\n")

    fired = asyncio.Event()

    async def cb():
        fired.set()

    w = YamlFileWatcher(str(p), on_change=cb, debounce_ms=50)
    w.start()
    await asyncio.sleep(0.05)  # 让 observer 注册

    # 写新内容
    p.write_text("version: 1\nkeys:\n  - id: k1\n    secret_hash: h\n    name: n\n    created_at: 2026-01-01T00:00:00+00:00\n")

    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await w.stop()
    assert fired.is_set()
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_watcher_yaml.py -v
```

- [ ] **Step 3: 写 `services/common/rtvoice_auth/watcher.py`**

```python
"""Hot reload watchers: file watcher (YAML) + Redis pubsub listener."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Callable, Awaitable

log = logging.getLogger("rtvoice.auth.watcher")

ReloadCallback = Callable[[], Awaitable[None]]


class _Debouncer:
    """短时间多次 fire 只触一次 callback（最后一次后 delay_ms 才执行）."""

    def __init__(self, callback: ReloadCallback, delay_ms: int = 100):
        self._cb = callback
        self._delay = delay_ms / 1000
        self._task: asyncio.Task | None = None

    def fire(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay)
            await self._cb()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("debounce callback failed")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class YamlFileWatcher:
    """监 keys.yaml 路径变更，触发 callback（debounced）。"""

    def __init__(self, path: str, on_change: ReloadCallback, debounce_ms: int = 100):
        self.path = path
        self._debouncer = _Debouncer(on_change, debounce_ms)
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        self._loop = asyncio.get_event_loop()
        debouncer = self._debouncer
        loop = self._loop
        target_basename = os.path.basename(self.path)

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if os.path.basename(event.src_path) == target_basename:
                    loop.call_soon_threadsafe(debouncer.fire)

            def on_created(self, event):
                self.on_modified(event)

            def on_moved(self, event):
                # atomic rename (write-then-rename) 也走这里
                dest = getattr(event, "dest_path", "")
                if os.path.basename(dest) == target_basename:
                    loop.call_soon_threadsafe(debouncer.fire)

        parent = os.path.dirname(os.path.abspath(self.path)) or "."
        self._observer = Observer()
        self._observer.schedule(_Handler(), parent, recursive=False)
        self._observer.start()
        log.info("yaml file watcher started: %s", self.path)

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        await self._debouncer.stop()
        log.info("yaml file watcher stopped")
```

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_watcher_yaml.py -v
```

Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/watcher.py services/common/rtvoice_auth/tests/test_watcher_yaml.py
git commit -m "feat(auth): watcher.py _Debouncer + YamlFileWatcher (T1)

- _Debouncer: 短时间多次 fire 只触一次 callback；asyncio.create_task + cancel-and-reset 模式
- YamlFileWatcher: watchdog Observer 监父目录；handler 内 basename 过滤；
  跨线程通过 loop.call_soon_threadsafe 唤起 debouncer
- 3 单元测试

per spec §4.1"
```

---

## Task 2: watcher.py — RedisPubSubListener

**Files:**
- Modify: `services/common/rtvoice_auth/watcher.py`
- Create: `services/common/rtvoice_auth/tests/test_watcher_redis.py`

- [ ] **Step 1: 写测试**

`services/common/rtvoice_auth/tests/test_watcher_redis.py`:

```python
"""Test RedisPubSubListener via fakeredis."""
import asyncio
import pytest


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_pubsub_listener_fires_on_publish(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener

    fired = asyncio.Event()

    async def cb():
        fired.set()

    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=50)
    await listener.start()
    await asyncio.sleep(0.1)  # subscribe 完成

    await fake_redis.publish("rtvoice:keys:changed", "key_x")

    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await listener.stop()
    assert fired.is_set()


@pytest.mark.asyncio
async def test_pubsub_listener_debounces_rapid_publishes(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener

    calls = []

    async def cb():
        calls.append(1)

    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=100)
    await listener.start()
    await asyncio.sleep(0.1)

    for i in range(3):
        await fake_redis.publish("rtvoice:keys:changed", f"k{i}")
    await asyncio.sleep(0.3)
    await listener.stop()
    assert len(calls) == 1, f"expected 1 reload after debounce, got {len(calls)}"


@pytest.mark.asyncio
async def test_pubsub_listener_stop_cleanly(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener

    async def cb():
        pass

    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=50)
    await listener.start()
    await asyncio.sleep(0.05)
    await listener.stop()
    # 不抛异常即通过
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_watcher_redis.py -v
```

- [ ] **Step 3: 在 `watcher.py` 末尾追加 `RedisPubSubListener`**

```python
class RedisPubSubListener:
    """订阅 'rtvoice:keys:changed' channel；自带 reconnect on disconnect."""

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
                log.info("redis pubsub subscribed: %s", self._channel)
                try:
                    async for msg in pubsub.listen():
                        if self._closed:
                            break
                        if msg.get("type") == "message":
                            self._debouncer.fire()
                finally:
                    try:
                        await pubsub.unsubscribe(self._channel)
                        await pubsub.aclose()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("redis pubsub loop error; reconnect in 1s")
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
        log.info("redis pubsub listener stopped")
```

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_watcher_redis.py -v
```

Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add services/common/rtvoice_auth/watcher.py services/common/rtvoice_auth/tests/test_watcher_redis.py
git commit -m "feat(auth): RedisPubSubListener (T2)

- 订阅 rtvoice:keys:changed channel；message → _Debouncer.fire()
- 内部 reconnect on disconnect（1s sleep retry）
- stop() 优雅取消 task + unsubscribe
- 3 单元测试（fakeredis）

per spec §4.1"
```

---

## Task 3: Store integration — YAML + Redis 都加 reload + publish

**Files:**
- Modify: `services/common/rtvoice_auth/store_redis.py`（加 `publish_change()` 可选）
- Modify: `services/common/rtvoice_auth/tests/test_store_yaml.py`（+2 watcher integration tests）
- Modify: `services/common/rtvoice_auth/tests/test_store_redis.py`（+2 同款）

注：YAML store 已经有 `load()` 方法可重 load；RedisKeyStore 同。本任务加 integration 测试 + Redis store 加 publish helper（供 admin CLI 调）。

- [ ] **Step 1: 在 `test_store_yaml.py` 末尾追加 2 测试**

```python
@pytest.mark.asyncio
async def test_yaml_store_reload_picks_up_new_key(tmp_path):
    """外部进程改 yaml 文件后 store.load() 能读到新 key（无需进程重启）."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    from datetime import datetime, timezone

    p = tmp_path / "keys.yaml"
    s1 = YamlKeyStore(str(p))
    await s1.load()
    # 模拟外部 admin CLI 写 key（s2 是独立 store 实例）
    s2 = YamlKeyStore(str(p))
    await s2.load()
    await s2.put(Key(id="kx", secret_hash="hx", name="x",
                     created_at=datetime.now(timezone.utc)))
    # s1 重 load 应该看到
    assert s1.find_by_hash("hx") is None  # before reload
    await s1.load()
    assert s1.find_by_hash("hx") is not None  # after reload


@pytest.mark.asyncio
async def test_yaml_store_reload_picks_up_revoke(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    from datetime import datetime, timezone

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="kr", secret_hash="hr", name="r",
                    created_at=datetime.now(timezone.utc)))
    # 外部 revoke
    s2 = YamlKeyStore(str(p))
    await s2.load()
    await s2.revoke("kr")
    # s 重 load
    await s.load()
    assert s.find_by_id("kr").revoked_at is not None
```

- [ ] **Step 2: 在 `test_store_redis.py` 末尾追加 2 测试**

```python
@pytest.mark.asyncio
async def test_redis_store_reload_picks_up_new_key(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    from datetime import datetime, timezone

    s1 = RedisKeyStore(fake_redis)
    await s1.load()
    s2 = RedisKeyStore(fake_redis)
    await s2.load()
    await s2.put(Key(id="rx", secret_hash="rh", name="r",
                     created_at=datetime.now(timezone.utc)))
    assert s1.find_by_hash("rh") is None  # before reload
    await s1.load()
    assert s1.find_by_hash("rh") is not None


@pytest.mark.asyncio
async def test_redis_store_publish_change(fake_redis):
    """publish_change(key_id) 发布到 channel."""
    from rtvoice_auth.store_redis import RedisKeyStore

    s = RedisKeyStore(fake_redis)
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("rtvoice:keys:changed")
    # discard subscribe ack
    msg = await asyncio.wait_for(pubsub.get_message(timeout=1), timeout=1.5)

    await s.publish_change("test_key")

    msg = await asyncio.wait_for(pubsub.get_message(timeout=1), timeout=1.5)
    assert msg["type"] == "message"
    data = msg["data"]
    if isinstance(data, bytes):
        data = data.decode()
    assert data == "test_key"
    await pubsub.aclose()
```

- [ ] **Step 3: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_yaml.py rtvoice_auth/tests/test_store_redis.py -v 2>&1 | tail -15
```

Expected: YAML 2 新可能 pass（已有 load 方法）；Redis publish_change 测 fail（方法不存在）。

- [ ] **Step 4: 在 `store_redis.py` `RedisKeyStore` 类内追加方法**

```python
    async def publish_change(self, key_id: str) -> None:
        """通知所有订阅者：某 key 变更（key_id 仅日志用，订阅者整盘 reload）."""
        await self._r.publish("rtvoice:keys:changed", key_id)
```

- [ ] **Step 5: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_yaml.py rtvoice_auth/tests/test_store_redis.py -v 2>&1 | tail -15
```

Expected: 全过（YAML 8 + Redis 7+ = 15+）。

- [ ] **Step 6: Commit**

```bash
git add services/common/rtvoice_auth/store_redis.py services/common/rtvoice_auth/tests/test_store_yaml.py services/common/rtvoice_auth/tests/test_store_redis.py
git commit -m "feat(auth): store reload integration + RedisKeyStore.publish_change (T3)

- YamlKeyStore: 已有 load()；+2 测试验外部写后 reload 能读到
- RedisKeyStore: 加 publish_change(key_id) → PUBLISH rtvoice:keys:changed
- +2 redis 测试（reload + publish）

per spec §4.3"
```

---

## Task 4: admin CLI 写操作末尾自动 publish（Redis backend）

**Files:**
- Modify: `services/rtvoice-admin/src/rtvoice_admin/commands.py`
- Modify: `services/rtvoice-admin/tests/test_commands.py`

- [ ] **Step 1: 在 `test_commands.py` 末尾追加 1 测试**

```python
@pytest.mark.asyncio
async def test_cmd_create_publishes_change_on_redis(monkeypatch):
    """Redis backend：cmd_create 末尾 PUBLISH rtvoice:keys:changed."""
    import fakeredis.aioredis
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_admin.commands import cmd_create

    r = fakeredis.aioredis.FakeRedis()
    s = RedisKeyStore(r)
    await s.load()

    pubsub = r.pubsub()
    await pubsub.subscribe("rtvoice:keys:changed")
    # consume subscribe ack
    await pubsub.get_message(timeout=1)

    await cmd_create(s, name="t", sessions_concurrent=1, sessions_per_hour=10,
                     scopes=["stt"])

    msg = await pubsub.get_message(timeout=2)
    assert msg is not None
    assert msg["type"] == "message"

    await pubsub.aclose()
    await r.aclose()
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/rtvoice-admin
python3 -m pytest tests/test_commands.py -k "publishes_change" -v
```

- [ ] **Step 3: 改 `services/rtvoice-admin/src/rtvoice_admin/commands.py`**

3a. 在文件末尾追加 helper：

```python
async def _maybe_publish_change(store, key_id: str) -> None:
    """Redis backend → PUBLISH；YAML 不需要（watchdog 自动监 file write）."""
    try:
        from rtvoice_auth.store_redis import RedisKeyStore
        if isinstance(store, RedisKeyStore):
            await store.publish_change(key_id)
    except Exception:
        pass  # 写已成功；publish 失败下次 reload 自愈
```

3b. 修改 `cmd_create` —— 在 `return {...}` 之前加一行：

```python
    await _maybe_publish_change(store, key_id)
```

3c. 修改 `cmd_revoke`：

```python
async def cmd_revoke(store: Any, *, key_id: str) -> bool:
    ok = await store.revoke(key_id)
    if ok:
        await _maybe_publish_change(store, key_id)
    return ok
```

3d. 修改 `cmd_rotate` —— 在 `return {...}` 之前加：

```python
    await _maybe_publish_change(store, key_id)
```

- [ ] **Step 4: 跑测试**

```bash
cd services/rtvoice-admin
python3 -m pytest tests/ -v 2>&1 | tail -15
```

Expected: 11 passed（10 旧 + 1 新）。

- [ ] **Step 5: Commit**

```bash
git add services/rtvoice-admin/src/rtvoice_admin/commands.py services/rtvoice-admin/tests/test_commands.py
git commit -m "feat(admin): _maybe_publish_change on create/revoke/rotate (T4)

- helper: Redis backend → PUBLISH rtvoice:keys:changed
- create / revoke / rotate 三命令末尾调
- YAML 不需（watchdog file watcher 自动监）
- +1 单元测试

per spec §4.3"
```

---

## Task 5: realtime-server lifespan 集成 watcher

**Files:**
- Modify: `services/realtime-server/app/main.py`
- Modify: `services/realtime-server/tests/test_endpoints.py`（追加 1 hot reload e2e）

- [ ] **Step 1: 写 hot reload e2e 测试**

在 `tests/test_endpoints.py` 末尾追加：

```python
def test_hot_reload_yaml_picks_up_new_key(monkeypatch, tmp_path):
    """admin CLI 改 keys.yaml 后服务侧 < 1s 自动 pickup."""
    import asyncio
    import hashlib
    from datetime import datetime, timezone
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    from fastapi.testclient import TestClient

    yaml_path = tmp_path / "keys.yaml"
    secret = "hot-reload-secret-32-chars-aaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()

    # 启动时 store 为空
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        # 第一次：secret 不存在 → 401
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401

        # 外部"admin CLI"写 key
        s = YamlKeyStore(str(yaml_path))
        asyncio.run(s.load())
        asyncio.run(s.put(Key(id="kh", secret_hash=h, name="h",
                              scopes=["realtime"],
                              created_at=datetime.now(timezone.utc))))

        # 等 watcher reload (debounce 100ms + IO buffer)
        import time
        time.sleep(0.6)

        # 第二次：应该 201
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 201, f"expected 201, got {r.status_code}: {r.text}"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "hot_reload" -v
```

- [ ] **Step 3: 改 `main.py` lifespan 内集成 watcher**

在 `services/realtime-server/app/main.py` 的 `lifespan()` 函数内（SP6 init key_store 之后、`yield` 之前）追加：

```python
    # SP7: hot reload watcher
    async def _on_keys_changed():
        await app.state.key_store.load()
        log.info("key store hot-reloaded")

    from rtvoice_auth.watcher import YamlFileWatcher, RedisPubSubListener
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.store_redis import RedisKeyStore
    debounce_ms = int(os.environ.get("RTVOICE_KEYS_RELOAD_DEBOUNCE_MS", "100"))
    if isinstance(app.state.key_store, YamlKeyStore):
        app.state.key_watcher = YamlFileWatcher(
            path=str(app.state.key_store.path),
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        app.state.key_watcher.start()
    elif isinstance(app.state.key_store, RedisKeyStore):
        app.state.key_watcher = RedisPubSubListener(
            redis_client=app.state.key_store._r,
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        await app.state.key_watcher.start()
```

在 `yield` 之后（finally / shutdown 段）追加：

```python
    if hasattr(app.state, "key_watcher") and app.state.key_watcher is not None:
        try:
            await app.state.key_watcher.stop()
        except Exception:
            log.exception("key_watcher stop failed")
```

- [ ] **Step 4: 跑测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v 2>&1 | tail -15
```

Expected: 全过（28+ tests）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/main.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): hot reload watcher 集成 (T5)

- lifespan 启 YamlFileWatcher (YAML) 或 RedisPubSubListener (Redis)
- debounce_ms 由 env RTVOICE_KEYS_RELOAD_DEBOUNCE_MS 控（默认 100ms）
- shutdown 优雅 stop watcher
- +1 e2e 测试（YAML 写 key → 服务侧 0.6s 内 pickup）

per spec §4.2"
```

---

## Task 6: stt + tts + token 3 服务 lifespan 同款集成

**Files:**
- Modify: `services/stt-server/app/main.py`
- Modify: `services/tts-server/app/main.py`
- Modify: `services/tts-server/app/main_cosyvoice.py`
- Modify: `services/tts-server/app/main_cosyvoice3.py`
- Modify: `services/token-server/app/main.py`

注：5 个 main 文件同款 patch。

- [ ] **Step 1: 5 个 main 文件各加 watcher 集成段（同 T5 Step 3 内容）**

每个 main.py 的 lifespan 内 SP6 init key_store 之后插入：

```python
    # SP7: hot reload watcher
    async def _on_keys_changed():
        await app.state.key_store.load()
        log.info("key store hot-reloaded")

    from rtvoice_auth.watcher import YamlFileWatcher, RedisPubSubListener
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.store_redis import RedisKeyStore
    debounce_ms = int(os.environ.get("RTVOICE_KEYS_RELOAD_DEBOUNCE_MS", "100"))
    if isinstance(app.state.key_store, YamlKeyStore):
        app.state.key_watcher = YamlFileWatcher(
            path=str(app.state.key_store.path),
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        app.state.key_watcher.start()
    elif isinstance(app.state.key_store, RedisKeyStore):
        app.state.key_watcher = RedisPubSubListener(
            redis_client=app.state.key_store._r,
            on_change=_on_keys_changed,
            debounce_ms=debounce_ms,
        )
        await app.state.key_watcher.start()
```

shutdown 段加：

```python
    if hasattr(app.state, "key_watcher") and app.state.key_watcher is not None:
        try:
            await app.state.key_watcher.stop()
        except Exception:
            log.exception("key_watcher stop failed")
```

- [ ] **Step 2: syntax check 5 文件**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in services/stt-server/app/main.py services/tts-server/app/main.py services/tts-server/app/main_cosyvoice.py services/tts-server/app/main_cosyvoice3.py services/token-server/app/main.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK $f" || echo "FAIL $f"
done
```

Expected: 5 OK。

- [ ] **Step 3: 跑 token-server 现有测试（其它沙盒无 tests dir）**

```bash
cd services/token-server
python3 -m pytest tests/ -v
```

Expected: 3 passed（SP6 加的；watcher 集成不破）。

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/stt-server/app/main.py services/tts-server/app/main.py services/tts-server/app/main_cosyvoice.py services/tts-server/app/main_cosyvoice3.py services/token-server/app/main.py
git commit -m "feat(services): hot reload watcher 集成 (T6 stt+tts×3+token)

- 5 个 main.py 同款集成 YamlFileWatcher / RedisPubSubListener
- shutdown 优雅 stop
- stt/tts 沙盒无 tests dir；token-server 3 测试通过
- 完整 4 服务 hot reload 链路

per spec §4.2"
```

---

## Task 7: scripts/download_model.sh + test_download_helper

**Files:**
- Create: `scripts/download_model.sh`
- Create: `scripts/tests/test_download_helper.py`

- [ ] **Step 1: 写 helper 脚本**

`scripts/download_model.sh`:

```bash
#!/usr/bin/env sh
# scripts/download_model.sh — wget + size check + retry × 3 for HF/CDN model files
# Usage:
#   download_model.sh <url> <dest_path> [min_bytes]
# Exit 0 on success（文件存在 + size >= min_bytes 默认 1024）；非 0 失败。

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
    echo "[download_model] attempt $attempt/3: $URL -> $DEST"
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

```bash
chmod +x scripts/download_model.sh
```

- [ ] **Step 2: 写 `scripts/tests/test_download_helper.py`**

```bash
mkdir -p scripts/tests
touch scripts/tests/__init__.py
```

`scripts/tests/test_download_helper.py`:

```python
"""Test scripts/download_model.sh via subprocess."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_model.sh"


def test_download_succeeds_for_large_file(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"\x00" * 10240)
    dest = tmp_path / "dest.bin"
    r = subprocess.run(["sh", str(SCRIPT), f"file://{src}", str(dest), "1024"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert dest.stat().st_size == 10240


def test_download_fails_when_too_small(tmp_path):
    src = tmp_path / "small.txt"
    src.write_text("x")
    dest = tmp_path / "dest.txt"
    r = subprocess.run(["sh", str(SCRIPT), f"file://{src}", str(dest), "100"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    # dest 应该被 rm（最后一次 retry 后）
    assert not dest.exists() or dest.stat().st_size < 100
```

- [ ] **Step 3: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -m pytest scripts/tests/test_download_helper.py -v
```

注：依赖 `wget` 可用 + 支持 `file://` URL。沙盒一般都有 wget。如沙盒 wget 不支持 `file://` 可用 `python3 -m http.server` 临时起服务测试，但 file:// 更简单优先。如失败，加 `pytest.mark.skipif` 跳过。

Expected: 2 passed。

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add scripts/download_model.sh scripts/tests/
git commit -m "feat(build): scripts/download_model.sh wget + size check + retry × 3 (T7)

- wget --tries=1 --timeout=60 -O；3 次外层重试 + sleep 3s
- size check：实际下载字节 < min_bytes → 视为失败 + rm + retry
- 2 subprocess 测试（succeed for large file + fail for small）

per spec §4.4"
```

---

## Task 8: stt-server Dockerfile + Dockerfile.gpu 改用 helper

**Files:**
- Modify: `services/stt-server/Dockerfile`
- Modify: `services/stt-server/Dockerfile.gpu`

- [ ] **Step 1: 改 `services/stt-server/Dockerfile.gpu`**

定位现有 wget 段（约 lines 43-52）：

```dockerfile
RUN mkdir -p /app/models/${MODEL_NAME} && cd /app/models/${MODEL_NAME} && \
    base="https://huggingface.co/${HF_REPO}/resolve/main" && \
    for f in \
        "encoder-epoch-99-avg-1.int8.onnx" \
        "decoder-epoch-99-avg-1.int8.onnx" \
        "joiner-epoch-99-avg-1.int8.onnx" \
        "tokens.txt" \
    ; do \
        wget -q -O "$f" "${base}/${f}" || (echo "FAILED: $f"; exit 1); \
    done
```

替换为：

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

注：`COPY scripts/download_model.sh` 走 monorepo root build context（SP6 已改）。

- [ ] **Step 2: 同款改 `services/stt-server/Dockerfile`（CPU 版本）**

模式相同，COPY + 4 行 download_model.sh 调用。

- [ ] **Step 3: tts-server Dockerfile 检查（如有 build 时 wget download）**

```bash
grep -n "wget" services/tts-server/Dockerfile services/tts-server/Dockerfile.cosyvoice services/tts-server/Dockerfile.cosyvoice3 2>&1 | head
```

如发现 build 时 wget download HF 模型（非运行时 entrypoint）则改之；如仅运行时 entrypoint download（运行时 CosyVoice 模型下载属常态），保留不动 + 在 OPERATIONS 文档化说明。

- [ ] **Step 4: 验证 Dockerfile 语法**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in services/stt-server/Dockerfile services/stt-server/Dockerfile.gpu; do
    # 不能完整 build；仅 lint 简化
    grep -c "download_model.sh" "$f" && echo "  helper present: $f"
done
```

Expected: 各文件 ≥ 2 次（COPY + RUN 至少各 1）。

- [ ] **Step 5: Commit**

```bash
git add services/stt-server/Dockerfile services/stt-server/Dockerfile.gpu
git commit -m "feat(stt-server): Dockerfile 改用 download_model.sh helper (T8)

- Dockerfile + Dockerfile.gpu: COPY + 4 行 download_model.sh 调用
- 各文件 min_bytes：encoder/joiner 1MB、decoder 100KB、tokens 1KB
- 防 HF 抖动 0-byte（T16 SP6 prod 验收实测痛点）
- tts-server 各 Dockerfile 经检查；如建时无 wget download 则不动

per spec §4.5"
```

---

## Task 9: docs — OPERATIONS.md §8 hot reload + §6 HF mirror 备份

**Files:**
- Modify: `OPERATIONS.md`

- [ ] **Step 1: 在 §6 末尾追加 6.5（HF mirror）**

读 `OPERATIONS.md` §6（SP5 加的 docker mirror），找到 §6.4 之后，插入 §6.5：

````markdown

### 6.5 HuggingFace 模型 build 时下载失败

stt-server / 其它服务 build 时通过 `scripts/download_model.sh` 下载 HF 模型。
默认走 `https://huggingface.co/...`；国内网络抖动会触发 retry × 3 + size check 防 0-byte（SP7 v0.14+）。

若 3 次重试全失败，建议切到 hf-mirror：

```bash
# Dockerfile 内的 ${HF_REPO} 不变；只把 base URL 改成 mirror
# 修改 services/stt-server/Dockerfile{,gpu} 行：
#   base="https://huggingface.co/${HF_REPO}/resolve/main"
# 改为：
#   base="https://hf-mirror.com/${HF_REPO}/resolve/main"
```

或临时设环境变量后 rebuild：

```bash
HF_ENDPOINT=https://hf-mirror.com docker compose --profile prod \
    build --no-cache stt-server
```

如已 build 成功的 image 模型损坏（0-byte），单独 `docker compose ... build --no-cache stt-server` 重建即可。
````

- [ ] **Step 2: 在 §7（SP6 multi-tenant）后追加 §8 hot reload**

````markdown

## §8 Auth Hot Reload（SP7, v0.14+）

### 8.1 工作原理

`rtvoice-admin` 命令（create / revoke / rotate）写 key 后，4 个服务 **<500ms 自动 pickup**（YAML backend）或 **<200ms**（Redis backend）。无需重启 service。

实现机制：
- **YAML backend**：watchdog 监 `/data/keys/keys.yaml` 父目录的文件变更；触发 100ms debounce 后整盘 reload
- **Redis backend**：admin CLI 写 key 后 `PUBLISH rtvoice:keys:changed`；4 服务订阅同 channel，触发 reload

### 8.2 配置 debounce 时长

```bash
# .env：
RTVOICE_KEYS_RELOAD_DEBOUNCE_MS=100   # 默认；可改 50 / 200 等
```

### 8.3 排障

#### 现象：admin CLI 写后服务侧仍 stale（401 token_revoked 不生效）

```bash
# 看服务日志是否触发 reload（YAML / Redis 都有 log）
docker logs rtvoice-realtime 2>&1 | grep "hot-reloaded"

# 没看到 reload log：
# 1. YAML：检查 keys.yaml 父目录可写（SP6-fix-2 父目录 mount，watchdog 监 inotify）
docker exec rtvoice-realtime ls -la /data/keys/

# 2. Redis：检查 RedisPubSubListener 是否在跑
docker exec rtvoice-realtime python3 -c "
import asyncio
async def main():
    import redis.asyncio as r
    c = r.from_url('redis://redis:6379/0')
    ps = c.pubsub()
    await ps.subscribe('rtvoice:keys:changed')
    info = await c.pubsub_numsub('rtvoice:keys:changed')
    print('subscribers:', info)
asyncio.run(main())
"
# 期望 subscribers >= 4（4 服务都订阅）

# 3. 强制 reload（紧急）：restart service
docker compose restart realtime-server
```

#### 现象：服务启动 log 无 "yaml file watcher started"
- 可能 watchdog 包没装：`docker exec rtvoice-realtime python3 -c "import watchdog; print(watchdog.__version__)"`
- v0.13.0 起 watchdog>=4.0 是 rtvoice_auth deps

#### 现象：YAML 频繁 reload（CPU 飙）
- 多次写触发：调高 `RTVOICE_KEYS_RELOAD_DEBOUNCE_MS=500`
- inotify watch leak：重启服务

````

- [ ] **Step 3: Commit**

```bash
git add OPERATIONS.md
git commit -m "docs(operations): §6.5 HF mirror + §8 auth hot reload (T9)

- §6.5: HF 模型 build 失败 → 切 hf-mirror.com（手动改 Dockerfile / env）
- §8.1-8.3: hot reload 工作原理（YAML watchdog / Redis pubsub）
  + debounce 配置 + 3 类排障（reload 不触/订阅没注册/reload 频繁）

per spec §4.6"
```

---

## Task 10: CHANGELOG v0.14.0 + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 在 [Unreleased] 之后插入 v0.14.0 entry**

```markdown
## [0.14.0] — 2026-05-11 — SP7 Auth Hot Reload + Build Stability

修补 SP6 prod 验收暴露的两大痛点。

### Added

- **`services/common/rtvoice_auth/watcher.py`** — 共享 watcher
  - `_Debouncer`：短时间多次 fire 只触一次 callback（默认 100ms）
  - `YamlFileWatcher`：watchdog Observer 后台线程 + `loop.call_soon_threadsafe` 唤起 asyncio debouncer；监父目录，handler 内 basename 过滤
  - `RedisPubSubListener`：订阅 `rtvoice:keys:changed`；自带 reconnect on disconnect
- **`scripts/download_model.sh`** — wget + size check + retry × 3
- **`RedisKeyStore.publish_change(key_id)`** — admin CLI 写后通知所有订阅者
- 4 服务 main.py lifespan 集成 watcher start/stop
- env `RTVOICE_KEYS_RELOAD_DEBOUNCE_MS=100`（可调）

### Changed

- admin CLI `create` / `revoke` / `rotate` 末尾调 `_maybe_publish_change`
  （Redis backend → PUBLISH；YAML backend → file write 自动触发 watcher）
- `services/stt-server/Dockerfile`{,.gpu}：wget 段改用 `download_model.sh`；
  各模型 min_bytes：encoder/joiner 1MB、decoder 100KB、tokens 1KB
- `OPERATIONS.md` §6.5：HF mirror 备份指南
- `OPERATIONS.md` §8：hot reload 工作原理 + 排障

### 验证（autonomous）

- ✅ watcher 6 单元测试（_Debouncer 2 + YAML 1 + Redis 3）
- ✅ store integration 4 测试（YAML reload 2 + Redis reload 1 + publish 1）
- ✅ admin commands publish 1 测试
- ✅ download_model.sh subprocess 2 测试
- ✅ realtime-server hot reload e2e 1 测试
- ✅ 总测试 165+ → 178+
- ⏳ prod：admin CLI create → < 1s 全 4 服务 pickup；stt rebuild 防 HF 抖动

### 设计决策（D-2026-05-11-E.1~E.4）

- YAML watchdog + Redis pubsub 双 backend 各自最优
- wget --tries=1 + size check + 外层 3 次 retry + sleep 3s
- 整盘 reload + 100ms debounce（store 已 in-memory dict，整盘 cheap）
- `scripts/download_model.sh` 在 monorepo root，各 Dockerfile COPY 调用
- token-server slowapi + per-key + 现 hot reload（不同维度共存）

详见 [SP7 设计](./docs/superpowers/specs/2026-05-11-sp7-auth-hot-reload-build-stability-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-11-sp7-auth-hot-reload-build-stability.md)。

---
```

- [ ] **Step 2: 文档链接 lint**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in README.md ARCHITECTURE.md DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/CONVENTIONS.md docs/api/stt.md docs/api/tts.md docs/api/sessions.md clients/python/README.md clients/web/README.md; do
    [ -e "$f" ] || continue
    echo "--- $f ---"
    grep -oE '\]\(\./[^)#]+' "$f" | sed 's/](\.\///' | sort -u | while read p; do
        [ -e "$p" ] && echo "  [ok] $p" || echo "  [FAIL] $p"
    done
done
```

Expected: 全 [ok]。

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): v0.14.0 — SP7 Auth Hot Reload + Build Stability (T10)

- Added: watcher.py + download_model.sh + RedisKeyStore.publish_change
- Changed: admin CLI auto-publish；stt Dockerfile{,gpu} 改用 helper；
  OPERATIONS §6.5 + §8
- 13 新单元测试；总测试 165+ → 178+
- prod 验收待 T11"

git push origin main 2>&1 | tail -10
```

---

## Task 11: prod 部署 + autonomous A1-A7 + user-participation B1-B3

**Files:** 无（read-only verification + remote ops）

- [ ] **Step 1: prod 端 git pull + 仅 rebuild stt-server（验 download_model.sh）+ 重启 4 服务**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -5
  echo
  # rebuild：stt 验 download_model.sh；realtime/tts/token 不需 rebuild（只是代码改 main.py，但 build context 改了所以也 rebuild）
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server stt-server tts-server token-server 2>&1 | tail -10
  echo
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d --force-recreate realtime-server stt-server tts-server token-server 2>&1 | tail -8
  echo
  for i in $(seq 1 24); do
    s1=$(docker inspect rtvoice-realtime --format "{{.State.Health.Status}}" 2>/dev/null)
    s2=$(docker inspect rtvoice-stt --format "{{.State.Health.Status}}" 2>/dev/null)
    s3=$(docker inspect rtvoice-tts --format "{{.State.Health.Status}}" 2>/dev/null)
    s4=$(docker inspect rtvoice-token --format "{{.State.Health.Status}}" 2>/dev/null)
    echo "[$i] rt=$s1 stt=$s2 tts=$s3 tok=$s4"
    [ "$s1" = "healthy" ] && [ "$s2" = "healthy" ] && [ "$s3" = "healthy" ] && [ "$s4" = "healthy" ] && break
    sleep 5
  done
}'
```

- [ ] **Step 2: autonomous A1-A7**

```bash
ssh root@192.168.66.163 '
echo "=== A1: 服务侧 log 含 \"yaml file watcher started\" ==="
docker logs rtvoice-realtime 2>&1 | grep -i "file watcher\|hot-reload\|pubsub" | head -5

echo
echo "=== A2: admin CLI create → < 1s service pickup ==="
OUT=$(docker exec rtvoice-realtime rtvoice-admin create --name sp7-test --sessions-concurrent 2 --sessions-per-hour 10 --scopes stt,tts,realtime,tokens 2>&1)
SEC=$(echo "$OUT" | python3 -c "import sys,json,re; d=sys.stdin.read(); m=re.search(r\"\\{[^{}]+(?:\\[[^\\]]*\\][^{}]*)*\\}\", d, re.DOTALL); print(json.loads(m.group())[\"secret\"]) if m else \"\"")
echo "secret length: ${#SEC}"
sleep 1   # < 1s watcher reload
docker exec rtvoice-realtime python3 -c "
import urllib.request, json
secret = \"$SEC\"
req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\", data=b\"{}\", headers={\"Content-Type\":\"application/json\", \"Authorization\": f\"Bearer {secret}\"})
r = urllib.request.urlopen(req, timeout=10)
print(\"\u2713 A2 status:\", r.status, \"session created without restart\")
"

echo
echo "=== A3: revoke → < 1s 失效 ==="
KID=$(docker exec rtvoice-realtime rtvoice-admin list --json 2>&1 | python3 -c "import sys, json; rows=json.load(sys.stdin); print([r[\"id\"] for r in rows if r[\"name\"]==\"sp7-test\"][0])")
docker exec rtvoice-realtime rtvoice-admin revoke "$KID"
sleep 1
docker exec rtvoice-realtime python3 -c "
import urllib.request, urllib.error, json
secret = \"$SEC\"
try:
    req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\", data=b\"{}\", headers={\"Authorization\": f\"Bearer {secret}\", \"Content-Type\":\"application/json\"})
    urllib.request.urlopen(req)
    print(\"FAIL: expected 401\")
except urllib.error.HTTPError as e:
    b = json.loads(e.read())
    assert e.code == 401 and b[\"code\"] == \"auth.token_revoked\"
    print(\"\u2713 A3 revoke < 1s 内 4 服务全失效\")
"

echo
echo "=== A4: stt Dockerfile download_model.sh 已应用 ==="
docker exec rtvoice-stt cat /usr/local/bin/download_model.sh 2>&1 | head -10
docker exec rtvoice-stt ls -la /app/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/ | head

echo
echo "=== A5: hot reload log（看 reload 计数）==="
docker logs rtvoice-realtime 2>&1 | grep -c "hot-reloaded"
echo "(应 >=2 per A2 + A3 各触发一次)"

echo
echo "=== A6/A7: token + tts/stt 也接 watcher（看 log）==="
for svc in token tts stt; do
    docker logs rtvoice-$svc 2>&1 | grep -i "watcher\|hot-reload" | head -1
done
'
```

- [ ] **Step 3: 通知 user B1-B3**

```
SP7 沙盒 + autonomous 完工。请你做：

1. **B1 SDK 切到刚创建的 sp7-test secret 验证零重启 pickup**：
   - 创建 cozyvoice2 key，记下 secret
   - 立即调用 RTVoice（无需任何重启）
   - 期望：第一次调用就成功，无 401

2. **B2 revoke 实时验证**：
   - admin CLI revoke 某 key
   - 立即用旧 secret 调用
   - 期望：1 秒内 4 服务全 401 auth.token_revoked

3. **B3 stt-server rebuild 验证防御**：
   - prod 端：docker compose build --no-cache stt-server
   - 若 HF 偶发抖动：会看到 retry 3 次 fail 后 build exit non-0
   - 不会再出现 v0.13 时的"容器 unhealthy"现象
```

- [ ] **Step 4: User 反馈后标 SP7 完工**

OK → SP7 done。
有 watcher 不触 / Dockerfile 改后 build fail / hot reload latency 高 → SP7-fix-N。

---

## Self-Review

### 1. Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §3 file layout | T1 watcher.py / T2 同 / T3 store integ / T4 admin / T5-T6 4 服务 / T7-T8 build |
| §4.1 watcher 接口 | T1 + T2 |
| §4.2 lifespan 集成 | T5 + T6 |
| §4.3 admin publish | T4 |
| §4.4 download_model.sh | T7 |
| §4.5 Dockerfile 改造 | T8 |
| §5 测试矩阵 13 | T1 3 + T2 3 + T3 4 + T4 1 + T5 1 + T7 2 = 14（spec 估 13，多 1） |
| §6 验收 A1-A7 + B1-B3 | T11 |
| §8 范围外 | 未实施任何 ✓ |

### 2. Placeholder scan

- 每 step 含完整代码或命令
- 无 TBD / TODO
- T6 5 文件同款 patch（plan 重复说一次同款模板，避免误"similar to TaskN"）
- T8 tts Dockerfile 检查标"如有 wget 则改"——实际指令明确

### 3. Type consistency

- `YamlFileWatcher / RedisPubSubListener / _Debouncer` 在 T1+T2 定义；T5/T6 4 服务 lifespan 引用一致
- `ReloadCallback` type alias 一致
- `publish_change(key_id)` 在 T3 加到 RedisKeyStore；T4 admin commands 调一致
- `_maybe_publish_change(store, key_id)` helper 在 T4 定义；create/revoke/rotate 3 处调用一致
- `RTVOICE_KEYS_RELOAD_DEBOUNCE_MS` env 在 T5/T6 5 服务一致 + OPERATIONS §8 文档化
- `rtvoice:keys:changed` channel name 在 T2 (listener) + T3 (publish_change) + T4 (admin) 一致

无类型/签名漂移。

### 4. 风险点 spec → plan 转化

| spec §7 风险 | plan 缓解 |
|---|---|
| watchdog inotify limit | 单文件监听 + 父目录监听 = 1 watch；不耗 max_user_watches |
| Redis PUBSUB 断线丢消息 | T2 listener 内置 reconnect 1s loop |
| File watcher 跨平台差异 | T1 用 `watchdog.observers.Observer`（自动选 inotify/FSEvents/PollingObserver）|
| Debounce 100ms 配置不灵活 | env `RTVOICE_KEYS_RELOAD_DEBOUNCE_MS`（T5/T6） |
| Service shutdown watcher 泄漏 | T5/T6 lifespan finally `await watcher.stop()` |
| HF 长期 down | OPERATIONS §6.5 加 hf-mirror 备份；不自动切 |
| YAML watcher 监父目录误触其它文件 | T1 handler 内 `basename(event.src_path) == target_basename` 过滤 |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-sp7-auth-hot-reload-build-stability.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + spec/quality 双审；与 SP1-SP6 同流程
2. **Inline Execution** — 本 session 批量执行 + checkpoints

Which approach?
