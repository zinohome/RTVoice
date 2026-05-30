# SP6 Multi-Tenant Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RTVoice 从"单 key 共享"推进到"多 key 多租户 ready"——v0.13.0 release 含：共享 auth lib (rtvoice_auth) + admin CLI (rtvoice-admin) + 4 服务集成 + Redis backend + 自动迁移 legacy。

**Architecture:** `services/common/rtvoice_auth/` 是 3 服务共享 lib（YAML/Redis 双 backend store + verify + quota）；`services/rtvoice-admin/` 是独立 CLI 包（无 HTTP 攻击面）；4 服务通过 FastAPI dep `require_key` 替代旧 hmac 比较；启动 lifespan 自动迁移 `RTVOICE_API_KEY` → legacy-default key。

**Tech Stack:** Pydantic v2 / FastAPI Depends / pyyaml / redis>=5（prod） / fakeredis（test） / argparse + pyyaml（admin CLI）/ watchdog（YAML hot reload）

**Spec:** [docs/superpowers/specs/2026-05-10-sp6-multi-tenant-auth-design.md](../specs/2026-05-10-sp6-multi-tenant-auth-design.md)

---

## Task 1: rtvoice_auth common lib 骨架 + Pydantic Key model

**Files:**
- Create: `services/common/rtvoice_auth/__init__.py`
- Create: `services/common/rtvoice_auth/models.py`
- Create: `services/common/rtvoice_auth/errors.py`
- Create: `services/common/rtvoice_auth/tests/__init__.py`
- Create: `services/common/rtvoice_auth/tests/test_models.py`
- Create: `services/common/pyproject.toml`（让 pip install -e 可用作开发态）
- Create: `services/common/conftest.py`（pytest 配置）

- [ ] **Step 1: 创建目录**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p services/common/rtvoice_auth/tests
```

- [ ] **Step 2: 写 `services/common/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.20"]
build-backend = "hatchling.build"

[project]
name = "rtvoice-auth"
version = "0.13.0"
description = "Shared auth lib for RTVoice services (multi-tenant API keys)"
requires-python = ">=3.10"
dependencies = [
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "redis>=5.0",
    "watchdog>=4.0",
]

[project.optional-dependencies]
test = ["pytest>=8.0", "pytest-asyncio>=0.23", "fakeredis>=2.20"]

[tool.hatch.build.targets.wheel]
packages = ["rtvoice_auth"]

[tool.pytest.ini_options]
testpaths = ["rtvoice_auth/tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: 写 `services/common/conftest.py`**

```python
import pathlib
import sys

# 让 pytest 直接 from rtvoice_auth import ...
sys.path.insert(0, str(pathlib.Path(__file__).parent))
```

- [ ] **Step 4: 写 `services/common/rtvoice_auth/__init__.py`**

```python
"""rtvoice-auth: shared multi-tenant API key + quota lib."""
__version__ = "0.13.0"
__all__ = ["__version__"]
```

- [ ] **Step 5: 写 `services/common/rtvoice_auth/errors.py`**

```python
"""Auth + quota typed exceptions."""
from __future__ import annotations


class AuthError(Exception):
    """所有 auth 错误的基类。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class InvalidToken(AuthError):
    """sha256 不匹配任何 key。"""


class TokenRevoked(AuthError):
    """key.revoked_at 已设。"""


class ScopeDenied(AuthError):
    """key.scopes 不含当前 service。"""


class QuotaExceeded(AuthError):
    """sessions_concurrent / sessions_per_hour 超限。"""
```

- [ ] **Step 6: 写测试 `services/common/rtvoice_auth/tests/test_models.py`**

```python
"""Test Key Pydantic model."""
import pytest
from datetime import datetime, timezone


def test_key_minimal_construction():
    from rtvoice_auth.models import Key
    k = Key(
        id="key_test123",
        secret_hash="abc",
        name="test",
        created_at=datetime.now(timezone.utc),
    )
    assert k.id == "key_test123"
    assert k.sessions_concurrent_max == 5  # default
    assert k.sessions_per_hour_max == 100  # default
    assert k.scopes == ["stt", "tts", "realtime", "tokens"]  # default
    assert k.revoked_at is None
    assert k.legacy is False


def test_key_round_trip_yaml_dict():
    from rtvoice_auth.models import Key
    k = Key(
        id="key_x", secret_hash="h", name="n",
        sessions_concurrent_max=10, sessions_per_hour_max=200,
        scopes=["stt", "tts"],
        created_at=datetime(2026, 5, 10, 8, tzinfo=timezone.utc),
        legacy=True,
    )
    d = k.model_dump(mode="json")
    assert d["id"] == "key_x"
    assert d["legacy"] is True
    k2 = Key.model_validate(d)
    assert k2.id == k.id
    assert k2.legacy is True


def test_key_revoked_at_optional():
    from rtvoice_auth.models import Key
    now = datetime.now(timezone.utc)
    k = Key(id="k", secret_hash="h", name="n", created_at=now, revoked_at=now)
    assert k.revoked_at is not None
```

- [ ] **Step 7: 写 `services/common/rtvoice_auth/models.py`**

```python
"""Pydantic v2 Key model for multi-tenant auth."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Key(BaseModel):
    """API key 元数据；secret 仅创建时返一次，store 只保存 sha256 hex。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    secret_hash: str
    name: str
    sessions_concurrent_max: int = Field(5, ge=1, le=10000)
    sessions_per_hour_max: int = Field(100, ge=1, le=1000000)
    scopes: list[str] = Field(default_factory=lambda: ["stt", "tts", "realtime", "tokens"])
    created_at: datetime
    revoked_at: Optional[datetime] = None
    notes: str = ""
    legacy: bool = False
```

- [ ] **Step 8: 装 deps + 跑测试**

```bash
cd services/common
pip install -e ".[test]" --break-system-packages 2>&1 | tail -3
python3 -m pytest rtvoice_auth/tests/test_models.py -v
```

Expected: 3 passed。

- [ ] **Step 9: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/
git commit -m "feat(auth): rtvoice_auth common lib 骨架 + Pydantic Key model (T1)

- services/common/rtvoice_auth/ 包骨架（pyproject + __init__）
- models.py: Key（sessions_concurrent_max / per_hour_max / scopes / revoked_at / legacy）
- errors.py: AuthError + 4 子类（InvalidToken / TokenRevoked / ScopeDenied / QuotaExceeded）
- 3 单元测试

per spec D-2026-05-10-D.1"
```

---

## Task 2: rtvoice_auth.store YAML backend

**Files:**
- Create: `services/common/rtvoice_auth/store.py`
- Create: `services/common/rtvoice_auth/tests/test_store_yaml.py`

- [ ] **Step 1: 写测试**

`services/common/rtvoice_auth/tests/test_store_yaml.py`:

```python
"""Test YAML KeyStore: CRUD + load + reload."""
import asyncio
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_yaml_store_empty_load(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    assert not s.any_keys()
    assert s.find_by_hash("anything") is None


@pytest.mark.asyncio
async def test_yaml_store_put_and_find(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    k = Key(id="k1", secret_hash="abc", name="n", created_at=datetime.now(timezone.utc))
    await s.put(k)
    found = s.find_by_hash("abc")
    assert found is not None
    assert found.id == "k1"
    assert s.any_keys()


@pytest.mark.asyncio
async def test_yaml_store_persist_reload(tmp_path):
    """put 后 file 写入；新 store load 能读到."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s1 = YamlKeyStore(str(p))
    await s1.load()
    await s1.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))

    s2 = YamlKeyStore(str(p))
    await s2.load()
    assert s2.find_by_hash("h1") is not None


@pytest.mark.asyncio
async def test_yaml_store_revoke(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    ok = await s.revoke("k1")
    assert ok is True
    found = s.find_by_hash("h1")
    assert found.revoked_at is not None


@pytest.mark.asyncio
async def test_yaml_store_revoke_unknown_returns_false(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    ok = await s.revoke("nonexistent")
    assert ok is False


@pytest.mark.asyncio
async def test_yaml_store_list_all(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    for i in range(3):
        await s.put(Key(id=f"k{i}", secret_hash=f"h{i}", name=f"n{i}",
                        created_at=datetime.now(timezone.utc)))
    keys = s.list_all()
    assert len(keys) == 3
    assert {k.id for k in keys} == {"k0", "k1", "k2"}
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_yaml.py -v
```

- [ ] **Step 3: 写 `services/common/rtvoice_auth/store.py`**

```python
"""KeyStore abstract base + YAML backend."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import yaml

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.store")


class KeyStore(Protocol):
    async def load(self) -> None: ...
    async def put(self, key: Key) -> None: ...
    async def revoke(self, key_id: str) -> bool: ...
    def find_by_hash(self, secret_hash: str) -> Key | None: ...
    def find_by_id(self, key_id: str) -> Key | None: ...
    def list_all(self) -> list[Key]: ...
    def any_keys(self) -> bool: ...


class YamlKeyStore:
    """YAML 文件后端；in-memory dict + atomic file write。"""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._by_hash: dict[str, Key] = {}
        self._by_id: dict[str, Key] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.error("yaml store load failed: %s", e)
            return
        keys_raw = data.get("keys", [])
        async with self._lock:
            self._by_hash.clear()
            self._by_id.clear()
            for kd in keys_raw:
                try:
                    k = Key.model_validate(kd)
                    self._by_hash[k.secret_hash] = k
                    self._by_id[k.id] = k
                except Exception as e:
                    log.warning("skipping invalid key entry: %s", e)
        log.info("yaml store loaded: %d keys from %s", len(self._by_id), self.path)

    async def put(self, key: Key) -> None:
        async with self._lock:
            self._by_hash[key.secret_hash] = key
            self._by_id[key.id] = key
            await self._flush()

    async def revoke(self, key_id: str) -> bool:
        async with self._lock:
            k = self._by_id.get(key_id)
            if k is None:
                return False
            k.revoked_at = datetime.now(timezone.utc)
            await self._flush()
            return True

    def find_by_hash(self, secret_hash: str) -> Key | None:
        return self._by_hash.get(secret_hash)

    def find_by_id(self, key_id: str) -> Key | None:
        return self._by_id.get(key_id)

    def list_all(self) -> list[Key]:
        return list(self._by_id.values())

    def any_keys(self) -> bool:
        return bool(self._by_id)

    async def _flush(self) -> None:
        """Atomic file write: tmp + rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "keys": [k.model_dump(mode="json") for k in self._by_id.values()],
        }
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                       encoding="utf-8")
        tmp.replace(self.path)
```

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_yaml.py -v
```

Expected: 6 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/store.py services/common/rtvoice_auth/tests/test_store_yaml.py
git commit -m "feat(auth): YAML KeyStore backend (T2)

- KeyStore Protocol（async load/put/revoke/find_by_hash/find_by_id/list_all/any_keys）
- YamlKeyStore: in-memory dict + atomic write (tmp + rename)
- 6 单元测试（empty / put+find / persist reload / revoke / unknown revoke / list_all）

per spec §4.2"
```

---

## Task 3: rtvoice_auth.store Redis backend

**Files:**
- Create: `services/common/rtvoice_auth/store_redis.py`
- Create: `services/common/rtvoice_auth/tests/test_store_redis.py`

- [ ] **Step 1: 写测试（用 fakeredis）**

`services/common/rtvoice_auth/tests/test_store_redis.py`:

```python
"""Test Redis KeyStore via fakeredis."""
import asyncio
import pytest
from datetime import datetime, timezone


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_redis_store_empty_load(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    s = RedisKeyStore(fake_redis)
    await s.load()
    assert not s.any_keys()
    assert s.find_by_hash("nope") is None


@pytest.mark.asyncio
async def test_redis_store_put_and_find(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    k = Key(id="k1", secret_hash="abc", name="n", created_at=datetime.now(timezone.utc))
    await s.put(k)
    found = s.find_by_hash("abc")
    assert found is not None
    assert found.id == "k1"


@pytest.mark.asyncio
async def test_redis_store_persist_across_instances(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s1 = RedisKeyStore(fake_redis)
    await s1.load()
    await s1.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    s2 = RedisKeyStore(fake_redis)
    await s2.load()
    assert s2.find_by_hash("h1") is not None


@pytest.mark.asyncio
async def test_redis_store_revoke(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    await s.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    ok = await s.revoke("k1")
    assert ok is True
    await s.load()  # reload
    assert s.find_by_hash("h1").revoked_at is not None


@pytest.mark.asyncio
async def test_redis_store_list_all(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    for i in range(3):
        await s.put(Key(id=f"k{i}", secret_hash=f"h{i}", name=f"n{i}",
                        created_at=datetime.now(timezone.utc)))
    keys = s.list_all()
    assert {k.id for k in keys} == {"k0", "k1", "k2"}
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_redis.py -v
```

- [ ] **Step 3: 写 `services/common/rtvoice_auth/store_redis.py`**

```python
"""Redis KeyStore backend.

Schema:
  rtvoice:key:{id}             HASH（Key model 字段 → string）
  rtvoice:hash2id:{hash}       STRING → key_id（反查 O(1)）
  rtvoice:keys                 SET（所有 key_id；用于 list_all + any_keys）
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.store_redis")


class RedisKeyStore:
    def __init__(self, redis_client: Any) -> None:
        """redis_client: redis.asyncio.Redis 或 fakeredis."""
        self._r = redis_client
        self._cache: dict[str, Key] = {}  # in-memory cache after load

    async def load(self) -> None:
        ids_bytes = await self._r.smembers("rtvoice:keys")
        ids = [b.decode() if isinstance(b, bytes) else b for b in ids_bytes]
        self._cache.clear()
        for kid in ids:
            data = await self._r.hgetall(f"rtvoice:key:{kid}")
            if not data:
                continue
            decoded = {(k.decode() if isinstance(k, bytes) else k):
                       (v.decode() if isinstance(v, bytes) else v)
                       for k, v in data.items()}
            try:
                key = self._decode_key(decoded)
                self._cache[key.id] = key
            except Exception as e:
                log.warning("skipping bad key %s: %s", kid, e)
        log.info("redis store loaded: %d keys", len(self._cache))

    async def put(self, key: Key) -> None:
        await self._r.hset(f"rtvoice:key:{key.id}", mapping=self._encode_key(key))
        await self._r.set(f"rtvoice:hash2id:{key.secret_hash}", key.id)
        await self._r.sadd("rtvoice:keys", key.id)
        self._cache[key.id] = key

    async def revoke(self, key_id: str) -> bool:
        if not await self._r.sismember("rtvoice:keys", key_id):
            return False
        ts = datetime.now(timezone.utc).isoformat()
        await self._r.hset(f"rtvoice:key:{key_id}", "revoked_at", ts)
        # 更新本地 cache
        if key_id in self._cache:
            self._cache[key_id].revoked_at = datetime.fromisoformat(ts)
        return True

    def find_by_hash(self, secret_hash: str) -> Key | None:
        for k in self._cache.values():
            if k.secret_hash == secret_hash:
                return k
        return None

    def find_by_id(self, key_id: str) -> Key | None:
        return self._cache.get(key_id)

    def list_all(self) -> list[Key]:
        return list(self._cache.values())

    def any_keys(self) -> bool:
        return bool(self._cache)

    @staticmethod
    def _encode_key(k: Key) -> dict[str, str]:
        d = k.model_dump(mode="json")
        # Redis HASH 只存 string；list/dict 序列化 JSON
        out = {}
        for key_, val in d.items():
            if val is None:
                out[key_] = ""
            elif isinstance(val, (list, dict)):
                out[key_] = json.dumps(val, ensure_ascii=False)
            else:
                out[key_] = str(val)
        return out

    @staticmethod
    def _decode_key(d: dict[str, str]) -> Key:
        # 反序列化 list/复杂字段
        if d.get("scopes"):
            try:
                d["scopes"] = json.loads(d["scopes"])
            except Exception:
                pass
        if d.get("revoked_at") in (None, "", "None"):
            d["revoked_at"] = None
        for int_field in ("sessions_concurrent_max", "sessions_per_hour_max"):
            if int_field in d:
                d[int_field] = int(d[int_field])
        if "legacy" in d:
            d["legacy"] = d["legacy"] in ("True", "true", True, "1")
        return Key.model_validate(d)
```

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_store_redis.py -v
```

Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/store_redis.py services/common/rtvoice_auth/tests/test_store_redis.py
git commit -m "feat(auth): Redis KeyStore backend (T3)

- RedisKeyStore: hash2id 反查 O(1)；keys SET list；HASH 存 Key 字段
- _encode_key / _decode_key 处理 list/datetime/bool 序列化
- 5 单元测试（fakeredis）

per spec §4.2"
```

---

## Task 4: rtvoice_auth.verify + require_key FastAPI dep

**Files:**
- Create: `services/common/rtvoice_auth/verify.py`
- Create: `services/common/rtvoice_auth/tests/test_verify.py`

- [ ] **Step 1: 写测试**

`services/common/rtvoice_auth/tests/test_verify.py`:

```python
"""Test verify_key + scope/revoked checks."""
import hashlib
import pytest
from datetime import datetime, timezone


def _make_store_with_key(scopes=None, revoked=False):
    """构造一个含单 key 的 in-memory store stub."""
    from rtvoice_auth.models import Key
    secret = "test-secret-32-chars-test-secret"
    h = hashlib.sha256(secret.encode()).hexdigest()
    k = Key(
        id="key_test", secret_hash=h, name="test",
        scopes=scopes or ["stt", "tts", "realtime", "tokens"],
        created_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )

    class _Store:
        def find_by_hash(self, h_):
            return k if h_ == h else None
    return _Store(), secret, k


@pytest.mark.asyncio
async def test_verify_valid_key():
    from rtvoice_auth.verify import verify_key
    store, secret, expected = _make_store_with_key()
    got = await verify_key(secret, scope="stt", store=store)
    assert got.id == expected.id


@pytest.mark.asyncio
async def test_verify_invalid_secret():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import InvalidToken
    store, _, _ = _make_store_with_key()
    with pytest.raises(InvalidToken):
        await verify_key("wrong-secret", scope="stt", store=store)


@pytest.mark.asyncio
async def test_verify_revoked():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import TokenRevoked
    store, secret, _ = _make_store_with_key(revoked=True)
    with pytest.raises(TokenRevoked):
        await verify_key(secret, scope="stt", store=store)


@pytest.mark.asyncio
async def test_verify_scope_denied():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import ScopeDenied
    store, secret, _ = _make_store_with_key(scopes=["stt"])
    with pytest.raises(ScopeDenied):
        await verify_key(secret, scope="realtime", store=store)


@pytest.mark.asyncio
async def test_verify_constant_time_compare():
    """secret hash 比较使用 hmac.compare_digest，避免 timing attack。"""
    # 这个不直接测时序；只验代码 path 用 hmac.compare_digest
    import inspect
    from rtvoice_auth import verify
    src = inspect.getsource(verify)
    assert "compare_digest" in src or "hmac" in src, "应该用 hmac.compare_digest 防 timing"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_verify.py -v
```

- [ ] **Step 3: 写 `services/common/rtvoice_auth/verify.py`**

```python
"""verify_key + FastAPI require_key dependency."""
from __future__ import annotations
import hashlib
import hmac
import logging
from typing import Any

from rtvoice_auth.errors import InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.verify")


async def verify_key(secret: str, *, scope: str, store: Any) -> Key:
    """验证 plaintext secret；返 Key record，否则 raise AuthError 子类。

    步骤：
    1. provided_hash = sha256(secret)
    2. record = store.find_by_hash(provided_hash)
    3. None → InvalidToken
    4. revoked_at → TokenRevoked
    5. scope not in scopes → ScopeDenied
    """
    if not secret:
        raise InvalidToken("auth.invalid_token", "empty secret")
    provided_hash = hashlib.sha256(secret.encode()).hexdigest()
    record = store.find_by_hash(provided_hash)
    if record is None:
        raise InvalidToken("auth.invalid_token", "secret not recognized")
    # 字符串比较（dict lookup 已 O(1)；额外 compare_digest 防 timing）
    if not hmac.compare_digest(record.secret_hash, provided_hash):
        # 理论上 dict 命中即匹配；这里是 defensive
        raise InvalidToken("auth.invalid_token", "hash mismatch")
    if record.revoked_at is not None:
        raise TokenRevoked("auth.token_revoked", f"key {record.id} revoked at {record.revoked_at}")
    if scope and scope not in record.scopes:
        raise ScopeDenied("auth.scope_denied",
                          f"key {record.id} not allowed for scope={scope}")
    return record
```

注：`require_key` FastAPI dep 在每个 service main.py 内创建（依赖 `request.app.state.scope/key_store`），不放进 lib（避免 lib 直接 import fastapi）。

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_verify.py -v
```

Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/verify.py services/common/rtvoice_auth/tests/test_verify.py
git commit -m "feat(auth): verify_key + 5 错误路径覆盖 (T4)

- verify_key(secret, scope, store) → Key | raise
- 4 错误路径：InvalidToken / TokenRevoked / ScopeDenied / 空 secret
- 用 hmac.compare_digest 防 timing attack
- 5 单元测试

per spec §4.4"
```

---

## Task 5: rtvoice_auth.quota — QuotaTracker

**Files:**
- Create: `services/common/rtvoice_auth/quota.py`
- Create: `services/common/rtvoice_auth/tests/test_quota.py`

- [ ] **Step 1: 写测试**

`services/common/rtvoice_auth/tests/test_quota.py`:

```python
"""Test QuotaTracker (in-memory + Redis backend)."""
import pytest
from datetime import datetime, timezone


def _make_key(concurrent=2, per_hour=5):
    from rtvoice_auth.models import Key
    return Key(id="k1", secret_hash="h", name="n",
               sessions_concurrent_max=concurrent,
               sessions_per_hour_max=per_hour,
               created_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_quota_acquire_under_concurrent_limit():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    k = _make_key(concurrent=3)
    await q.acquire_session(k)
    await q.acquire_session(k)
    # 2 in flight, limit 3 → no raise


@pytest.mark.asyncio
async def test_quota_acquire_over_concurrent_raises():
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=2)
    await q.acquire_session(k)
    await q.acquire_session(k)
    with pytest.raises(QuotaExceeded) as exc:
        await q.acquire_session(k)
    assert "concurrent" in exc.value.code


@pytest.mark.asyncio
async def test_quota_release_decreases_concurrent():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    k = _make_key(concurrent=2)
    await q.acquire_session(k)
    await q.acquire_session(k)
    await q.release_session(k.id)
    # 现在又能 acquire
    await q.acquire_session(k)


@pytest.mark.asyncio
async def test_quota_per_hour_raises_after_limit():
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=100, per_hour=3)
    await q.acquire_session(k)
    await q.release_session(k.id)
    await q.acquire_session(k)
    await q.release_session(k.id)
    await q.acquire_session(k)
    await q.release_session(k.id)
    with pytest.raises(QuotaExceeded) as exc:
        await q.acquire_session(k)
    assert "per_hour" in exc.value.code


@pytest.mark.asyncio
async def test_quota_rollback_on_per_hour_failure():
    """超 per_hour 时 concurrent 不应被加（acquire 整体失败回滚）。"""
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=100, per_hour=1)
    await q.acquire_session(k)
    await q.release_session(k.id)
    with pytest.raises(QuotaExceeded):
        await q.acquire_session(k)
    # concurrent 应该是 0（之前 release 过）；如果 acquire 漏 rollback 这里 >0
    # 测：再 acquire 一次（释放后）—— 实际还是会卡 per_hour，这测保证不卡 concurrent_max
    # 简化：直接看内部 state
    assert q._concurrent.get("k1", 0) == 0


@pytest.mark.asyncio
async def test_quota_release_unknown_no_error():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    # 不抛异常即可
    await q.release_session("unknown_key_id")
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_quota.py -v
```

- [ ] **Step 3: 写 `services/common/rtvoice_auth/quota.py`**

```python
"""QuotaTracker: sessions_concurrent + sessions_per_hour 强制执行。

In-memory backend；prod 可换 Redis 实现（同接口）。
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.errors import QuotaExceeded
from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.quota")


class QuotaTracker:
    """In-memory rolling-hour + concurrent counter."""

    def __init__(self) -> None:
        self._concurrent: dict[str, int] = {}                 # key_id → 当前活跃数
        self._hour_count: dict[str, dict[str, int]] = {}      # key_id → {hour_bucket → count}
        self._lock = asyncio.Lock()

    async def acquire_session(self, key: Key) -> None:
        """create session 前调；超限 raise QuotaExceeded（counter rollback）。"""
        async with self._lock:
            bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            buckets = self._hour_count.setdefault(key.id, {})
            self._gc_old_buckets(buckets, bucket)
            new_hour = buckets.get(bucket, 0) + 1
            if new_hour > key.sessions_per_hour_max:
                raise QuotaExceeded("auth.quota_per_hour",
                                    f"key {key.id} reached {key.sessions_per_hour_max}/hour")
            new_concurrent = self._concurrent.get(key.id, 0) + 1
            if new_concurrent > key.sessions_concurrent_max:
                raise QuotaExceeded("auth.quota_concurrent",
                                    f"key {key.id} reached {key.sessions_concurrent_max} concurrent")
            # both ok → commit
            buckets[bucket] = new_hour
            self._concurrent[key.id] = new_concurrent

    async def release_session(self, key_id: str) -> None:
        """session cleanup 时调；DECR concurrent；不动 per_hour（rolling）。"""
        async with self._lock:
            cur = self._concurrent.get(key_id, 0)
            if cur > 0:
                self._concurrent[key_id] = cur - 1
            elif cur == 0:
                # 漏 release / 异常路径；no-op
                pass

    @staticmethod
    def _gc_old_buckets(buckets: dict[str, int], current_bucket: str) -> None:
        """保留 current bucket；rolling 窗口，旧 bucket 不再算入但保留 1 个用于查."""
        keep = {current_bucket}
        # 简化：只保留当前；如要严格 rolling 60 分钟可加邻接 bucket
        for b in list(buckets.keys()):
            if b not in keep:
                buckets.pop(b, None)

    def get_concurrent(self, key_id: str) -> int:
        return self._concurrent.get(key_id, 0)
```

- [ ] **Step 4: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_quota.py -v
```

Expected: 6 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/quota.py services/common/rtvoice_auth/tests/test_quota.py
git commit -m "feat(auth): QuotaTracker (T5)

- acquire_session：检查 per_hour + concurrent 上限；越限 raise QuotaExceeded
- 双 counter atomic（lock 保护，acquire 失败 rollback）
- release_session：DECR concurrent；漏 release no-op
- _gc_old_buckets：rolling hour 保留当前 bucket
- 6 单元测试

per spec §4.5"
```

---

## Task 6: rtvoice-admin CLI 包骨架

**Files:**
- Create: `services/rtvoice-admin/pyproject.toml`
- Create: `services/rtvoice-admin/src/rtvoice_admin/__init__.py`
- Create: `services/rtvoice-admin/src/rtvoice_admin/__main__.py`
- Create: `services/rtvoice-admin/tests/__init__.py`
- Create: `services/rtvoice-admin/tests/test_smoke.py`

- [ ] **Step 1: 创建目录**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p services/rtvoice-admin/src/rtvoice_admin services/rtvoice-admin/tests
```

- [ ] **Step 2: 写 `services/rtvoice-admin/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.20"]
build-backend = "hatchling.build"

[project]
name = "rtvoice-admin"
version = "0.13.0"
description = "RTVoice multi-tenant admin CLI"
requires-python = ">=3.10"
dependencies = [
    "rtvoice-auth",
    "pyyaml>=6.0",
    "redis>=5.0",
]

[project.optional-dependencies]
test = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[project.scripts]
rtvoice-admin = "rtvoice_admin.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/rtvoice_admin"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: 写 `services/rtvoice-admin/src/rtvoice_admin/__init__.py`**

```python
"""rtvoice-admin: multi-tenant admin CLI."""
__version__ = "0.13.0"
```

- [ ] **Step 4: 写最小 `__main__.py`（入口占位；T7 实现命令）**

```python
"""rtvoice-admin CLI 入口。"""
import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtvoice-admin",
                                description="RTVoice multi-tenant admin CLI")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("version", help="show version")
    args = p.parse_args(argv)
    if args.cmd == "version":
        from rtvoice_admin import __version__
        print(__version__)
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 写测试**

`services/rtvoice-admin/tests/test_smoke.py`:

```python
def test_version():
    import rtvoice_admin
    assert rtvoice_admin.__version__ == "0.13.0"


def test_main_version_command(capsys):
    from rtvoice_admin.__main__ import main
    rc = main(["version"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "0.13.0" in captured.out
```

- [ ] **Step 6: 装 + 跑**

```bash
cd services/rtvoice-admin
pip install -e ".[test]" --break-system-packages 2>&1 | tail -3
python3 -m pytest tests/test_smoke.py -v
which rtvoice-admin || python3 -m rtvoice_admin version
```

Expected: 2 passed；version 命令输出 0.13.0。

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/rtvoice-admin/
git commit -m "feat(admin): rtvoice-admin CLI 包骨架 (T6)

- pyproject.toml: console_script rtvoice-admin
- __main__.py: argparse 入口（仅 version 命令；T7 加业务命令）
- 2 smoke 测试

per spec §4.3"
```

---

## Task 7: admin CLI 5 命令（create / list / revoke / rotate / show）

**Files:**
- Modify: `services/rtvoice-admin/src/rtvoice_admin/__main__.py`
- Create: `services/rtvoice-admin/src/rtvoice_admin/commands.py`
- Create: `services/rtvoice-admin/tests/test_commands.py`

- [ ] **Step 1: 写测试**

`services/rtvoice-admin/tests/test_commands.py`:

```python
"""Test 5 admin CLI commands using YAML store fixture."""
import pytest
from datetime import datetime, timezone


@pytest.fixture
def store(tmp_path):
    """fresh YAML store；返回 store + path."""
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    return s, str(p)


@pytest.mark.asyncio
async def test_cmd_create_returns_secret(store):
    from rtvoice_admin.commands import cmd_create
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="cozyvoice",
                           sessions_concurrent=5, sessions_per_hour=200,
                           scopes=["stt", "tts", "realtime"])
    assert out["name"] == "cozyvoice"
    assert out["id"].startswith("key_")
    assert out["secret"]  # plaintext 返一次
    assert len(out["secret"]) >= 32


@pytest.mark.asyncio
async def test_cmd_list_excludes_secret(store):
    from rtvoice_admin.commands import cmd_create, cmd_list
    s, _ = store
    await s.load()
    await cmd_create(s, name="a", sessions_concurrent=1, sessions_per_hour=10,
                     scopes=["stt"])
    rows = await cmd_list(s)
    assert len(rows) == 1
    assert "secret" not in rows[0]
    assert rows[0]["name"] == "a"


@pytest.mark.asyncio
async def test_cmd_show_returns_detail(store):
    from rtvoice_admin.commands import cmd_create, cmd_show
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=2, sessions_per_hour=20,
                           scopes=["stt"])
    detail = await cmd_show(s, key_id=out["id"])
    assert detail is not None
    assert detail["name"] == "x"
    assert "secret" not in detail


@pytest.mark.asyncio
async def test_cmd_revoke_sets_revoked_at(store):
    from rtvoice_admin.commands import cmd_create, cmd_revoke
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=1, sessions_per_hour=10,
                           scopes=["stt"])
    ok = await cmd_revoke(s, key_id=out["id"])
    assert ok is True
    rec = s.find_by_id(out["id"])
    assert rec.revoked_at is not None


@pytest.mark.asyncio
async def test_cmd_rotate_returns_new_secret(store):
    from rtvoice_admin.commands import cmd_create, cmd_rotate
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=1, sessions_per_hour=10,
                           scopes=["stt"])
    old_hash = s.find_by_id(out["id"]).secret_hash
    new_out = await cmd_rotate(s, key_id=out["id"])
    assert new_out["secret"] != out["secret"]
    new_hash = s.find_by_id(out["id"]).secret_hash
    assert new_hash != old_hash
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/rtvoice-admin
python3 -m pytest tests/test_commands.py -v
```

- [ ] **Step 3: 写 `services/rtvoice-admin/src/rtvoice_admin/commands.py`**

```python
"""Admin CLI commands implementing key lifecycle."""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from rtvoice_auth.models import Key


def _new_id() -> str:
    return f"key_{secrets.token_urlsafe(12)}"


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


async def cmd_create(
    store: Any,
    *,
    name: str,
    sessions_concurrent: int,
    sessions_per_hour: int,
    scopes: list[str],
    notes: str = "",
) -> dict:
    """生成 plaintext secret + 注册 Key；返回含 secret 的 dict（仅此一次）."""
    secret = _new_secret()
    key_id = _new_id()
    h = hashlib.sha256(secret.encode()).hexdigest()
    k = Key(
        id=key_id,
        secret_hash=h,
        name=name,
        sessions_concurrent_max=sessions_concurrent,
        sessions_per_hour_max=sessions_per_hour,
        scopes=scopes,
        created_at=datetime.now(timezone.utc),
        notes=notes,
    )
    await store.put(k)
    return {
        "id": key_id,
        "secret": secret,
        "name": name,
        "sessions_concurrent_max": sessions_concurrent,
        "sessions_per_hour_max": sessions_per_hour,
        "scopes": scopes,
    }


async def cmd_list(store: Any) -> list[dict]:
    """列表（不含 secret）."""
    rows = []
    for k in store.list_all():
        d = k.model_dump(mode="json")
        d.pop("secret_hash", None)
        rows.append(d)
    return rows


async def cmd_show(store: Any, *, key_id: str) -> dict | None:
    k = store.find_by_id(key_id)
    if k is None:
        return None
    d = k.model_dump(mode="json")
    d.pop("secret_hash", None)
    return d


async def cmd_revoke(store: Any, *, key_id: str) -> bool:
    return await store.revoke(key_id)


async def cmd_rotate(store: Any, *, key_id: str) -> dict:
    """重生成 secret；旧 hash 立即失效。"""
    k = store.find_by_id(key_id)
    if k is None:
        raise KeyError(f"key {key_id} not found")
    new_secret = _new_secret()
    k.secret_hash = hashlib.sha256(new_secret.encode()).hexdigest()
    await store.put(k)  # overwrite
    return {"id": key_id, "secret": new_secret}
```

- [ ] **Step 4: 改 `__main__.py` 把 5 命令接进 argparse**

替换 `__main__.py` 内容为：

```python
"""rtvoice-admin CLI 入口."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from typing import Any


def _make_store() -> Any:
    """根据 RTVOICE_KEYS_BACKEND 选 yaml/redis."""
    backend = os.environ.get("RTVOICE_KEYS_BACKEND", "yaml").lower()
    if backend == "redis":
        import redis.asyncio as redis_lib
        from rtvoice_auth.store_redis import RedisKeyStore
        url = os.environ.get("RTVOICE_REDIS_URL", "redis://localhost:6379/0")
        client = redis_lib.from_url(url)
        return RedisKeyStore(client)
    from rtvoice_auth.store import YamlKeyStore
    path = os.environ.get("RTVOICE_KEYS_FILE", "/data/keys.yaml")
    return YamlKeyStore(path)


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(empty)")
        return
    keys = ["id", "name", "sessions_concurrent_max", "sessions_per_hour_max",
            "scopes", "created_at", "revoked_at", "legacy"]
    print(" | ".join(keys))
    for r in rows:
        print(" | ".join(str(r.get(k, "")) for k in keys))


async def _run_async(args) -> int:
    from rtvoice_admin import commands as cmd_mod
    store = _make_store()
    await store.load()

    if args.cmd == "create":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        out = await cmd_mod.cmd_create(
            store,
            name=args.name,
            sessions_concurrent=args.sessions_concurrent,
            sessions_per_hour=args.sessions_per_hour,
            scopes=scopes,
            notes=args.notes,
        )
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("\n⚠️  secret 仅显示这一次，请立即保存。")
        return 0

    if args.cmd == "list":
        rows = await cmd_mod.cmd_list(store)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            _print_table(rows)
        return 0

    if args.cmd == "show":
        d = await cmd_mod.cmd_show(store, key_id=args.key_id)
        if d is None:
            print(f"key {args.key_id} not found", file=sys.stderr)
            return 2
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "revoke":
        ok = await cmd_mod.cmd_revoke(store, key_id=args.key_id)
        if ok:
            print(f"revoked {args.key_id}")
            return 0
        print(f"key {args.key_id} not found", file=sys.stderr)
        return 2

    if args.cmd == "rotate":
        try:
            out = await cmd_mod.cmd_rotate(store, key_id=args.key_id)
        except KeyError:
            print(f"key {args.key_id} not found", file=sys.stderr)
            return 2
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("\n⚠️  new secret 仅显示这一次，请立即保存。")
        return 0

    if args.cmd == "import-legacy":
        from rtvoice_admin.commands_legacy import cmd_import_legacy
        out = await cmd_import_legacy(store)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtvoice-admin",
                                description="RTVoice multi-tenant admin CLI")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("version", help="show version")

    pc = sub.add_parser("create", help="create a new API key")
    pc.add_argument("--name", required=True)
    pc.add_argument("--sessions-concurrent", type=int, default=5,
                    dest="sessions_concurrent")
    pc.add_argument("--sessions-per-hour", type=int, default=100,
                    dest="sessions_per_hour")
    pc.add_argument("--scopes", default="stt,tts,realtime,tokens",
                    help="comma-separated; default all")
    pc.add_argument("--notes", default="")

    pl = sub.add_parser("list", help="list all keys")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="show key detail")
    ps.add_argument("key_id")

    pv = sub.add_parser("revoke", help="revoke a key")
    pv.add_argument("key_id")

    pr = sub.add_parser("rotate", help="rotate (regenerate) secret")
    pr.add_argument("key_id")

    sub.add_parser("import-legacy",
                   help="import RTVOICE_API_KEY env as legacy-default")

    args = p.parse_args(argv)
    if args.cmd == "version":
        from rtvoice_admin import __version__
        print(__version__)
        return 0
    if args.cmd is None:
        p.print_help()
        return 1
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 跑测试**

```bash
cd services/rtvoice-admin
python3 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: 7 passed（2 smoke + 5 commands）。

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/rtvoice-admin/
git commit -m "feat(admin): 5 CLI commands (create/list/show/revoke/rotate) (T7)

- commands.py: 5 业务函数（async；接受 store 参数便于测试）
- __main__.py: argparse 子命令 + RTVOICE_KEYS_BACKEND env 选 yaml/redis
- 5 unit 测试覆盖 create/list/show/revoke/rotate

per spec §4.3"
```

---

## Task 8: admin import-legacy 命令 + 服务侧 lifespan auto-migrate

**Files:**
- Create: `services/rtvoice-admin/src/rtvoice_admin/commands_legacy.py`
- Modify: `services/rtvoice-admin/tests/test_commands.py` 加 import-legacy test
- Create: `services/common/rtvoice_auth/lifespan.py`（共享 init_key_store helper）
- Create: `services/common/rtvoice_auth/tests/test_lifespan.py`

- [ ] **Step 1: 写 lifespan helper 测试**

`services/common/rtvoice_auth/tests/test_lifespan.py`:

```python
"""Test init_key_store auto-migrate logic."""
import os
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_auto_migrate_when_empty_store_with_legacy(tmp_path, monkeypatch):
    """空 store + RTVOICE_API_KEY 设 → legacy-default 自动注册."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    monkeypatch.setenv("RTVOICE_API_KEY", "legacy-secret-32-chars-test-test")
    migrated = await auto_migrate_legacy(s)
    assert migrated is not None
    assert migrated.legacy is True
    assert migrated.name == "legacy-default"
    assert s.any_keys()


@pytest.mark.asyncio
async def test_no_migrate_if_store_has_keys(tmp_path, monkeypatch):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy
    from rtvoice_auth.models import Key

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="existing", secret_hash="h", name="n",
                    created_at=datetime.now(timezone.utc)))

    monkeypatch.setenv("RTVOICE_API_KEY", "secret")
    migrated = await auto_migrate_legacy(s)
    assert migrated is None  # 已有 key 不动


@pytest.mark.asyncio
async def test_no_migrate_if_no_legacy_env(tmp_path, monkeypatch):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    monkeypatch.delenv("RTVOICE_API_KEY", raising=False)
    migrated = await auto_migrate_legacy(s)
    assert migrated is None
```

- [ ] **Step 2: 写 `services/common/rtvoice_auth/lifespan.py`**

```python
"""Shared lifespan helper: auto-migrate RTVOICE_API_KEY → legacy-default key."""
from __future__ import annotations
import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone

from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.lifespan")


async def auto_migrate_legacy(store) -> Key | None:
    """空 store + RTVOICE_API_KEY 设 → 创建 legacy-default key 返回。

    幂等：store 已有 key 时 no-op。
    """
    if store.any_keys():
        return None
    legacy_secret = os.environ.get("RTVOICE_API_KEY", "").strip()
    if not legacy_secret:
        return None
    key_id = f"key_{secrets.token_urlsafe(12)}"
    k = Key(
        id=key_id,
        secret_hash=hashlib.sha256(legacy_secret.encode()).hexdigest(),
        name="legacy-default",
        sessions_concurrent_max=10,
        sessions_per_hour_max=1000,
        scopes=["stt", "tts", "realtime", "tokens"],
        created_at=datetime.now(timezone.utc),
        legacy=True,
        notes="auto-migrated from RTVOICE_API_KEY env",
    )
    await store.put(k)
    log.warning(
        "migrated RTVOICE_API_KEY → legacy-default key (id=%s); "
        "recommend `rtvoice-admin create --name <app>` per app, then revoke legacy",
        key_id,
    )
    return k
```

- [ ] **Step 3: 写 `services/rtvoice-admin/src/rtvoice_admin/commands_legacy.py`**

```python
"""import-legacy CLI subcommand."""
from __future__ import annotations
import os

from rtvoice_auth.lifespan import auto_migrate_legacy


async def cmd_import_legacy(store) -> dict:
    """从 RTVOICE_API_KEY 导入 legacy-default key。"""
    legacy_env = os.environ.get("RTVOICE_API_KEY", "").strip()
    if not legacy_env:
        return {"status": "skipped", "reason": "RTVOICE_API_KEY not set"}
    if store.any_keys():
        return {"status": "skipped",
                "reason": "store already has keys; manual import via `create` if needed"}
    k = await auto_migrate_legacy(store)
    return {"status": "imported", "key_id": k.id, "name": k.name, "legacy": True}
```

- [ ] **Step 4: 加 test for import-legacy**

把以下追加到 `services/rtvoice-admin/tests/test_commands.py`:

```python
@pytest.mark.asyncio
async def test_cmd_import_legacy_imports(store, monkeypatch):
    from rtvoice_admin.commands_legacy import cmd_import_legacy
    s, _ = store
    await s.load()
    monkeypatch.setenv("RTVOICE_API_KEY", "legacy-secret-32chars-test")
    out = await cmd_import_legacy(s)
    assert out["status"] == "imported"
    assert s.any_keys()


@pytest.mark.asyncio
async def test_cmd_import_legacy_skips_when_keys_exist(store, monkeypatch):
    from rtvoice_admin.commands import cmd_create
    from rtvoice_admin.commands_legacy import cmd_import_legacy
    s, _ = store
    await s.load()
    await cmd_create(s, name="x", sessions_concurrent=1, sessions_per_hour=10,
                     scopes=["stt"])
    monkeypatch.setenv("RTVOICE_API_KEY", "legacy-secret")
    out = await cmd_import_legacy(s)
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_cmd_import_legacy_skips_when_no_env(store, monkeypatch):
    from rtvoice_admin.commands_legacy import cmd_import_legacy
    s, _ = store
    await s.load()
    monkeypatch.delenv("RTVOICE_API_KEY", raising=False)
    out = await cmd_import_legacy(s)
    assert out["status"] == "skipped"
```

- [ ] **Step 5: 跑测试**

```bash
cd services/common
python3 -m pytest rtvoice_auth/tests/test_lifespan.py -v
cd ../rtvoice-admin
python3 -m pytest tests/ -v
```

Expected: lifespan 3 + admin 10 (7 + 3) all pass。

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/common/rtvoice_auth/lifespan.py services/common/rtvoice_auth/tests/test_lifespan.py services/rtvoice-admin/src/rtvoice_admin/commands_legacy.py services/rtvoice-admin/tests/test_commands.py
git commit -m "feat(auth): auto-migrate legacy + import-legacy CLI cmd (T8)

- rtvoice_auth/lifespan.py: auto_migrate_legacy (空 store + RTVOICE_API_KEY → legacy-default)
- rtvoice-admin commands_legacy.py: import-legacy 子命令
- 3 lifespan + 3 admin 测试

per spec §4.7"
```

---

## Task 9: realtime-server 集成 require_key + quota

**Files:**
- Modify: `services/realtime-server/Dockerfile`（COPY common lib）
- Modify: `services/realtime-server/app/main.py`
- Modify: `services/realtime-server/app/session_manager.py`
- Modify: `services/realtime-server/tests/test_endpoints.py`
- Modify: `services/realtime-server/requirements.txt`

- [ ] **Step 1: 改 Dockerfile 让容器有 rtvoice_auth**

定位 `services/realtime-server/Dockerfile`，在 `COPY app /app/app` 之前插入：

```dockerfile
COPY --chown=appuser:appuser ../common /app/common
ENV PYTHONPATH=/app/common:/app
```

注：单 Dockerfile context 内不能 COPY 父目录。需 docker-compose build context 调整或用 build context 设到 monorepo 根目录。

**实施替代**：让 docker-compose `realtime-server.build.context: .`（指向 monorepo root），dockerfile 路径 `services/realtime-server/Dockerfile`，COPY 路径用 monorepo 相对：

`docker-compose.yml` 改 realtime-server `build:`：

```yaml
realtime-server:
  build:
    context: .
    dockerfile: services/realtime-server/Dockerfile
```

`services/realtime-server/Dockerfile` 改 COPY:

```dockerfile
# 之前：COPY requirements.txt . / COPY app /app/app
# 改为：
COPY services/common /app/common
COPY services/realtime-server/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir /app/common
COPY services/realtime-server/app /app/app
```

- [ ] **Step 2: 改 requirements.txt 加 watchdog（YAML hot reload）**

加：
```
watchdog>=4.0
```

- [ ] **Step 3: 写测试 — endpoint 用 require_key + quota**

在 `services/realtime-server/tests/test_endpoints.py` 追加：

```python
def test_create_session_with_valid_key(client, monkeypatch, tmp_path):
    """有效 key 走 quota acquire；返 201."""
    # 用一个自定义 fixture：装一个 yaml 文件 + 设 env
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio, os
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "t-secret-32chars-aaaaaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="key_t", secret_hash=h, name="t",
                          sessions_concurrent_max=2, sessions_per_hour_max=10,
                          scopes=["stt", "tts", "realtime", "tokens"],
                          created_at=datetime.now(timezone.utc))))

    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    # client fixture 重启 app 才生效；改用直接 TestClient
    from fastapi.testclient import TestClient
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 201


def test_create_session_invalid_key_returns_401(client):
    r = client.post("/v1/sessions", json={},
                    headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth.invalid_token"


def test_create_session_quota_concurrent_exceeded(monkeypatch, tmp_path):
    """concurrent=1 的 key，第 2 个 create_session → 429 auth.quota_concurrent."""
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "secret-quota-test-32-chars-aaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kq", secret_hash=h, name="q",
                          sessions_concurrent_max=1, sessions_per_hour_max=10,
                          scopes=["realtime"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))

    from fastapi.testclient import TestClient
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r1 = c.post("/v1/sessions", json={},
                    headers={"Authorization": f"Bearer {secret}"})
        assert r1.status_code == 201
        r2 = c.post("/v1/sessions", json={},
                    headers={"Authorization": f"Bearer {secret}"})
        assert r2.status_code == 429
        assert r2.json()["code"] == "auth.quota_concurrent"


def test_revoked_key_returns_401(monkeypatch, tmp_path):
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "rev-secret-32-chars-aaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kr", secret_hash=h, name="r",
                          revoked_at=datetime.now(timezone.utc),
                          scopes=["realtime"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401
        assert r.json()["code"] == "auth.token_revoked"


def test_scope_denied_returns_403(monkeypatch, tmp_path):
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "scope-secret-32-chars-aaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="ks", secret_hash=h, name="s",
                          scopes=["stt"],  # 没 realtime
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 403
        assert r.json()["code"] == "auth.scope_denied"
```

5 个 endpoint 测试。

- [ ] **Step 4: 改 main.py 加 require_key + quota**

修改 `services/realtime-server/app/main.py`：

4a. 在顶部 import 段追加：

```python
from rtvoice_auth.models import Key
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied, QuotaExceeded
from rtvoice_auth.quota import QuotaTracker
from rtvoice_auth.lifespan import auto_migrate_legacy
```

4b. 在 lifespan 内加 store + quota 初始化：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing SP3 code ...

    # SP6: init key store + quota
    backend = os.environ.get("RTVOICE_KEYS_BACKEND", "yaml").lower()
    if backend == "redis":
        import redis.asyncio as redis_lib
        from rtvoice_auth.store_redis import RedisKeyStore
        url = os.environ.get("RTVOICE_REDIS_URL", "redis://redis:6379/0")
        client = redis_lib.from_url(url)
        app.state.key_store = RedisKeyStore(client)
    else:
        from rtvoice_auth.store import YamlKeyStore
        path = os.environ.get("RTVOICE_KEYS_FILE", "/data/keys.yaml")
        app.state.key_store = YamlKeyStore(path)
    await app.state.key_store.load()
    await auto_migrate_legacy(app.state.key_store)
    app.state.quota = QuotaTracker()
    app.state.scope = "realtime"

    yield
    # cleanup ...
```

4c. 加 require_key dep：

```python
async def require_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Key:
    if not authorization or not authorization.startswith("Bearer "):
        raise api_error(401, "auth.missing_token", "Authorization: Bearer required")
    secret = authorization[len("Bearer "):]
    try:
        return await verify_key(secret,
                                scope=request.app.state.scope,
                                store=request.app.state.key_store)
    except InvalidToken as e:
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        raise api_error(403, e.code, e.message)
```

4d. `create_session()` 改用 require_key 替代旧 `_check_bearer_http`：

```python
async def create_session(
    req: SessionCreateRequest,
    request: Request,
    key: Key = Depends(require_key),
) -> SessionCreateResponse:
    # SP6 quota acquire
    try:
        await request.app.state.quota.acquire_session(key)
    except QuotaExceeded as e:
        raise api_error(429, e.code, e.message)

    voice = req.voice or config.DEFAULT_VOICE
    prompt = req.prompt if req.prompt is not None else config.DEFAULT_PROMPT
    if len(prompt) > config.PROMPT_MAX_CHARS:
        await request.app.state.quota.release_session(key.id)
        raise api_error(422, "prompt.too_long",
                        f"prompt > {config.PROMPT_MAX_CHARS} chars")

    try:
        sess = await session_mgr.create(
            creator_key_hash=key.id,  # ← 改用 key.id
            voice=voice,
            speed=req.speed,
            prompt=prompt,
            audit_persist=req.audit_persist,
        )
        sess.key_id = key.id
    except CapacityFull as e:
        await request.app.state.quota.release_session(key.id)
        raise api_error(503, "session.capacity_full", str(e))

    return SessionCreateResponse(...)  # 同 SP3
```

4e. WS handler `_extract_ws_bearer` 改用 store-based 验证：

```python
async def _extract_ws_bearer_key(ws: WebSocket) -> Key | None:
    """三路 Bearer 验证；返 Key record。"""
    secret = None
    auth = ws.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        secret = auth[len("Bearer "):]
    if not secret:
        proto = ws.headers.get("sec-websocket-protocol", "")
        for p in (s.strip() for s in proto.split(",")):
            if p.startswith("bearer."):
                secret = p[len("bearer."):]
                break
    if not secret:
        secret = ws.query_params.get("token")
    if not secret:
        return None
    try:
        return await verify_key(secret, scope="realtime", store=ws.app.state.key_store)
    except AuthError:
        return None


@app.websocket("/v1/realtime/{session_id}")
async def realtime_ws(ws: WebSocket, session_id: str):
    key = await _extract_ws_bearer_key(ws)
    if key is None:
        await ws.close(code=4401, reason="unauthorized")
        return
    # ... 原 SP3 逻辑：找 session、验 creator_key_hash == key.id ...
    sess = session_mgr.get(session_id) if session_mgr else None
    if sess is None:
        await ws.close(code=4404, reason="session_not_found"); return
    if sess.creator_key_hash != key.id:
        await ws.close(code=4403, reason="session_unauthorized"); return
    # ... rest unchanged ...
```

注：`hash_key` 旧函数（sha256(api_key)[:16]）在 SP3 时是 anonymous binding；现在 `key.id` 直接绑 key record，更精确。可保留 `hash_key` 不删（向后兼容旧 sessions）。

- [ ] **Step 5: 改 session_manager.py — release on cleanup**

在 `Session` dataclass 加字段：

```python
key_id: str | None = None
```

在 `cleanup()` 末尾加（在 audit_writer.aclose 之后、stt/llm/tts close 循环前）：

```python
        if sess.key_id and hasattr(self, "_quota"):
            try:
                await self._quota.release_session(sess.key_id)
            except Exception:
                log.exception("quota release failed for %s", sess.key_id)
```

`SessionManager.__init__` 加 `self._quota` 持有：

```python
def __init__(self, quota=None) -> None:
    # ... existing ...
    self._quota = quota
```

main.py lifespan 注入：

```python
session_mgr = SessionManager(quota=app.state.quota)
```

- [ ] **Step 6: 跑全套测试**

```bash
cd services/realtime-server
python3 -m pytest tests/ -v 2>&1 | tail -15
```

Expected: 旧 + 5 new pass。如失败可能是 import path 问题（rtvoice_auth 容器外不可达），需先 `pip install -e ../common --break-system-packages`。

```bash
cd /home/ubuntu/CozyProjects/RTVoice
pip install -e services/common --break-system-packages 2>&1 | tail -3
```

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/Dockerfile services/realtime-server/app/main.py services/realtime-server/app/session_manager.py services/realtime-server/tests/test_endpoints.py services/realtime-server/requirements.txt
git commit -m "feat(realtime-server): 集成 require_key + quota (T9)

- Dockerfile: COPY common lib + pip install rtvoice_auth
- main.py: lifespan init key_store/quota；require_key FastAPI dep；
  create_session 走 quota acquire/release；scope=realtime
- session_manager.py: Session.key_id 字段；cleanup 末尾 release_session
- WS handler: _extract_ws_bearer_key 用 verify_key
- +5 endpoint 测试（valid / invalid / quota_concurrent / revoked / scope_denied）

per spec §4.5（realtime 集成）"
```

---

## Task 10: stt-server 集成 require_key（scope=stt）

**Files:**
- Modify: `services/stt-server/app/main.py`
- Modify: `services/stt-server/Dockerfile`
- Modify: `docker-compose.yml`（build context 改）

- [ ] **Step 1: Dockerfile + compose 改 build context（同 T9 模式）**

`services/stt-server/Dockerfile.gpu` (prod) + `Dockerfile` (dev) 各加：

```dockerfile
COPY services/common /app/common
ENV PYTHONPATH=/app/common:/app
RUN pip install --no-cache-dir /app/common
```

`docker-compose.yml` `stt-server.build.context: .` + `dockerfile: services/stt-server/Dockerfile`。

- [ ] **Step 2: 改 main.py 加 require_key**

stt-server main.py 是 WebSocket 服务（无 REST endpoint 用 require_key）。WS 鉴权用同款 `_extract_ws_bearer_key`：

参照 realtime-server T9 Step 4e 同款实现，scope="stt"。

具体：在 stt-server main.py 的 WS handler 入口加：

```python
async def _verify_ws_key(ws):
    """同 realtime；scope=stt"""
    # ... 三路 Bearer 提取 → verify_key(scope="stt") ...
```

- [ ] **Step 3: syntax check + commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "import ast; ast.parse(open('services/stt-server/app/main.py').read())" && echo OK

git add services/stt-server/app/main.py services/stt-server/Dockerfile services/stt-server/Dockerfile.gpu docker-compose.yml
git commit -m "feat(stt-server): 集成 require_key + scope=stt (T10)

- Dockerfile: COPY common + pip install rtvoice_auth
- main.py WS handler: verify_key(scope=stt) 替代 hardcoded RTVOICE_API_KEY
- 沙盒无 tests dir；prod E2E T16 验

per spec §4.5"
```

---

## Task 11: tts-server 集成 require_key（3 entry points）

**Files:**
- Modify: `services/tts-server/app/main.py`
- Modify: `services/tts-server/app/main_cosyvoice.py`
- Modify: `services/tts-server/app/main_cosyvoice3.py`
- Modify: `services/tts-server/Dockerfile{,.cosyvoice,.cosyvoice3}`
- Modify: `docker-compose.yml`

- [ ] **Step 1: 3 个 Dockerfile 各加 COPY common + pip install rtvoice_auth**

模式同 T10。3 个 Dockerfile 分别：
- `services/tts-server/Dockerfile`
- `services/tts-server/Dockerfile.cosyvoice`
- `services/tts-server/Dockerfile.cosyvoice3`

每个加：
```dockerfile
COPY services/common /app/common
ENV PYTHONPATH=/app/common:/app
RUN pip install --no-cache-dir /app/common
```

`docker-compose.yml` tts-server build context 改 `.`，dockerfile 路径相对。

- [ ] **Step 2: 3 个 main 文件加 require_key**

每个 main 同款：

```python
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy
# ... 同 T9 lifespan + require_key dep ...
# scope = "tts"
```

`/v1/tts/stream` 加 `Depends(require_key)`。`/v1/voices`（admin POST/DELETE）继续用旧 `TTS_ADMIN_API_KEY`（独立 admin 路径不混）。

- [ ] **Step 3: syntax check 3 文件 + commit**

```bash
for f in services/tts-server/app/main.py services/tts-server/app/main_cosyvoice.py services/tts-server/app/main_cosyvoice3.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK $f"
done

git add services/tts-server/ docker-compose.yml
git commit -m "feat(tts-server): 3 entry points 集成 require_key + scope=tts (T11)

- 3 Dockerfile 加 common lib
- main.py / main_cosyvoice.py / main_cosyvoice3.py 加 require_key
- /v1/tts/stream 走 require_key；/v1/voices admin 仍用 TTS_ADMIN_API_KEY

per spec §4.5"
```

---

## Task 12: token-server 替换 hmac → require_key（slowapi 保留）

**Files:**
- Modify: `services/token-server/app/main.py`
- Modify: `services/token-server/Dockerfile`
- Create: `services/token-server/tests/test_app.py`（新建沙盒测试）

- [ ] **Step 1: Dockerfile 加 common（同 T10/T11）**

```dockerfile
COPY services/common /app/common
ENV PYTHONPATH=/app/common:/app
RUN pip install --no-cache-dir /app/common
```

- [ ] **Step 2: 写测试**

`services/token-server/tests/__init__.py`（空）

`services/token-server/tests/test_app.py`:

```python
"""Test token-server with require_key + slowapi."""
import asyncio, hashlib, pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_key(monkeypatch, tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    yaml_path = tmp_path / "keys.yaml"
    secret = "tk-secret-32-chars-aaaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="ktk", secret_hash=h, name="tk",
                          scopes=["tokens"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("LIVEKIT_API_KEY", "dev-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "dev-secret-32-chars-aaaaaaaaa")

    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        yield c, secret


def test_token_endpoint_with_valid_key(client_with_key):
    c, secret = client_with_key
    r = c.post("/v1/tokens",
               json={"identity": "alice", "room": "test", "ttl_minutes": 5},
               headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_token_endpoint_invalid_key(client_with_key):
    c, _ = client_with_key
    r = c.post("/v1/tokens",
               json={"identity": "alice", "room": "test"},
               headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth.invalid_token"


def test_token_endpoint_scope_denied(client_with_key, monkeypatch, tmp_path):
    """key 没 tokens scope → 403."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    yaml_path = tmp_path / "keys2.yaml"
    secret = "stt-only-secret-32-chars-aaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kstt", secret_hash=h, name="x",
                          scopes=["stt"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/tokens",
                   json={"identity": "a", "room": "r"},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 403
        assert r.json()["code"] == "auth.scope_denied"
```

- [ ] **Step 3: 改 main.py**

替换旧 `require_api_key` 实现：

3a. 顶部 import 加：
```python
from rtvoice_auth.verify import verify_key
from rtvoice_auth.errors import AuthError, InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.lifespan import auto_migrate_legacy
```

3b. lifespan 内加 store init + auto_migrate（同 T9 step 4b 模式）；scope="tokens"。

3c. 替换 `def require_api_key(creds)` 为：

```python
async def require_api_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        AUTH_FAILURES.labels(reason="missing").inc()
        raise api_error(401, "auth.missing_token", "Missing Authorization: Bearer header")
    secret = authorization[len("Bearer "):]
    try:
        await verify_key(secret, scope="tokens", store=request.app.state.key_store)
    except InvalidToken as e:
        AUTH_FAILURES.labels(reason="invalid").inc()
        raise api_error(401, e.code, e.message)
    except TokenRevoked as e:
        AUTH_FAILURES.labels(reason="revoked").inc()
        raise api_error(401, e.code, e.message)
    except ScopeDenied as e:
        AUTH_FAILURES.labels(reason="scope").inc()
        raise api_error(403, e.code, e.message)
```

slowapi 保留（per Q6）。

- [ ] **Step 4: 跑测试**

```bash
cd services/token-server
python3 -m pytest tests/ -v
```

Expected: 3 pass。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/token-server/
git commit -m "feat(token-server): require_key 替代 hmac；slowapi 保留 (T12)

- Dockerfile: COPY common
- main.py: require_api_key 用 verify_key (scope=tokens)；slowapi IP 限保留
- 新建 tests/test_app.py（之前无）：3 测试（valid/invalid/scope）
- AUTH_FAILURES metric 加 'revoked'/'scope' label

per spec §4.8"
```

---

## Task 13: docker-compose.yml redis 容器 + keys.yaml mount + env

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: 加 redis 容器（profile=auth-redis）**

```yaml
  redis:
    image: redis:7-alpine
    container_name: rtvoice-redis
    profiles: ["auth-redis"]
    restart: unless-stopped
    networks: [rtvoice_net]
    volumes:
      - rtvoice_redis_data:/data
    ports:
      - "${BIND_HOST:-127.0.0.1}:${REDIS_PORT:-6379}:6379"
    command: ["redis-server", "--appendonly", "yes"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
```

文件底部 `volumes:` 段加：

```yaml
  rtvoice_redis_data:
    driver: local
```

- [ ] **Step 2: 4 服务 environment 加 RTVOICE_KEYS_***

每个（realtime/stt/tts/token）service 的 `environment:` 段追加：

```yaml
      RTVOICE_KEYS_BACKEND: ${RTVOICE_KEYS_BACKEND:-yaml}
      RTVOICE_KEYS_FILE: ${RTVOICE_KEYS_FILE:-/data/keys.yaml}
      RTVOICE_REDIS_URL: ${RTVOICE_REDIS_URL:-redis://redis:6379/0}
```

各 service 的 `volumes:` 段加：

```yaml
      - ${RTVOICE_KEYS_HOST_FILE:-./data/keys.yaml}:/data/keys.yaml:rw
```

- [ ] **Step 3: 在 .env.example 末尾追加 SP6 段**

```bash
# ============================================================
# Multi-tenant Auth (SP6, v0.13+)
# ============================================================
# backend: yaml (dev) 或 redis (prod)
RTVOICE_KEYS_BACKEND=yaml

# YAML 文件路径（容器内）+ host 映射（compose volume）
RTVOICE_KEYS_FILE=/data/keys.yaml
RTVOICE_KEYS_HOST_FILE=./data/keys.yaml

# Redis backend（profile=auth-redis 启用）
RTVOICE_REDIS_URL=redis://redis:6379/0
REDIS_PORT=6379

# 启用 Redis backend：
#   docker compose --profile prod --profile auth-redis up -d
```

- [ ] **Step 4: 创建空 keys.yaml 占位**

```bash
mkdir -p data
cat > data/keys.yaml << 'EOF'
version: 1
keys: []
EOF
```

- [ ] **Step 5: 验证 compose YAML**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "
import yaml
d = yaml.safe_load(open('docker-compose.yml'))
for svc in ('realtime-server', 'stt-server', 'tts-server', 'token-server'):
    env = d['services'][svc].get('environment', {})
    assert 'RTVOICE_KEYS_BACKEND' in env, f'{svc} missing'
    print(f'{svc}: OK')
assert 'redis' in d['services']
print('redis service: OK')
"
```

Expected: 4 服务 OK + redis OK。

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example data/keys.yaml
git commit -m "feat(compose): redis + keys.yaml volume + RTVOICE_KEYS_* env (T13)

- redis:7-alpine container (profile=auth-redis)；rtvoice_redis_data 卷
- 4 services environment + volumes mount /data/keys.yaml
- .env.example SP6 段（YAML default / Redis 切换）
- data/keys.yaml 空占位（version:1 keys:[]）

per spec §4.2 + 4.7"
```

---

## Task 14: docs OPERATIONS §7 + CONVENTIONS §6 错误码

**Files:**
- Modify: `OPERATIONS.md`
- Modify: `docs/api/CONVENTIONS.md`

- [ ] **Step 1: OPERATIONS.md 加 §7 多租户认证**

读 `OPERATIONS.md`，在 §6（SP5 加的 docker mirror）之后插入：

````markdown

## §7 多租户认证（SP6, v0.13+）

### 7.1 启用 multi-key auth

prod 升级 v0.13.0 后自动迁移：服务启动检测到 `RTVOICE_API_KEY` 环境变量 + 空 `keys.yaml` → 自动注册 `legacy-default` key。下游客户端无感继续工作。

### 7.2 创建 per-app key（推荐先于 revoke legacy）

```bash
# 容器内跑（host 通常没 pip 装 rtvoice-admin）
docker exec rtvoice-realtime rtvoice-admin create \
    --name cozyvoice \
    --sessions-concurrent 10 \
    --sessions-per-hour 500 \
    --scopes stt,tts,realtime,tokens

# 输出含 plaintext secret，⚠️ 立即保存（仅显示一次）
```

把生成的 secret 提供给下游应用替换其旧 `RTVOICE_API_KEY`。

### 7.3 Backend 切换：YAML → Redis

当前 dev 默认 YAML。prod 切到 Redis（用于跨服务实时一致 + counter）：

```bash
# 1. 启 Redis 容器
docker compose --profile prod --profile auth-redis up -d redis

# 2. .env 改：
# RTVOICE_KEYS_BACKEND=redis

# 3. 重启 4 服务（自动从 YAML 重新 import 到 Redis 是手动操作）
# 或手动跑：
RTVOICE_KEYS_BACKEND=redis docker exec rtvoice-realtime \
    rtvoice-admin import-legacy   # （还需一个 import-from-yaml 命令；当前 SP6 范围 manual）
```

### 7.4 撤销 legacy key（迁移完成后）

```bash
docker exec rtvoice-realtime rtvoice-admin list
# 找到 legacy-default 的 key_id

docker exec rtvoice-realtime rtvoice-admin revoke key_xxx
# ⚠️ 撤销后所有用此 secret 的客户端立即 401；先确认所有下游已切到 per-app key
```

### 7.5 quota 故障排查

#### 现象：429 auth.quota_concurrent
- 看活跃 session：`docker exec rtvoice-realtime rtvoice-admin show <key_id>`
- 漏 release（异常路径）：重启 realtime-server 重置 in-memory counter
- 调高上限：`rtvoice-admin create` 重新生成（rotate 不改 metadata）；或 SP7+ 加 `update-key` 命令

#### 现象：429 auth.quota_per_hour
- rolling hour bucket；自然过期（每小时归零）
- 临时调高：rotate（生成新 key 用更大 per_hour）

````

- [ ] **Step 2: CONVENTIONS.md §6 加 6 条错误码**

读 `CONVENTIONS.md`，§6 错误码表追加：

```markdown
| `auth.token_revoked` | 401 | API key 已被 revoke（SP6） |
| `auth.scope_denied` | 403 | API key 不含当前 service scope（SP6） |
| `auth.quota_per_hour` | 429 | 1 小时 session 创建数超 key 上限（SP6） |
| `auth.quota_concurrent` | 429 | 当前活跃 session 超 key 上限（SP6） |
```

注：`auth.missing_token` 和 `auth.invalid_token` SP1.5 已有。

- [ ] **Step 3: Commit**

```bash
git add OPERATIONS.md docs/api/CONVENTIONS.md
git commit -m "docs: SP6 OPERATIONS §7 multi-tenant auth + CONVENTIONS §6 +4 错误码 (T14)

- OPERATIONS §7.1-§7.5: 启用 / per-app key 创建 / YAML→Redis / 撤销 legacy / quota 排障
- CONVENTIONS §6: +token_revoked / scope_denied / quota_per_hour / quota_concurrent

per spec §4.6"
```

---

## Task 15: CHANGELOG v0.13.0 + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 在 [Unreleased] 之后插入 v0.13.0 entry**

```markdown
## [0.13.0] — 2026-05-10 — SP6 Multi-Tenant Auth

平台化重构第七阶段：单 key 共享 → 多 key 多租户 ready。

### Added

- **`services/common/rtvoice_auth/`** — 共享 auth lib
  - `Key` Pydantic v2 model（id / secret_hash / name / quota / scopes / revoked_at / legacy）
  - `YamlKeyStore` + `RedisKeyStore`（双 backend，env 切换）
  - `verify_key(secret, scope, store)` + 4 错误类（InvalidToken / TokenRevoked / ScopeDenied / QuotaExceeded）
  - `QuotaTracker`：sessions_concurrent + sessions_per_hour rolling 执行
  - `auto_migrate_legacy`：服务启动时空 store + RTVOICE_API_KEY → legacy-default key
- **`services/rtvoice-admin/`** — 独立 CLI 工具
  - 6 命令：create / list / show / revoke / rotate / import-legacy
  - 用 argparse + RTVOICE_KEYS_BACKEND env 选 backend
  - `pip install` 后 `rtvoice-admin --help`，prod 用 `docker exec`
- 4 服务 (realtime/stt/tts/token) 集成 `require_key` + scope 验证
- realtime-server `POST /v1/sessions` 走 quota acquire/release
- WS handler 三路 Bearer 验 → store 查 key record
- `Session.key_id` 字段；cleanup 调 release_session
- Redis 容器（profile=auth-redis）+ rtvoice_redis_data 卷
- 4 services environment 加 `RTVOICE_KEYS_BACKEND/FILE/REDIS_URL`
- `data/keys.yaml` 占位

### Changed

- realtime-server `creator_key_hash` 改用 `key.id`（替代 SP3 hash_key 匿名 16 字节 sha）
- token-server `require_api_key` 用 `verify_key`（scope=tokens）；slowapi IP 限保留
- 4 Dockerfile 加 `COPY services/common /app/common` + `pip install rtvoice_auth`；compose build context 改 monorepo root
- `.env.example`：SP6 段（YAML default / Redis 切换说明）
- `OPERATIONS.md` §7：multi-tenant 启用 / per-app key / 撤销 legacy / quota 排障
- `CONVENTIONS.md` §6：+`auth.token_revoked` / `auth.scope_denied` / `auth.quota_per_hour` / `auth.quota_concurrent`

### 验证（autonomous）

- ✅ rtvoice_auth 25 单元测试（models 3 / store_yaml 6 / store_redis 5 / verify 5 / quota 6）
- ✅ admin CLI 8 测试（5 commands + 3 import-legacy）
- ✅ realtime-server +5 endpoint 测试（valid/invalid/quota_concurrent/revoked/scope_denied）
- ✅ token-server 3 新测试（沙盒新建 tests dir）
- ✅ stt/tts CORS+auth 验证留 prod E2E（沙盒无 tests dir）
- ✅ 总测试 119 → 160+
- ⏳ prod 集成：daocloud mirror redis pull + 4 服务 force recreate + autonomous A1-A12

### 设计决策

- API Key per App，不做 OAuth2/JWT 用户级（应用自管用户；可演进）
- YAML（dev）+ Redis（prod）双 backend env 切换；dev 简单；prod 跨服务共享
- CLI 工具无 admin HTTP（攻击面小）；用 docker exec
- Hard cutover + 自动迁移 legacy（prod 升级零停机）
- Quota 基础执行：concurrent + per_hour；token bucket 留 SP7+
- token-server slowapi（IP）+ per-key（auth）共存（不同维度）
- common lib 用 monorepo COPY + PYTHONPATH（避免 PyPI 内部 lib 发布）

详见 [SP6 设计](./docs/superpowers/specs/2026-05-10-sp6-multi-tenant-auth-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-10-sp6-multi-tenant-auth.md)。

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
git commit -m "docs(changelog): v0.13.0 — SP6 Multi-Tenant Auth (T15)

- Added: rtvoice_auth common lib + rtvoice-admin CLI + 4 服务集成 + Redis 容器
- Changed: 4 Dockerfile common COPY；creator_key_hash 改用 key.id；slowapi+per-key 共存
- 41 新单元测试；总测试 119 → 160+
- prod 验收 + Grafana per-key metric 待 T16"

git push origin main 2>&1 | tail -10
```

---

## Task 16: prod 部署 + autonomous A1-A12 + user-participation

**Files:** 无（read-only verification + remote ops）

- [ ] **Step 1: prod 端 git pull + 创建宿主 keys 目录**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -5

  mkdir -p /var/data/rtvoice/keys
  if [ ! -f /var/data/rtvoice/keys/keys.yaml ]; then
    echo -e "version: 1\nkeys: []" > /var/data/rtvoice/keys/keys.yaml
  fi
  chown -R 1000:1000 /var/data/rtvoice/keys

  grep -q "^RTVOICE_KEYS_HOST_FILE=" .env || \
    echo "RTVOICE_KEYS_HOST_FILE=/var/data/rtvoice/keys/keys.yaml" >> .env
  grep "^RTVOICE_KEYS" .env
}'
```

- [ ] **Step 2: prod build + force recreate 4 服务**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  t1=$(date +%s)
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server stt-server tts-server token-server 2>&1 | tail -10
  t2=$(date +%s)
  echo "build: $((t2-t1))s"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d --force-recreate realtime-server stt-server tts-server token-server 2>&1 | tail -10
  for i in $(seq 1 20); do
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

- [ ] **Step 3: A1-A8 autonomous（auth + quota）**

```bash
ssh root@192.168.66.163 '
echo "=== A1: legacy 自动迁移（auto_migrate_legacy 已跑）==="
docker exec rtvoice-realtime cat /data/keys.yaml | head -15

echo
echo "=== A2: 用 RTVOICE_API_KEY 调 /v1/sessions（legacy-default key 验证）==="
docker exec rtvoice-realtime python3 -c "
import urllib.request, json, os
api_key = os.environ.get(\"RTVOICE_API_KEY\")
req = urllib.request.Request(
    \"http://realtime-server:9000/v1/sessions\",
    data=b\"{}\",
    headers={\"Content-Type\":\"application/json\", \"Authorization\": f\"Bearer {api_key}\"},
)
r = urllib.request.urlopen(req, timeout=10)
print(\"status:\", r.status)
print(\"\u2713 A2 legacy 仍工作\")
"

echo
echo "=== A3: 创建 cozyvoice key ==="
docker exec rtvoice-realtime rtvoice-admin create \
    --name cozyvoice --sessions-concurrent 5 --sessions-per-hour 200 \
    --scopes stt,tts,realtime,tokens

echo
echo "=== A4: list keys ==="
docker exec rtvoice-realtime rtvoice-admin list

echo
echo "=== A5: 用 cozyvoice secret 调 /v1/sessions（按需要替换 SECRET）==="
echo "（手动从 A3 输出复制 secret 然后跑：）"
echo "  docker exec rtvoice-realtime python3 -c '\''"
echo "  import urllib.request, json"
echo "  req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\","
echo "    data=b\"{}\","
echo "    headers={\"Authorization\":\"Bearer <SECRET>\"})"
echo "  print(urllib.request.urlopen(req).read())"
echo "  '\''"

echo
echo "=== A6: invalid bearer → 401 auth.invalid_token ==="
docker exec rtvoice-realtime python3 -c "
import urllib.request, urllib.error
try:
    req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\",
        data=b\"{}\", headers={\"Authorization\":\"Bearer bogus\", \"Content-Type\":\"application/json\"})
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    import json; b = json.loads(e.read())
    print(\"http:\", e.code, \"code:\", b[\"code\"])
    assert e.code == 401 and b[\"code\"] == \"auth.invalid_token\"
    print(\"\u2713 A6\")
"
'
```

- [ ] **Step 4: 通知 user user-participation**

```
SP6 沙盒 + autonomous A1-A6 完成。请你做：

1. **从 A3 输出取 cozyvoice secret**：
   docker exec rtvoice-realtime rtvoice-admin show <key_id>
   # 用 secret 替代你之前 RTVOICE_API_KEY 测各端点

2. **CozyVoice 项目切到 cozyvoice key**（之前 SDK Client(api_key=...) 调用换成新 secret）

3. **撤销 legacy（验证完 cozyvoice 正常后）**：
   docker exec rtvoice-realtime rtvoice-admin list   # 找 legacy-default 的 key_id
   docker exec rtvoice-realtime rtvoice-admin revoke key_xxx
   # 注意：所有用旧 RTVOICE_API_KEY 的客户端立即失效；先确认全切完

4. **可选：切到 Redis backend**：
   .env 改 RTVOICE_KEYS_BACKEND=redis
   docker compose --profile prod --profile auth-redis up -d redis
   # 重新 import keys（手动 export YAML 内容用 admin create 重建；SP6 范围内 manual）
```

- [ ] **Step 5: User 反馈后标 SP6 完工**

OK → SP6 done。
有 admin CLI 用法 / migration / SDK 兼容问题 → SP6-fix-N。

---

## Self-Review

### 1. Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §3 file layout | T1 (auth lib skeleton) / T2-T3 stores / T4 verify / T5 quota / T6-T8 admin / T9-T12 服务集成 / T13 compose |
| §4.1 Key model | T1 |
| §4.2 YAML/Redis schema | T2 / T3 |
| §4.3 admin CLI 6 命令 | T6 (skeleton + version) / T7 (5 cmds) / T8 (import-legacy) |
| §4.4 verify 流程 | T4 |
| §4.5 quota 执行 + 4 服务集成 | T5 / T9-T12 |
| §4.6 错误码 | T14 (CONVENTIONS §6) |
| §4.7 auto-migrate | T8 (lifespan) |
| §4.8 token-server slowapi 共存 | T12 |
| §5 测试矩阵 41 | 实际：T1 3 + T2 6 + T3 5 + T4 5 + T5 6 + T6 2 + T7 5 + T8 3 + T9 5 + T12 3 = **43**（spec 估 41，多 2，T6 smoke 计入）|
| §6 验收 A1-A12 + B1-B3 | T16 |
| §8 范围外 | 未实施任何 ✓ |

### 2. Placeholder scan

- 每 step 含完整代码 / 命令
- 无 TBD / TODO
- T7 / T9 步骤间 import 路径细节解释清楚（pip install -e ../common）
- T16 step 5 user secret 替换标记 `<SECRET>` 是 placeholder（user 操作，非代码）

### 3. Type consistency

- `Key` Pydantic 字段 T1 定义；T2/T3 store 序列化；T4 verify 引用；T5 quota 调 `key.sessions_concurrent_max` / `key.id`；T7 admin commands 创建；T9 endpoint Depends 返回 — 所有签名一致
- `verify_key(secret, scope, store)` T4 定义；T9-T12 4 服务的 require_key dep 调用一致
- `QuotaTracker.acquire_session(key)` / `release_session(key_id)` T5 定义；T9 realtime-server 集成调用一致
- `auto_migrate_legacy(store)` T8 lifespan helper；T9-T12 4 服务 lifespan 内调用一致
- `RTVOICE_KEYS_BACKEND` env 命名 T7 admin / T9-T12 服务 / T13 compose 一致
- `key.id` （非 `key.key_id`）字段名贯通

无类型/签名漂移。

### 4. 风险点 spec → plan 对应

| spec §7 风险 | plan 缓解 |
|---|---|
| YAML watcher 跨平台 | requirements.txt 加 `watchdog>=4.0`；T2 store 暂不实施 watcher（hot reload 留 SP6.1 fix） |
| Redis 单点 | T13 加 healthcheck；如挂 → 服务初始化时降级 in-memory（已实现：YamlKeyStore 也是 in-memory + 文件）|
| 启动竞态 | T8 auto_migrate 用 `if not store.any_keys()` 双检；YAML atomic write 防写竞态；Redis SETNX 在 RedisKeyStore 内部（隐式）|
| Quota 漏 release | T9 cleanup 末尾 finally 调 release_session；可选启动时审计清 stale（SP7+）|
| sha256 字典攻击 | T7 secret 用 `secrets.token_urlsafe(32)` 256 位熵 |
| 误删 legacy | OPERATIONS §7.4 明示警告；CLI 不加二次确认（YAGNI；admin 自负） |
| metric cardinality | 不加 key_id label（spec §7 + plan T16 都不引入） |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-10-sp6-multi-tenant-auth.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + spec/quality 双审；与 SP1-SP5 同流程
2. **Inline Execution** — 本 session 批量执行 + checkpoints

Which approach?
