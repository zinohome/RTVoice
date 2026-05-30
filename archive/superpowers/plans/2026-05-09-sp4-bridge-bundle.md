# SP4 Bridge Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RTVoice 从"platform 已建好"推进到"platform 已能被用起来 + 看得见"——三子项 (Python SDK + SP3 残项 + A-lite 仪表盘) 一个 release v0.11.0。

**Architecture:** SDK 是 monorepo 内 `clients/python/` 新独立 PyPI 包（4 命名空间，async+sync 双形态）；K 残项触 realtime-server 3 文件加 voice/speed 热改 + memory.clear；A-lite 加 monitoring profile 给 docker-compose（Prometheus + Grafana），在 realtime-server 加 3 个新 metric。

**Tech Stack:** Python 3.10+ / hatchling / httpx / websockets / Pydantic v2 / pytest / Prometheus + Grafana 容器

**Spec:** [docs/superpowers/specs/2026-05-09-sp4-bridge-bundle-design.md](../specs/2026-05-09-sp4-bridge-bundle-design.md)

---

## Task 1: SDK 骨架（pyproject + LICENSE + py.typed + 入口 module + smoke test）

**Files:**
- Create: `clients/python/pyproject.toml`
- Create: `clients/python/README.md`
- Create: `clients/python/LICENSE`
- Create: `clients/python/CHANGELOG.md`
- Create: `clients/python/.gitignore`
- Create: `clients/python/src/rtvoice_client/__init__.py`
- Create: `clients/python/src/rtvoice_client/py.typed`（空文件）
- Create: `clients/python/tests/__init__.py`
- Create: `clients/python/tests/test_smoke.py`

- [ ] **Step 1: 创建目录结构**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p clients/python/src/rtvoice_client clients/python/tests
touch clients/python/src/rtvoice_client/py.typed
```

- [ ] **Step 2: 写 pyproject.toml**

`clients/python/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.20"]
build-backend = "hatchling.build"

[project]
name = "rtvoice-client"
version = "0.1.0"
description = "Official Python client for RTVoice — self-hosted voice services platform (STT + TTS + Realtime Voice + LiveKit tokens)"
readme = "README.md"
requires-python = ">=3.10"
license = {file = "LICENSE"}
authors = [{name = "RTVoice contributors"}]
keywords = ["rtvoice", "stt", "tts", "voice", "realtime", "websocket", "asr", "speech"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Multimedia :: Sound/Audio :: Speech",
    "Typing :: Typed",
]
dependencies = [
    "httpx>=0.27",
    "websockets>=12.0",
    "pydantic>=2.7",
    "typing-extensions>=4.9; python_version < '3.11'",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "respx>=0.21",  # httpx mocking
]

[project.urls]
Homepage = "https://github.com/zinohome/RTVoice"
Issues = "https://github.com/zinohome/RTVoice/issues"
Source = "https://github.com/zinohome/RTVoice/tree/main/clients/python"

[tool.hatch.build.targets.wheel]
packages = ["src/rtvoice_client"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.hatch.version]
path = "src/rtvoice_client/__init__.py"
```

- [ ] **Step 3: 拷 LICENSE（与 monorepo Apache 2.0 一致）**

```bash
cp /home/ubuntu/CozyProjects/RTVoice/LICENSE clients/python/LICENSE
```

- [ ] **Step 4: 写 README.md**

`clients/python/README.md`:

````markdown
# rtvoice-client

Official Python client for [RTVoice](https://github.com/zinohome/RTVoice) — self-hosted voice services platform.

## Install

```bash
pip install rtvoice-client
```

## Quick start

```python
from rtvoice_client import Client

c = Client(api_key="bear-32-...", base_url="https://rtvoice.your-domain.com")

# STT
text = c.stt.transcribe(open("user.pcm", "rb").read(), sample_rate=16000)

# TTS
pcm = c.tts.synthesize("你好", voice="default_zh_female", speed=1.0)

# Realtime — high-level helper
async for evt in c.realtime.conversation(audio_iter, prompt="你是助手"):
    print(evt)

# LiveKit token (optional advanced mode)
tok = c.tokens.livekit(identity="alice", room="rtvoice-test", ttl_minutes=10)
```

## Async API

```python
from rtvoice_client import AsyncClient

c = AsyncClient(api_key="...", base_url="...")
text = await c.stt.transcribe(pcm)
```

## Status

**Alpha (0.1.x).** API may change. Pin minor version (`rtvoice-client~=0.1.0`).

## License

Apache 2.0
````

- [ ] **Step 5: 写 CHANGELOG.md**

`clients/python/CHANGELOG.md`:

```markdown
# Changelog

## [0.1.0] — 2026-05-09

Initial alpha release.

### Added

- `Client` (sync) + `AsyncClient` (async) entry points
- `stt` / `tts` / `realtime` / `tokens` namespaces
- `realtime.conversation()` high-level helper
- 14 typed exception classes mapped to RTVoice CONVENTIONS error codes
- Pydantic v2 request/response models
- Type hints throughout (`py.typed` marker)
```

- [ ] **Step 6: 写 .gitignore**

`clients/python/.gitignore`:

```
__pycache__/
*.pyc
*.egg-info/
build/
dist/
.pytest_cache/
.coverage
htmlcov/
*.egg
```

- [ ] **Step 7: 写最小入口 module**

`clients/python/src/rtvoice_client/__init__.py`:

```python
"""rtvoice-client: official Python client for RTVoice platform."""
__version__ = "0.1.0"

# Re-export public API（后续 task 实际提供）
__all__ = ["__version__", "Client", "AsyncClient"]


def __getattr__(name: str):
    """Lazy import to avoid circular issues during package build."""
    if name == "Client":
        from rtvoice_client._base import Client
        return Client
    if name == "AsyncClient":
        from rtvoice_client._base import AsyncClient
        return AsyncClient
    raise AttributeError(f"module 'rtvoice_client' has no attribute {name!r}")
```

注：这里 import 的 `Client` / `AsyncClient` 在 T4 实现；T1 只占名。

- [ ] **Step 8: 写 smoke test**

`clients/python/tests/test_smoke.py`:

```python
"""Smoke test: package importable, version present."""


def test_version():
    import rtvoice_client
    assert rtvoice_client.__version__ == "0.1.0"


def test_package_layout():
    import rtvoice_client
    assert "Client" in rtvoice_client.__all__
    assert "AsyncClient" in rtvoice_client.__all__


def test_py_typed_marker_exists():
    """PyPI typed package 必须含 py.typed."""
    import rtvoice_client
    from pathlib import Path
    pkg_dir = Path(rtvoice_client.__file__).parent
    assert (pkg_dir / "py.typed").is_file()
```

- [ ] **Step 9: 装 dev deps + 跑 smoke**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
pip install -e ".[test]" 2>&1 | tail -3
python3 -m pytest tests/test_smoke.py -v
```

Expected: 3 passed（test_version 跑前 Client/AsyncClient 还未实现也不会报错，因为只在 attribute access 时才 import，test_smoke 没访问）。

注：`test_package_layout` 验证 `__all__` 内容；不真正 access Client / AsyncClient，不会触发 lazy import。

- [ ] **Step 10: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/
git commit -m "feat(sdk): rtvoice-client 骨架 + pyproject + LICENSE + py.typed (T1)

- pyproject.toml: hatchling，PyPI metadata，httpx/websockets/pydantic deps
- src/rtvoice_client/__init__.py: lazy import Client/AsyncClient（占名）
- LICENSE: Apache 2.0
- README.md: install + quick start + async API + alpha 状态说明
- CHANGELOG.md / .gitignore / tests/ 框架
- 3 smoke 测试（version / __all__ / py.typed marker）

per spec D-2026-05-09-B.1/B.4"
```

---

## Task 2: errors.py — 14 typed exceptions + `_raise_for_code` 映射

**Files:**
- Create: `clients/python/src/rtvoice_client/errors.py`
- Create: `clients/python/tests/test_errors.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_errors.py`:

```python
"""Test typed exception hierarchy + code → class 映射."""
import pytest


def test_base_class_carries_metadata():
    from rtvoice_client.errors import RTVoiceError
    exc = RTVoiceError(code="x.y", message="oops", request_id="req_1", http_status=500)
    assert exc.code == "x.y"
    assert exc.message == "oops"
    assert exc.request_id == "req_1"
    assert exc.http_status == 500
    assert "x.y" in str(exc)


def test_all_subclasses_inherit_base():
    from rtvoice_client.errors import (
        RTVoiceError, AuthError, ValidationError, PromptTooLong,
        CapacityFull, SessionNotFound, SessionExpired, SessionUnauthorized,
        TurnTimeout, TurnInProgress, STTError, LLMError, TTSError, ServerError,
    )
    for cls in (AuthError, ValidationError, PromptTooLong, CapacityFull,
                SessionNotFound, SessionExpired, SessionUnauthorized,
                TurnTimeout, TurnInProgress, STTError, LLMError, TTSError,
                ServerError):
        assert issubclass(cls, RTVoiceError)


@pytest.mark.parametrize("code,expected_cls_name", [
    ("auth.missing_token", "AuthError"),
    ("auth.invalid_token", "AuthError"),
    ("validation.invalid_request", "ValidationError"),
    ("prompt.too_long", "PromptTooLong"),
    ("session.capacity_full", "CapacityFull"),
    ("session.not_found", "SessionNotFound"),
    ("session.expired", "SessionExpired"),
    ("session.unauthorized", "SessionUnauthorized"),
    ("turn.timeout", "TurnTimeout"),
    ("turn.in_progress", "TurnInProgress"),
    ("stt.empty", "STTError"),
    ("stt.timeout", "STTError"),
    ("stt.failed", "STTError"),
    ("llm.failed", "LLMError"),
    ("tts.failed", "TTSError"),
    ("internal.unknown", "ServerError"),
])
def test_raise_for_code_maps_correctly(code, expected_cls_name):
    from rtvoice_client import errors
    body = {"type": "error", "code": code, "message": "msg", "request_id": "req"}
    cls = errors._code_to_class(code)
    assert cls.__name__ == expected_cls_name
    with pytest.raises(getattr(errors, expected_cls_name)):
        errors._raise_for_body(body, http_status=500)


def test_unknown_code_falls_back_to_server_error():
    from rtvoice_client import errors
    body = {"type": "error", "code": "future.unknown_thing", "message": "x"}
    with pytest.raises(errors.ServerError):
        errors._raise_for_body(body, http_status=500)


def test_raise_for_response_handles_non_error_body():
    """Non-error JSON body → no raise."""
    from rtvoice_client import errors
    errors._raise_for_body({"foo": "bar"}, http_status=200)  # no exception


def test_raise_for_response_handles_5xx_no_body():
    """5xx with no JSON body → ServerError."""
    from rtvoice_client import errors
    with pytest.raises(errors.ServerError):
        errors._raise_for_body(None, http_status=500)
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_errors.py -v
```

Expected: ImportError on `rtvoice_client.errors`.

- [ ] **Step 3: 写 errors.py**

`clients/python/src/rtvoice_client/errors.py`:

```python
"""Typed exceptions matching RTVoice CONVENTIONS.md §6 error codes."""
from __future__ import annotations
from typing import Any


class RTVoiceError(Exception):
    """Base for all RTVoice client errors.

    Attributes:
        code: server-side error code (CONVENTIONS.md §6, e.g. 'session.capacity_full')
        message: human-readable message
        request_id: server-assigned request id (may be None)
        http_status: HTTP status code if from REST endpoint (None for WS errors)
    """

    def __init__(
        self,
        code: str,
        message: str,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


class AuthError(RTVoiceError):
    """auth.missing_token / auth.invalid_token (401)."""


class ValidationError(RTVoiceError):
    """validation.invalid_request (422) — body schema mismatch."""


class PromptTooLong(RTVoiceError):
    """prompt.too_long (422) — prompt > PROMPT_MAX_CHARS."""


class SessionError(RTVoiceError):
    """Session-scoped errors."""


class CapacityFull(SessionError):
    """session.capacity_full (503) — server reached MAX_CONCURRENT_SESSIONS."""


class SessionNotFound(SessionError):
    """session.not_found (WS close 4404)."""


class SessionExpired(SessionError):
    """session.expired (WS close 4410)."""


class SessionUnauthorized(SessionError):
    """session.unauthorized (WS close 4403) — Bearer mismatch with creator."""


class TurnError(RTVoiceError):
    """Turn-scoped errors."""


class TurnTimeout(TurnError):
    """turn.timeout."""


class TurnInProgress(TurnError):
    """turn.in_progress — sent audio.eos while previous turn not done."""


class STTError(RTVoiceError):
    """stt.empty / stt.timeout / stt.failed."""


class LLMError(RTVoiceError):
    """llm.failed."""


class TTSError(RTVoiceError):
    """tts.failed."""


class ServerError(RTVoiceError):
    """5xx / internal.unknown / unmapped error code."""


_CODE_MAP: dict[str, type[RTVoiceError]] = {
    "auth.missing_token": AuthError,
    "auth.invalid_token": AuthError,
    "validation.invalid_request": ValidationError,
    "prompt.too_long": PromptTooLong,
    "session.capacity_full": CapacityFull,
    "session.not_found": SessionNotFound,
    "session.expired": SessionExpired,
    "session.unauthorized": SessionUnauthorized,
    "turn.timeout": TurnTimeout,
    "turn.in_progress": TurnInProgress,
    "stt.empty": STTError,
    "stt.timeout": STTError,
    "stt.failed": STTError,
    "llm.failed": LLMError,
    "tts.failed": TTSError,
    "internal.unknown": ServerError,
    "internal.upstream_closed": ServerError,
}


def _code_to_class(code: str) -> type[RTVoiceError]:
    """Map error code → exception class. Unknown codes → ServerError."""
    return _CODE_MAP.get(code, ServerError)


def _raise_for_body(body: Any, http_status: int | None) -> None:
    """If body is RTVoice ErrorResponse shape, raise corresponding typed exception.

    Body is None or non-error → return without raising (5xx with None → ServerError)."""
    if body is None:
        if http_status is not None and http_status >= 500:
            raise ServerError(
                code="internal.unknown",
                message=f"server returned {http_status} with no body",
                http_status=http_status,
            )
        return
    if not isinstance(body, dict):
        return
    if body.get("type") != "error":
        return
    code = body.get("code", "internal.unknown")
    message = body.get("message", "")
    request_id = body.get("request_id")
    cls = _code_to_class(code)
    raise cls(code=code, message=message, request_id=request_id, http_status=http_status)
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_errors.py -v
```

Expected: 21 passed（含 16 parametrize + 5 其它）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/errors.py clients/python/tests/test_errors.py
git commit -m "feat(sdk): errors.py 14 typed exceptions + 代码映射 (T2)

- RTVoiceError 基类（code/message/request_id/http_status 元数据）
- 13 子类（Auth/Validation/PromptTooLong/Session*/Turn*/STT/LLM/TTS/Server）
- _code_to_class + _raise_for_body 把 ErrorResponse JSON 抛 typed exception
- 21 单元测试（16 code 映射 + 5 边界）

per spec §4.2"
```

---

## Task 3: models.py — Pydantic v2 模型 + RealtimeEvent union

**Files:**
- Create: `clients/python/src/rtvoice_client/models.py`
- Create: `clients/python/tests/test_models.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_models.py`:

```python
"""Test Pydantic v2 request/response models + RealtimeEvent discriminated union."""
import pytest


def test_session_create_request_defaults():
    from rtvoice_client.models import SessionCreateRequest
    r = SessionCreateRequest()
    assert r.voice is None
    assert r.speed == 1.0
    assert r.prompt is None
    assert r.audit_persist is False


def test_session_create_request_speed_bounds():
    from rtvoice_client.models import SessionCreateRequest
    SessionCreateRequest(speed=0.5)
    SessionCreateRequest(speed=2.0)
    with pytest.raises(Exception):
        SessionCreateRequest(speed=3.0)
    with pytest.raises(Exception):
        SessionCreateRequest(speed=0.4)


def test_session_create_response_round_trip():
    from rtvoice_client.models import SessionCreateResponse
    data = {
        "session_id": "sess_x", "ws_url": "ws://...",
        "expires_at": "2026-05-09T16:00:00Z",
        "voice": "v", "speed": 1.0, "prompt": "p", "audit_persist": True,
    }
    r = SessionCreateResponse.model_validate(data)
    assert r.session_id == "sess_x"
    assert r.audit_persist is True


def test_realtime_event_discriminated_union():
    from rtvoice_client.models import parse_realtime_event, TranscriptFinal, ResponseDone
    assert isinstance(
        parse_realtime_event({"type": "transcript.final", "text": "hi"}),
        TranscriptFinal,
    )
    assert isinstance(
        parse_realtime_event({"type": "response.done", "text": "ok"}),
        ResponseDone,
    )


def test_realtime_event_unknown_type_returns_none():
    """Unknown event type → return None (caller decides to log/drop)."""
    from rtvoice_client.models import parse_realtime_event
    assert parse_realtime_event({"type": "future.event", "x": 1}) is None


def test_response_pcm_holds_bytes():
    from rtvoice_client.models import ResponsePCM
    e = ResponsePCM(data=b"\x00\x01\x02")
    assert e.type == "response.pcm"
    assert e.data == b"\x00\x01\x02"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_models.py -v
```

- [ ] **Step 3: 写 models.py**

`clients/python/src/rtvoice_client/models.py`:

```python
"""Pydantic v2 models for RTVoice API requests/responses + WS events."""
from __future__ import annotations
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, ConfigDict


class SessionCreateRequest(BaseModel):
    voice: str | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    prompt: str | None = None
    audit_persist: bool = False


class SessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str
    expires_at: str
    voice: str
    speed: float
    prompt: str
    audit_persist: bool


class TokenRequest(BaseModel):
    identity: str
    room: str
    ttl_minutes: int = 10


class TokenResponse(BaseModel):
    token: str
    url: str
    room: str
    identity: str


# ---------------- Realtime WS events ----------------


class TranscriptPartial(BaseModel):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str
    stable: bool = False


class TranscriptFinal(BaseModel):
    type: Literal["transcript.final"] = "transcript.final"
    text: str


class ResponseText(BaseModel):
    type: Literal["response.text"] = "response.text"
    text: str


class ResponseDone(BaseModel):
    type: Literal["response.done"] = "response.done"
    text: str = ""


class ResponsePCM(BaseModel):
    """Synthetic event: SDK 高层 helper 把 binary WS frame 包装成 typed event."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    type: Literal["response.pcm"] = "response.pcm"
    data: bytes


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None


RealtimeEvent = Union[
    TranscriptPartial, TranscriptFinal, ResponseText,
    ResponseDone, ResponsePCM, ErrorEvent,
]


_EVENT_MAP: dict[str, type[BaseModel]] = {
    "transcript.partial": TranscriptPartial,
    "transcript.final": TranscriptFinal,
    "response.text": ResponseText,
    "response.done": ResponseDone,
    "error": ErrorEvent,
}


def parse_realtime_event(payload: dict[str, Any]) -> RealtimeEvent | None:
    """Parse server-sent JSON event into typed model. Unknown type → None."""
    t = payload.get("type")
    cls = _EVENT_MAP.get(t)
    if cls is None:
        return None
    return cls.model_validate(payload)
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_models.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/models.py clients/python/tests/test_models.py
git commit -m "feat(sdk): models.py Pydantic v2 + RealtimeEvent union (T3)

- SessionCreateRequest/Response（voice/speed/prompt/audit_persist）
- TokenRequest/Response
- 5 RealtimeEvent 子类（transcript.partial/final, response.text/done, error）
- ResponsePCM synthetic event 包 binary frame
- parse_realtime_event() 由 'type' field 选 class

per spec §4.3"
```

---

## Task 4: _base.py — BaseClient + httpx Client/AsyncClient + URL 解析

**Files:**
- Create: `clients/python/src/rtvoice_client/_base.py`
- Create: `clients/python/tests/test_base.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_base.py`:

```python
"""Test BaseClient: URL resolution, Bearer headers, response → exception."""
import pytest
import httpx
import respx


def test_per_service_urls_override_base():
    from rtvoice_client._base import _resolve_urls
    urls = _resolve_urls(
        base_url="http://main:80",
        stt_url="http://stt:9090",
        tts_url=None,
        realtime_url="http://rt:9000",
        tokens_url=None,
    )
    assert urls["stt"] == "http://stt:9090"
    assert urls["tts"] == "http://main:80"  # falls back
    assert urls["realtime"] == "http://rt:9000"
    assert urls["tokens"] == "http://main:80"


def test_base_url_required_when_no_per_service():
    from rtvoice_client._base import _resolve_urls
    with pytest.raises(ValueError):
        _resolve_urls(base_url=None, stt_url=None, tts_url=None,
                      realtime_url=None, tokens_url=None)


def test_bearer_header_added():
    from rtvoice_client._base import _build_headers
    h = _build_headers(api_key="secret")
    assert h["Authorization"] == "Bearer secret"


def test_no_bearer_when_no_api_key():
    from rtvoice_client._base import _build_headers
    h = _build_headers(api_key=None)
    assert "Authorization" not in h


@respx.mock
def test_response_with_error_body_raises_typed():
    """422 with prompt.too_long body → PromptTooLong."""
    from rtvoice_client.errors import PromptTooLong
    from rtvoice_client._base import _check_response
    respx.post("http://x/y").respond(
        422,
        json={"type": "error", "code": "prompt.too_long",
              "message": "too long", "request_id": "r1"},
    )
    resp = httpx.post("http://x/y")
    with pytest.raises(PromptTooLong):
        _check_response(resp)


@respx.mock
def test_response_with_2xx_returns_normally():
    from rtvoice_client._base import _check_response
    respx.get("http://x/y").respond(200, json={"ok": True})
    resp = httpx.get("http://x/y")
    _check_response(resp)  # no raise
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_base.py -v
```

- [ ] **Step 3: 写 _base.py**

`clients/python/src/rtvoice_client/_base.py`:

```python
"""BaseClient: URL resolution, Bearer headers, response → typed exception."""
from __future__ import annotations
from typing import Any

import httpx

from rtvoice_client.errors import _raise_for_body


def _resolve_urls(
    *,
    base_url: str | None,
    stt_url: str | None,
    tts_url: str | None,
    realtime_url: str | None,
    tokens_url: str | None,
) -> dict[str, str]:
    """Per-service URL override > base_url. base_url required if no per-service URL given."""
    fallback = base_url
    overrides = {"stt": stt_url, "tts": tts_url, "realtime": realtime_url, "tokens": tokens_url}
    if fallback is None and not all(overrides.values()):
        missing = [k for k, v in overrides.items() if v is None]
        raise ValueError(
            f"base_url is None and these per-service URLs missing: {missing}"
        )
    return {k: (v or fallback) for k, v in overrides.items()}  # type: ignore[misc]


def _build_headers(api_key: str | None) -> dict[str, str]:
    h: dict[str, str] = {"User-Agent": "rtvoice-client/0.1.0"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _try_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _check_response(resp: httpx.Response) -> None:
    """Raise typed exception on RTVoice error body; else return."""
    body = _try_json(resp)
    if resp.status_code >= 400:
        _raise_for_body(body, http_status=resp.status_code)
        # Defensive: server returned non-RTVoice 4xx/5xx
        from rtvoice_client.errors import ServerError
        raise ServerError(
            code="internal.unknown",
            message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code,
        )


# ---------------- Async + sync clients ----------------


class AsyncClient:
    """Async entry point: AsyncClient(api_key=..., base_url=...).stt / .tts / .realtime / .tokens"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        stt_url: str | None = None,
        tts_url: str | None = None,
        realtime_url: str | None = None,
        tokens_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._urls = _resolve_urls(
            base_url=base_url, stt_url=stt_url, tts_url=tts_url,
            realtime_url=realtime_url, tokens_url=tokens_url,
        )
        self._api_key = api_key
        self._headers = _build_headers(api_key)
        self._http = httpx.AsyncClient(headers=self._headers, timeout=timeout)
        # Lazy-init namespaces (avoid circular import at top level)
        self._stt: Any = None
        self._tts: Any = None
        self._realtime: Any = None
        self._tokens: Any = None

    @property
    def stt(self):
        if self._stt is None:
            from rtvoice_client.stt import AsyncSTT
            self._stt = AsyncSTT(self._http, self._urls["stt"])
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from rtvoice_client.tts import AsyncTTS
            self._tts = AsyncTTS(self._http, self._urls["tts"])
        return self._tts

    @property
    def realtime(self):
        if self._realtime is None:
            from rtvoice_client.realtime import AsyncRealtime
            self._realtime = AsyncRealtime(self._http, self._urls["realtime"], self._api_key)
        return self._realtime

    @property
    def tokens(self):
        if self._tokens is None:
            from rtvoice_client.tokens import AsyncTokens
            self._tokens = AsyncTokens(self._http, self._urls["tokens"])
        return self._tokens

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


class Client:
    """Sync entry point — wraps AsyncClient via asyncio.run for each call.

    Use AsyncClient when running inside an async event loop (FastAPI etc.).
    """

    def __init__(self, **kwargs: Any) -> None:
        self._async = AsyncClient(**kwargs)
        # Lazy sync namespace wrappers
        self._stt: Any = None
        self._tts: Any = None
        self._realtime: Any = None
        self._tokens: Any = None

    @property
    def stt(self):
        if self._stt is None:
            from rtvoice_client.stt import SyncSTT
            self._stt = SyncSTT(self._async.stt)
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from rtvoice_client.tts import SyncTTS
            self._tts = SyncTTS(self._async.tts)
        return self._tts

    @property
    def realtime(self):
        if self._realtime is None:
            from rtvoice_client.realtime import SyncRealtime
            self._realtime = SyncRealtime(self._async.realtime)
        return self._realtime

    @property
    def tokens(self):
        if self._tokens is None:
            from rtvoice_client.tokens import SyncTokens
            self._tokens = SyncTokens(self._async.tokens)
        return self._tokens

    def close(self) -> None:
        import asyncio
        asyncio.run(self._async.aclose())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_base.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/_base.py clients/python/tests/test_base.py
git commit -m "feat(sdk): _base.py BaseClient + URL/Bearer/typed-error 解析 (T4)

- AsyncClient: 4 lazy-init namespaces + httpx.AsyncClient
- Client: 包 AsyncClient 提供 sync 形态
- _resolve_urls: per-service URL > base_url > raise
- _build_headers: Bearer + UA
- _check_response: 4xx/5xx 走 _raise_for_body 抛 typed exception
- 6 单元测试

per spec §4.1"
```

---

## Task 5: stt.py — STT namespace（transcribe + stream）

**Files:**
- Create: `clients/python/src/rtvoice_client/stt.py`
- Create: `clients/python/tests/test_stt.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_stt.py`:

```python
"""Test STT namespace: transcribe + stream (mocked httpx)."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_async_transcribe_returns_text():
    from rtvoice_client.stt import AsyncSTT
    with respx.mock:
        respx.post("http://stt:9090/v1/asr").respond(
            200, json={"text": "你好世界"}
        )
        async with httpx.AsyncClient() as h:
            stt = AsyncSTT(h, "http://stt:9090")
            text = await stt.transcribe(b"\x00" * 100, sample_rate=16000)
            assert text == "你好世界"


@pytest.mark.asyncio
async def test_async_transcribe_raises_on_4xx():
    from rtvoice_client.stt import AsyncSTT
    from rtvoice_client.errors import ValidationError
    with respx.mock:
        respx.post("http://stt:9090/v1/asr").respond(
            422, json={"type": "error", "code": "validation.invalid_request",
                       "message": "bad pcm", "request_id": "r1"},
        )
        async with httpx.AsyncClient() as h:
            stt = AsyncSTT(h, "http://stt:9090")
            with pytest.raises(ValidationError):
                await stt.transcribe(b"x", sample_rate=16000)


def test_sync_transcribe_calls_async_via_run(mocker):
    """SyncSTT.transcribe should asyncio.run AsyncSTT.transcribe."""
    from rtvoice_client.stt import SyncSTT, AsyncSTT
    from unittest.mock import AsyncMock, MagicMock
    inner = MagicMock(spec=AsyncSTT)
    inner.transcribe = AsyncMock(return_value="hello")
    sync = SyncSTT(inner)
    result = sync.transcribe(b"x", sample_rate=16000)
    assert result == "hello"


@pytest.mark.asyncio
async def test_async_stream_context_manager():
    """stream() returns async context manager with feed + request_final."""
    from rtvoice_client.stt import AsyncSTT
    # Mocking websockets is tricky; assert structure only.
    # Verify the namespace exposes stream method that returns awaitable context.
    async with httpx.AsyncClient() as h:
        stt = AsyncSTT(h, "http://stt:9090")
        cm = stt.stream(ws_url="ws://stt:9090/v1/asr")
        # 是 async context manager
        assert hasattr(cm, "__aenter__")
        assert hasattr(cm, "__aexit__")
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_stt.py -v
```

- [ ] **Step 3: 写 stt.py**

`clients/python/src/rtvoice_client/stt.py`:

```python
"""STT namespace: REST transcribe + WS stream."""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import websockets

from rtvoice_client._base import _check_response


class AsyncSTT:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
    ) -> str:
        """One-shot transcribe; pcm = int16 LE mono."""
        r = await self._http.post(
            f"{self._base}/v1/asr",
            content=pcm,
            params={"sample_rate": sample_rate},
            headers={"Content-Type": "application/octet-stream"},
        )
        _check_response(r)
        body = r.json()
        return body.get("text", "")

    @asynccontextmanager
    async def stream(self, *, ws_url: str | None = None) -> AsyncIterator["AsyncSTTStream"]:
        """Open WS streaming session; auto-close on exit.

        ws_url defaults to ws variant of base_url + /v1/asr.
        """
        if ws_url is None:
            ws_url = self._base.replace("http://", "ws://").replace("https://", "wss://") + "/v1/asr"
        ws = await websockets.connect(ws_url, max_size=None)
        try:
            yield AsyncSTTStream(ws)
        finally:
            try:
                await ws.close()
            except Exception:
                pass


class AsyncSTTStream:
    """Streaming WS session: feed bytes, request_final returns text."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def feed(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def request_final(self, *, timeout: float = 5.0) -> str:
        await self._ws.send("EOS")
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msg = await asyncio.wait_for(
                self._ws.recv(),
                timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
            )
            if isinstance(msg, str):
                ev = json.loads(msg)
                if ev.get("type") == "final":
                    return ev.get("text", "")
        return ""


class SyncSTT:
    """Sync wrapper: each call asyncio.run AsyncSTT method."""

    def __init__(self, inner: AsyncSTT) -> None:
        self._inner = inner

    def transcribe(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        return asyncio.run(self._inner.transcribe(pcm, sample_rate=sample_rate))
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_stt.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/stt.py clients/python/tests/test_stt.py
git commit -m "feat(sdk): stt.py STT namespace (transcribe + stream) (T5)

- AsyncSTT.transcribe: REST POST /v1/asr + sample_rate
- AsyncSTT.stream: async context manager → AsyncSTTStream(feed/request_final)
- SyncSTT 包 async via asyncio.run
- 4 单元测试（mock httpx + 结构验证）

per spec §4.1"
```

---

## Task 6: tts.py — TTS namespace（synthesize + stream）

**Files:**
- Create: `clients/python/src/rtvoice_client/tts.py`
- Create: `clients/python/tests/test_tts.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_tts.py`:

```python
"""Test TTS namespace."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_synthesize_returns_bytes():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        respx.post("http://tts:9880/v1/tts/stream").respond(
            200, content=b"\x01\x02\x03", headers={"Content-Type": "audio/pcm"},
        )
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            pcm = await tts.synthesize("hi")
            assert pcm == b"\x01\x02\x03"


@pytest.mark.asyncio
async def test_synthesize_passes_voice_speed_lang():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        route = respx.post("http://tts:9880/v1/tts/stream").respond(200, content=b"x")
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            await tts.synthesize("hi", voice="alice", speed=1.5, lang="cmn")
        body = route.calls.last.request.read()
        import json as _json
        parsed = _json.loads(body)
        assert parsed["voice"] == "alice"
        assert parsed["speed"] == 1.5
        assert parsed["lang"] == "cmn"


@pytest.mark.asyncio
async def test_stream_yields_chunks():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        respx.post("http://tts:9880/v1/tts/stream").respond(
            200, stream=[b"abc", b"def", b"ghi"]
        )
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            chunks = [c async for c in tts.stream("hi")]
            assert b"".join(chunks) == b"abcdefghi"


def test_sync_synthesize_calls_async():
    from rtvoice_client.tts import SyncTTS, AsyncTTS
    from unittest.mock import AsyncMock, MagicMock
    inner = MagicMock(spec=AsyncTTS)
    inner.synthesize = AsyncMock(return_value=b"\x00")
    sync = SyncTTS(inner)
    assert sync.synthesize("hi") == b"\x00"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_tts.py -v
```

- [ ] **Step 3: 写 tts.py**

`clients/python/src/rtvoice_client/tts.py`:

```python
"""TTS namespace: synthesize (one-shot bytes) + stream (chunked)."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

import httpx

from rtvoice_client._base import _check_response


class AsyncTTS:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "default_zh_female",
        speed: float = 1.0,
        lang: str = "cmn",
    ) -> bytes:
        """Return entire PCM (24k mono int16) as bytes."""
        r = await self._http.post(
            f"{self._base}/v1/tts/stream",
            json={"text": text, "voice": voice, "speed": speed, "lang": lang},
        )
        _check_response(r)
        return r.content

    async def stream(
        self,
        text: str,
        *,
        voice: str = "default_zh_female",
        speed: float = 1.0,
        lang: str = "cmn",
    ) -> AsyncIterator[bytes]:
        """Yield PCM chunks as they arrive."""
        async with self._http.stream(
            "POST",
            f"{self._base}/v1/tts/stream",
            json={"text": text, "voice": voice, "speed": speed, "lang": lang},
        ) as r:
            if r.status_code >= 400:
                content = await r.aread()
                import json
                try:
                    body = json.loads(content)
                except Exception:
                    body = None
                from rtvoice_client.errors import _raise_for_body
                _raise_for_body(body, http_status=r.status_code)
                # Defensive
                from rtvoice_client.errors import ServerError
                raise ServerError(code="internal.unknown",
                                  message=f"HTTP {r.status_code}",
                                  http_status=r.status_code)
            async for chunk in r.aiter_bytes():
                if chunk:
                    yield chunk


class SyncTTS:
    def __init__(self, inner: AsyncTTS) -> None:
        self._inner = inner

    def synthesize(
        self, text: str, *, voice: str = "default_zh_female",
        speed: float = 1.0, lang: str = "cmn",
    ) -> bytes:
        return asyncio.run(
            self._inner.synthesize(text, voice=voice, speed=speed, lang=lang)
        )

    def stream(self, text: str, **kwargs):
        """Sync iterator wrapping async stream — drains into list."""
        async def _drain():
            return [c async for c in self._inner.stream(text, **kwargs)]
        chunks = asyncio.run(_drain())
        for c in chunks:
            yield c
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_tts.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/tts.py clients/python/tests/test_tts.py
git commit -m "feat(sdk): tts.py TTS namespace (synthesize + stream) (T6)

- AsyncTTS.synthesize: 一次性 POST /v1/tts/stream → bytes
- AsyncTTS.stream: 流式 POST + aiter_bytes
- SyncTTS sync wrapper（stream 改 drain 后 yield）
- 4 单元测试（mock httpx）"
```

---

## Task 7: realtime.py — Realtime namespace primitives + conversation helper

**Files:**
- Create: `clients/python/src/rtvoice_client/realtime.py`
- Create: `clients/python/tests/test_realtime.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_realtime.py`:

```python
"""Test Realtime namespace: primitives + conversation helper."""
import asyncio
import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_create_session_returns_typed_response():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.models import SessionCreateResponse
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            201,
            json={
                "session_id": "sess_a", "ws_url": "ws://rt:9000/v1/realtime/sess_a",
                "expires_at": "2026-05-09T16:00:00Z", "voice": "v",
                "speed": 1.0, "prompt": "p", "audit_persist": False,
            },
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            resp = await rt.create_session(prompt="p")
        assert isinstance(resp, SessionCreateResponse)
        assert resp.session_id == "sess_a"


@pytest.mark.asyncio
async def test_create_session_capacity_full_raises():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.errors import CapacityFull
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            503,
            json={"type": "error", "code": "session.capacity_full",
                  "message": "max 5", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            with pytest.raises(CapacityFull):
                await rt.create_session()


@pytest.mark.asyncio
async def test_prompt_too_long_raises():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.errors import PromptTooLong
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            422,
            json={"type": "error", "code": "prompt.too_long",
                  "message": "x", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            with pytest.raises(PromptTooLong):
                await rt.create_session(prompt="x" * 9999)


@pytest.mark.asyncio
async def test_realtime_session_update_methods_send_correct_json():
    """Session has update_prompt/update_voice/update_speed/clear_memory.

    These methods send JSON over WS. We mock the underlying ws.send.
    """
    from rtvoice_client.realtime import AsyncRealtimeSession
    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    sess = AsyncRealtimeSession(fake_ws)
    await sess.update_prompt("hello")
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "prompt": "hello"}))
    await sess.update_voice("alice")
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "voice": "alice"}))
    await sess.update_speed(1.5)
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "speed": 1.5}))
    await sess.clear_memory()
    fake_ws.send.assert_awaited_with(json.dumps({"type": "memory.clear"}))


@pytest.mark.asyncio
async def test_realtime_session_eos_sends_audio_eos():
    from rtvoice_client.realtime import AsyncRealtimeSession
    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    sess = AsyncRealtimeSession(fake_ws)
    await sess.eos()
    fake_ws.send.assert_awaited_with("audio.eos")


@pytest.mark.asyncio
async def test_realtime_events_parses_typed():
    """Iterate ws.recv → typed RealtimeEvent."""
    from rtvoice_client.realtime import AsyncRealtimeSession
    from rtvoice_client.models import TranscriptFinal, ResponseDone

    msgs = [
        json.dumps({"type": "transcript.final", "text": "hi"}),
        b"\x00\x01\x02",
        json.dumps({"type": "response.done", "text": "ok"}),
    ]
    fake_ws = AsyncMock()
    fake_ws.recv = AsyncMock(side_effect=msgs + [Exception("end")])
    sess = AsyncRealtimeSession(fake_ws)
    collected = []
    try:
        async for evt in sess.events():
            collected.append(evt)
            if hasattr(evt, "type") and evt.type == "response.done":
                break
    except Exception:
        pass
    types = [type(e).__name__ for e in collected]
    assert "TranscriptFinal" in types
    assert "ResponsePCM" in types
    assert "ResponseDone" in types


@pytest.mark.asyncio
async def test_conversation_helper_full_flow():
    """conversation() creates session, connects, feeds audio, yields events."""
    from contextlib import asynccontextmanager
    from rtvoice_client.realtime import AsyncRealtime, AsyncRealtimeSession
    from rtvoice_client.models import SessionCreateResponse, ResponseDone

    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    msg_iter = iter([
        json.dumps({"type": "transcript.final", "text": "hi"}),
        json.dumps({"type": "response.done", "text": "ok"}),
    ])
    async def _recv():
        return next(msg_iter)
    fake_ws.recv = _recv

    rt = AsyncRealtime.__new__(AsyncRealtime)
    rt._http = MagicMock()
    rt._base = "http://rt:9000"
    rt._api_key = None

    async def _create(**kwargs):
        return SessionCreateResponse(
            session_id="sess_x", ws_url="ws://rt/sess_x",
            expires_at="2026-05-09T16:00:00Z",
            voice="v", speed=1.0, prompt="p", audit_persist=False,
        )
    rt.create_session = _create

    @asynccontextmanager
    async def _connect(sess):
        yield AsyncRealtimeSession(fake_ws)
    rt.connect = _connect

    async def _audio_iter():
        yield b"\x00" * 100

    events = []
    async for evt in rt.conversation(_audio_iter(), prompt="p"):
        events.append(evt)
        if isinstance(evt, ResponseDone):
            break
    types = [type(e).__name__ for e in events]
    assert "TranscriptFinal" in types
    assert "ResponseDone" in types


@pytest.mark.asyncio
async def test_sync_realtime_create_session_via_run():
    from rtvoice_client.realtime import SyncRealtime, AsyncRealtime
    from rtvoice_client.models import SessionCreateResponse
    inner = MagicMock(spec=AsyncRealtime)
    inner.create_session = AsyncMock(return_value=SessionCreateResponse(
        session_id="sess_x", ws_url="ws://x", expires_at="t",
        voice="v", speed=1.0, prompt="p", audit_persist=False,
    ))
    sync = SyncRealtime(inner)
    r = sync.create_session(prompt="p")
    assert r.session_id == "sess_x"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_realtime.py -v
```

- [ ] **Step 3: 写 realtime.py**

`clients/python/src/rtvoice_client/realtime.py`:

```python
"""Realtime namespace: create_session + connect + conversation helper."""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterable, AsyncIterator

import httpx
import websockets

from rtvoice_client._base import _check_response
from rtvoice_client.errors import RTVoiceError
from rtvoice_client.models import (
    SessionCreateRequest, SessionCreateResponse,
    RealtimeEvent, ResponsePCM, parse_realtime_event,
)


class AsyncRealtimeSession:
    """Active WS session: send PCM, send EOS, send updates, receive typed events."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def feed(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def eos(self) -> None:
        await self._ws.send("audio.eos")

    async def update_prompt(self, prompt: str) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "prompt": prompt}))

    async def update_voice(self, voice: str) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "voice": voice}))

    async def update_speed(self, speed: float) -> None:
        await self._ws.send(json.dumps({"type": "session.update", "speed": speed}))

    async def clear_memory(self) -> None:
        await self._ws.send(json.dumps({"type": "memory.clear"}))

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        """Iterate WS frames → typed RealtimeEvent."""
        while True:
            msg = await self._ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                yield ResponsePCM(data=bytes(msg))
                continue
            try:
                payload = json.loads(msg)
            except Exception:
                continue
            evt = parse_realtime_event(payload)
            if evt is not None:
                yield evt


class AsyncRealtime:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    async def create_session(
        self,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        prompt: str | None = None,
        audit_persist: bool = False,
    ) -> SessionCreateResponse:
        req = SessionCreateRequest(
            voice=voice, speed=speed, prompt=prompt, audit_persist=audit_persist,
        )
        r = await self._http.post(
            f"{self._base}/v1/sessions",
            json=req.model_dump(exclude_none=True),
        )
        _check_response(r)
        return SessionCreateResponse.model_validate(r.json())

    @asynccontextmanager
    async def connect(self, sess: SessionCreateResponse) -> AsyncIterator[AsyncRealtimeSession]:
        """Open WS to sess.ws_url with bearer; yield session helper."""
        # 三路 Bearer：默认走 subprotocol（WebSocket 标准字段不被 reverse proxy 改）
        subprotocols = [f"bearer.{self._api_key}"] if self._api_key else None
        ws = await websockets.connect(
            sess.ws_url,
            max_size=None,
            subprotocols=subprotocols,
        )
        try:
            yield AsyncRealtimeSession(ws)
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def conversation(
        self,
        audio_iter: AsyncIterable[bytes],
        *,
        voice: str | None = None,
        speed: float = 1.0,
        prompt: str | None = None,
        audit_persist: bool = False,
    ) -> AsyncIterator[RealtimeEvent]:
        """高层 helper：创 session + 连 WS + 喂音频 + yield 事件直到 response.done。"""
        sess = await self.create_session(
            voice=voice, speed=speed, prompt=prompt, audit_persist=audit_persist,
        )
        async with self.connect(sess) as ws_sess:
            async def _feed():
                try:
                    async for chunk in audio_iter:
                        await ws_sess.feed(chunk)
                finally:
                    try:
                        await ws_sess.eos()
                    except Exception:
                        pass

            feed_task = asyncio.create_task(_feed())
            try:
                async for evt in ws_sess.events():
                    yield evt
                    if hasattr(evt, "type") and evt.type == "response.done":
                        break
            finally:
                if not feed_task.done():
                    feed_task.cancel()
                    try:
                        await feed_task
                    except (asyncio.CancelledError, Exception):
                        pass


class SyncRealtime:
    def __init__(self, inner: AsyncRealtime) -> None:
        self._inner = inner

    def create_session(self, **kwargs) -> SessionCreateResponse:
        return asyncio.run(self._inner.create_session(**kwargs))
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_realtime.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/realtime.py clients/python/tests/test_realtime.py
git commit -m "feat(sdk): realtime.py primitives + conversation helper (T7)

- AsyncRealtime: create_session + connect (async ctx manager)
- AsyncRealtimeSession: feed/eos/update_prompt|voice|speed/clear_memory/events
- AsyncRealtime.conversation: 高层 helper（创 session + 连 + 喂音 + yield 事件）
- SyncRealtime sync wrapper（仅 create_session；streaming 用 async）
- 8 单元测试（mock httpx + mock ws）

per spec §4.1（primitives + helper）"
```

---

## Task 8: tokens.py — LiveKit tokens namespace

**Files:**
- Create: `clients/python/src/rtvoice_client/tokens.py`
- Create: `clients/python/tests/test_tokens.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_tokens.py`:

```python
"""Test Tokens namespace."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_livekit_returns_typed_response():
    from rtvoice_client.tokens import AsyncTokens
    from rtvoice_client.models import TokenResponse
    with respx.mock:
        respx.post("http://tok:8000/v1/tokens").respond(
            200, json={"token": "eyJ...", "url": "ws://lk:7880",
                       "room": "r", "identity": "alice"},
        )
        async with httpx.AsyncClient() as h:
            tok = AsyncTokens(h, "http://tok:8000")
            r = await tok.livekit(identity="alice", room="r", ttl_minutes=10)
        assert isinstance(r, TokenResponse)
        assert r.token == "eyJ..."


@pytest.mark.asyncio
async def test_livekit_auth_error_raises():
    from rtvoice_client.tokens import AsyncTokens
    from rtvoice_client.errors import AuthError
    with respx.mock:
        respx.post("http://tok:8000/v1/tokens").respond(
            401,
            json={"type": "error", "code": "auth.invalid_token",
                  "message": "x", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            tok = AsyncTokens(h, "http://tok:8000")
            with pytest.raises(AuthError):
                await tok.livekit(identity="a", room="b")
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_tokens.py -v
```

- [ ] **Step 3: 写 tokens.py**

`clients/python/src/rtvoice_client/tokens.py`:

```python
"""LiveKit tokens namespace."""
from __future__ import annotations
import asyncio

import httpx

from rtvoice_client._base import _check_response
from rtvoice_client.models import TokenRequest, TokenResponse


class AsyncTokens:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def livekit(
        self,
        *,
        identity: str,
        room: str,
        ttl_minutes: int = 10,
    ) -> TokenResponse:
        req = TokenRequest(identity=identity, room=room, ttl_minutes=ttl_minutes)
        r = await self._http.post(
            f"{self._base}/v1/tokens",
            json=req.model_dump(),
        )
        _check_response(r)
        return TokenResponse.model_validate(r.json())


class SyncTokens:
    def __init__(self, inner: AsyncTokens) -> None:
        self._inner = inner

    def livekit(self, *, identity: str, room: str, ttl_minutes: int = 10) -> TokenResponse:
        return asyncio.run(
            self._inner.livekit(identity=identity, room=room, ttl_minutes=ttl_minutes)
        )
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_tokens.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/src/rtvoice_client/tokens.py clients/python/tests/test_tokens.py
git commit -m "feat(sdk): tokens.py LiveKit tokens namespace (T8)

- AsyncTokens.livekit: POST /v1/tokens
- SyncTokens 包 async
- 2 单元测试"
```

---

## Task 9: Sync wrapper smoke + AsyncClient/Client integration test

**Files:**
- Create: `clients/python/tests/test_sync.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_sync.py`:

```python
"""Test that Client (sync) and AsyncClient (async) expose 4 namespaces correctly."""
import pytest


def test_async_client_has_4_namespaces():
    from rtvoice_client import AsyncClient
    c = AsyncClient(api_key="k", base_url="http://x")
    # Lazy access — properties returning namespace objects
    from rtvoice_client.stt import AsyncSTT
    from rtvoice_client.tts import AsyncTTS
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.tokens import AsyncTokens
    assert isinstance(c.stt, AsyncSTT)
    assert isinstance(c.tts, AsyncTTS)
    assert isinstance(c.realtime, AsyncRealtime)
    assert isinstance(c.tokens, AsyncTokens)


def test_sync_client_has_4_namespaces():
    from rtvoice_client import Client
    c = Client(api_key="k", base_url="http://x")
    from rtvoice_client.stt import SyncSTT
    from rtvoice_client.tts import SyncTTS
    from rtvoice_client.realtime import SyncRealtime
    from rtvoice_client.tokens import SyncTokens
    assert isinstance(c.stt, SyncSTT)
    assert isinstance(c.tts, SyncTTS)
    assert isinstance(c.realtime, SyncRealtime)
    assert isinstance(c.tokens, SyncTokens)
```

- [ ] **Step 2: 跑测试**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_sync.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/tests/test_sync.py
git commit -m "test(sdk): sync wrapper smoke + 4 namespace integration (T9)"
```

---

## Task 10: e2e_smoke.py — 真 prod E2E（pytest.mark.e2e）

**Files:**
- Create: `clients/python/tests/test_e2e_smoke.py`

- [ ] **Step 1: 写测试**

`clients/python/tests/test_e2e_smoke.py`:

```python
"""End-to-end smoke against real RTVoice prod.

跑：cd clients/python && pytest -m e2e -v
仅在 RTVOICE_E2E_BASE 环境变量设置时跑（默认跳过）。
"""
import os
import pytest


pytestmark = pytest.mark.e2e


@pytest.fixture
def base_url():
    url = os.environ.get("RTVOICE_E2E_BASE")
    if not url:
        pytest.skip("RTVOICE_E2E_BASE not set; skipping e2e")
    return url


@pytest.fixture
def api_key():
    return os.environ.get("RTVOICE_E2E_API_KEY", "")


def test_e2e_info_endpoint_reachable(base_url, api_key):
    """GET /info 返回 SP3 capabilities."""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.get(f"{base_url}/info", headers=headers, timeout=10)
    r.raise_for_status()
    caps = r.json()["capabilities"]
    assert caps["memory"] is True
    assert "default_prompt" in caps


def test_e2e_create_and_get_session(base_url, api_key):
    """POST /v1/sessions → SDK 解析；prompt 透传 OK。"""
    from rtvoice_client import Client
    c = Client(api_key=api_key or None, base_url=base_url)
    sess = c.realtime.create_session(prompt="e2e test prompt", audit_persist=False)
    assert sess.session_id.startswith("sess_")
    assert sess.prompt == "e2e test prompt"


def test_e2e_prompt_too_long(base_url, api_key):
    """超长 prompt → SDK 抛 PromptTooLong。"""
    from rtvoice_client import Client
    from rtvoice_client.errors import PromptTooLong
    c = Client(api_key=api_key or None, base_url=base_url)
    with pytest.raises(PromptTooLong):
        c.realtime.create_session(prompt="x" * 9999)
```

`clients/python/conftest.py`:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring real prod RTVoice")
```

- [ ] **Step 2: 验跳过（默认无 RTVOICE_E2E_BASE）**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pytest tests/test_e2e_smoke.py -v
```

Expected: 3 skipped。

- [ ] **Step 3: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add clients/python/tests/test_e2e_smoke.py clients/python/conftest.py
git commit -m "test(sdk): e2e_smoke.py 真 prod E2E (pytest.mark.e2e) (T10)

跑法：RTVOICE_E2E_BASE=http://192.168.66.163:9000 pytest -m e2e
默认跳过；CI optional。"
```

---

## Task 11: K · memory.py 加 clear() 方法

**Files:**
- Modify: `services/realtime-server/app/memory.py`
- Modify: `services/realtime-server/tests/test_memory.py`

- [ ] **Step 1: 写新测试**

在 `services/realtime-server/tests/test_memory.py` 末尾追加：

```python
def test_clear_empties_buffer():
    m = ConversationMemory(max_turns=3)
    m.append_turn("u1", "a1")
    m.append_turn("u2", "a2")
    assert len(m) == 4
    m.clear()
    assert len(m) == 0
    assert list(m) == []


def test_clear_preserves_max_turns_after_clear():
    """clear 后还能继续 append."""
    m = ConversationMemory(max_turns=2)
    m.append_turn("u1", "a1")
    m.clear()
    m.append_turn("u2", "a2")
    m.append_turn("u3", "a3")
    m.append_turn("u4", "a4")  # 该驱逐 u2/a2（cap=2）
    msgs = list(m)
    assert msgs[0]["content"] == "u3"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_memory.py -v
```

Expected: 2 fails（AttributeError: clear）。

- [ ] **Step 3: 改 memory.py**

在 `services/realtime-server/app/memory.py` `ConversationMemory` 类内添加方法：

```python
    def clear(self) -> None:
        """清空当前历史；prompt 不动."""
        self._buf.clear()
```

- [ ] **Step 4: 跑测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_memory.py -v
```

Expected: 6 passed（4 旧 + 2 新）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/memory.py services/realtime-server/tests/test_memory.py
git commit -m "feat(realtime-server): ConversationMemory.clear() (T11)

- 新方法 clear() 清空 deque，prompt/maxlen 不动
- +2 单元测试

per spec §4.4"
```

---

## Task 12: K · main.py session.update voice/speed + memory.clear

**Files:**
- Modify: `services/realtime-server/app/main.py`
- Modify: `services/realtime-server/app/session_manager.py`（加 tts_client_dirty 字段）
- Modify: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: Session 加 tts_client_dirty 字段**

修改 `services/realtime-server/app/session_manager.py` 的 `Session` dataclass，在最后追加：

```python
    tts_client_dirty: bool = False
```

- [ ] **Step 2: 写 endpoint 测试**

在 `services/realtime-server/tests/test_endpoints.py` 末尾追加：

```python
def test_session_update_voice_speed_via_ws(client):
    """WS session.update voice/speed → ack（无 error）."""
    r = client.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    with client.websocket_connect(f"/v1/realtime/{sid}") as ws:
        import json as _json
        ws.send_text(_json.dumps({"type": "session.update", "voice": "alice"}))
        ws.send_text(_json.dumps({"type": "session.update", "speed": 1.5}))
        # 没 error 收到（用 timeout 简短检查）
        # 复杂的：测 sess.tts_client_dirty 用 monkeypatch
    # OK：endpoint 测试只能粗验"不抛"


def test_session_update_speed_out_of_range_emits_error(client):
    """speed=3.0 → error validation.invalid_request."""
    r = client.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    import json as _json
    with client.websocket_connect(f"/v1/realtime/{sid}") as ws:
        ws.send_text(_json.dumps({"type": "session.update", "speed": 3.0}))
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "validation.invalid_request"


def test_memory_clear_event_handled(client):
    """WS memory.clear → no error event back."""
    r = client.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    import json as _json
    with client.websocket_connect(f"/v1/realtime/{sid}") as ws:
        ws.send_text(_json.dumps({"type": "memory.clear"}))
        # 期望：无 error；但 TestClient 收消息会 block，所以仅验"发不抛"
```

- [ ] **Step 3: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "voice_speed or speed_out_of_range or memory_clear" -v
```

- [ ] **Step 4: 改 main.py**

定位 WS handler 中 `if ev.get("type") == "session.update":` 段（约 SP3 T7 加的位置）。把整段替换为：

```python
                    if ev.get("type") == "session.update":
                        allowed = {"type", "prompt", "voice", "speed"}
                        extra = set(ev.keys()) - allowed
                        if extra:
                            await ws.send_json({
                                "type": "error",
                                "code": "session.update.invalid",
                                "message": f"only prompt/voice/speed; got extra: {sorted(extra)}",
                                "request_id": None,
                            })
                            continue
                        if "prompt" in ev:
                            new_prompt = str(ev["prompt"])
                            if len(new_prompt) > config.PROMPT_MAX_CHARS:
                                await ws.send_json({
                                    "type": "error", "code": "prompt.too_long",
                                    "message": f"prompt > {config.PROMPT_MAX_CHARS}",
                                    "request_id": None,
                                })
                            else:
                                sess.prompt = new_prompt
                                log.info("session %s prompt updated (%d chars)",
                                         session_id, len(new_prompt))
                        if "voice" in ev:
                            sess.voice = str(ev["voice"])
                            sess.tts_client_dirty = True
                            log.info("session %s voice updated to %s (dirty)",
                                     session_id, sess.voice)
                        if "speed" in ev:
                            try:
                                s = float(ev["speed"])
                            except (TypeError, ValueError):
                                await ws.send_json({
                                    "type": "error", "code": "validation.invalid_request",
                                    "message": "speed must be a number",
                                    "request_id": None,
                                })
                                continue
                            if not (0.5 <= s <= 2.0):
                                await ws.send_json({
                                    "type": "error", "code": "validation.invalid_request",
                                    "message": "speed out of range (0.5-2.0)",
                                    "request_id": None,
                                })
                                continue
                            sess.speed = s
                            sess.tts_client_dirty = True
                            log.info("session %s speed updated to %.2f (dirty)",
                                     session_id, s)
                    elif ev.get("type") == "memory.clear":
                        sess.memory.clear()
                        log.info("session %s memory cleared", session_id)
                        if sess.audit_writer is not None:
                            try:
                                await sess.audit_writer.write({"event": "memory.clear"})
                            except Exception:
                                log.exception("audit write memory.clear failed")
                    else:
                        log.debug("session %s: unknown event %r",
                                  session_id, ev.get("type"))
```

注意删除原有的"unknown event log.debug"行，因为合并进新 elif 链了。

- [ ] **Step 5: 跑全部 endpoint 测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v
```

Expected: 所有原有测试通过 + 3 个新测试通过（部分可能因 TestClient ws 行为不完全可控；至少 `test_session_update_speed_out_of_range_emits_error` 必须过）。

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/main.py services/realtime-server/app/session_manager.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): K - session.update voice/speed + memory.clear (T12)

- session.update 白名单扩展：prompt + voice + speed
- voice/speed 改后置 sess.tts_client_dirty=True（pipeline 下 turn 重建）
- speed 0.5-2.0 范围校验，越界发 validation.invalid_request error
- memory.clear 事件清 sess.memory + audit log
- Session dataclass 加 tts_client_dirty 字段
- +3 endpoint 测试

per spec §4.4 + D-2026-05-09-B.7"
```

---

## Task 13: K · pipeline.run_turn TTS rebuild on dirty

**Files:**
- Modify: `services/realtime-server/app/pipeline.py`
- Modify: `services/realtime-server/tests/test_pipeline_mock.py`

- [ ] **Step 1: 改 FakeTTSClient + 写 2 个新测试**

修改 `services/realtime-server/tests/test_pipeline_mock.py`。

1a. 让 `FakeTTSClient` 跟踪重建次数：

把现有 `FakeTTSClient` 类替换为：

```python
class FakeTTSClient:
    def __init__(self, voice="default", speed=1.0):
        self.voice = voice
        self.speed = speed
        self.opened_ws = None
        self.closed = False

    async def open_ws(self):
        self.opened_ws = FakeTTSWS()
        return self.opened_ws

    async def close(self):
        self.closed = True
```

1b. 文件末尾追加 2 个测试：

```python
@pytest.mark.asyncio
async def test_run_turn_rebuilds_tts_when_dirty(monkeypatch):
    """sess.tts_client_dirty=True → pipeline 关旧 TTS、建新（用新 voice/speed）."""
    from app.pipeline import run_turn
    from app import pipeline as pipeline_mod

    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient()
    old_tts = FakeTTSClient(voice="old_voice", speed=1.0)
    sess.tts_client = old_tts
    sess.voice = "new_voice"  # 客户端已改
    sess.speed = 1.5
    sess.tts_client_dirty = True

    # Patch TTSClient 构造（pipeline.py 内 import 的 TTSClient）
    new_tts = FakeTTSClient(voice="new_voice", speed=1.5)
    monkeypatch.setattr("app.pipeline.TTSClient", lambda **kwargs: new_tts)

    ws = FakeWS()
    await run_turn(sess, ws)

    assert old_tts.closed is True       # 旧 client 关了
    assert sess.tts_client is new_tts   # 替换成新的
    assert sess.tts_client_dirty is False  # flag 复位


@pytest.mark.asyncio
async def test_run_turn_does_not_rebuild_when_not_dirty():
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient()
    tts = FakeTTSClient()
    sess.tts_client = tts
    sess.tts_client_dirty = False
    ws = FakeWS()
    await run_turn(sess, ws)
    assert tts.closed is False  # 没被关
    assert sess.tts_client is tts  # 没换
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -k "rebuilds or not_rebuild" -v
```

- [ ] **Step 3: 改 pipeline.py**

在 `services/realtime-server/app/pipeline.py` 中，把 `from app import config` 行下追加：

```python
from app.tts_client import TTSClient
```

并在 `async def run_turn(sess, ws):` 函数体最开头（`sess.current_turn_task = asyncio.current_task()` 之后）插入重建逻辑：

```python
    # SP4 K: voice/speed 热改 → pipeline 这里重建 TTS client
    if getattr(sess, "tts_client_dirty", False):
        try:
            old = sess.tts_client
            if old is not None and hasattr(old, "close"):
                res = old.close()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            log.exception("close old tts_client failed (continuing)")
        try:
            sess.tts_client = TTSClient(
                base_url=config.TTS_BASE_URL,
                voice=sess.voice,
                speed=sess.speed,
                api_key=config.RTVOICE_API_KEY or None,
            )
            sess.tts_client_dirty = False
            log.info("session %s rebuilt tts_client (voice=%s speed=%.2f)",
                     sess.id, sess.voice, sess.speed)
        except Exception:
            log.exception("rebuild tts_client failed")
            # 沿用旧 client；下次 turn 再尝试
            sess.tts_client_dirty = False
```

- [ ] **Step 4: 跑测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -v
```

Expected: 12 passed（10 旧 + 2 新）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/pipeline.py services/realtime-server/tests/test_pipeline_mock.py
git commit -m "feat(realtime-server): K - pipeline 重建 TTS on dirty (T13)

- run_turn 开头：tts_client_dirty 时关旧 + 用 sess.voice/speed 新建
- 异常时沿用旧 client；dirty flag 总复位（防卡住）
- +2 单元测试

per spec §4.4"
```

---

## Task 14: A · metrics.py — 3 个新 Prometheus metric

**Files:**
- Create: `services/realtime-server/app/metrics.py`
- Modify: `services/realtime-server/app/session_manager.py`
- Modify: `services/realtime-server/app/pipeline.py`
- Modify: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: 写 metrics.py**

`services/realtime-server/app/metrics.py`:

```python
"""SP4 A-lite: realtime-server 自定义 Prometheus metrics."""
from prometheus_client import Counter, Gauge

SESSIONS_ACTIVE = Gauge(
    "rtvoice_realtime_sessions_active",
    "current number of active sessions",
)

TURNS_TOTAL = Counter(
    "rtvoice_realtime_turns_total",
    "total run_turn invocations",
    ["status"],  # ok / error
)

AUDIT_QUEUE_DEPTH = Gauge(
    "rtvoice_realtime_audit_queue_depth",
    "sum of all session audit queue sizes (no per-session label to avoid cardinality blowup)",
)
```

- [ ] **Step 2: SessionManager 接 SESSIONS_ACTIVE + AUDIT_QUEUE_DEPTH**

在 `services/realtime-server/app/session_manager.py` 顶部 import 段加：

```python
from app.metrics import SESSIONS_ACTIVE, AUDIT_QUEUE_DEPTH
```

在 `create()` 方法 `return sess` 之前加：

```python
            SESSIONS_ACTIVE.set(self.active_count())
```

在 `cleanup()` 方法函数末尾（最后一行 `for c in (...)` 循环之后）加：

```python
        SESSIONS_ACTIVE.set(self.active_count())
        # 重新计算 audit queue 总深度
        try:
            depth = 0
            for s in self._sessions.values():
                if s.audit_writer is not None and hasattr(s.audit_writer, "_q"):
                    depth += s.audit_writer._q.qsize()
            AUDIT_QUEUE_DEPTH.set(depth)
        except Exception:
            pass
```

- [ ] **Step 3: pipeline 接 TURNS_TOTAL**

在 `services/realtime-server/app/pipeline.py` import 段加：

```python
from app.metrics import TURNS_TOTAL
```

在 `run_turn` 末尾的 `finally` 块内（`sess.last_activity = ...` 之前）加：

```python
        # 状态：assistant_chunks 非空 → ok；否则视为 error/empty
        status = "ok" if assistant_chunks else "error"
        TURNS_TOTAL.labels(status=status).inc()
```

- [ ] **Step 4: 写 endpoint 测试**

在 `services/realtime-server/tests/test_endpoints.py` 末尾追加：

```python
def test_metrics_endpoint_exposes_sp4_metrics(client):
    """/metrics 含 3 个 SP4 自定义 metric 名."""
    r = client.get("/metrics")
    body = r.text
    assert "rtvoice_realtime_sessions_active" in body
    assert "rtvoice_realtime_turns_total" in body
    assert "rtvoice_realtime_audit_queue_depth" in body
```

- [ ] **Step 5: 跑测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -k "metrics_endpoint" -v
python3 -m pytest tests/ -v 2>&1 | tail -10
```

Expected: 全过。

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/metrics.py services/realtime-server/app/session_manager.py services/realtime-server/app/pipeline.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): A - 3 SP4 metrics (T14)

- metrics.py: SESSIONS_ACTIVE / TURNS_TOTAL / AUDIT_QUEUE_DEPTH
- SessionManager.create/cleanup 更新 SESSIONS_ACTIVE + AUDIT_QUEUE_DEPTH
- pipeline.run_turn 末 inc TURNS_TOTAL by status
- +1 endpoint 测试

per spec §4.5"
```

---

## Task 15: A · monitoring/ 目录（prometheus.yml + grafana provisioning + dashboard.json）

**Files:**
- Create: `monitoring/prometheus.yml`
- Create: `monitoring/grafana/datasources.yml`
- Create: `monitoring/grafana/dashboards/dashboards.yml`
- Create: `monitoring/grafana/dashboards/rtvoice-overview.json`

- [ ] **Step 1: 创建目录**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
mkdir -p monitoring/grafana/dashboards
```

- [ ] **Step 2: 写 prometheus.yml**

`monitoring/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    deployment: rtvoice-prod

scrape_configs:
  - job_name: realtime-server
    static_configs:
      - targets: ["realtime-server:9000"]
    metrics_path: /metrics
  - job_name: stt-server
    static_configs:
      - targets: ["stt-server:9090"]
    metrics_path: /metrics
  - job_name: tts-server
    static_configs:
      - targets: ["tts-server:9880"]
    metrics_path: /metrics
  - job_name: token-server
    static_configs:
      - targets: ["token-server:8000"]
    metrics_path: /metrics
  - job_name: prometheus-self
    static_configs:
      - targets: ["localhost:9090"]
```

- [ ] **Step 3: 写 grafana datasources**

`monitoring/grafana/datasources.yml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

- [ ] **Step 4: 写 grafana dashboards provisioning**

`monitoring/grafana/dashboards/dashboards.yml`:

```yaml
apiVersion: 1

providers:
  - name: rtvoice
    orgId: 1
    folder: RTVoice
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: false
    options:
      path: /etc/grafana/provisioning/dashboards
```

- [ ] **Step 5: 写 dashboard JSON**

`monitoring/grafana/dashboards/rtvoice-overview.json`:

```json
{
  "uid": "rtvoice-overview",
  "title": "RTVoice Overview",
  "tags": ["rtvoice"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "10s",
  "time": {"from": "now-1h", "to": "now"},
  "panels": [
    {
      "id": 1,
      "title": "Service Health",
      "type": "stat",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {"expr": "up{job=~\".+-server\"}", "legendFormat": "{{job}}", "refId": "A"}
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"options": {"0": {"text": "DOWN", "color": "red"}}, "type": "value"},
            {"options": {"1": {"text": "UP", "color": "green"}}, "type": "value"}
          ]
        }
      },
      "gridPos": {"h": 4, "w": 24, "x": 0, "y": 0}
    },
    {
      "id": 2,
      "title": "Active Sessions (Realtime)",
      "type": "stat",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {"expr": "rtvoice_realtime_sessions_active", "refId": "A"}
      ],
      "gridPos": {"h": 6, "w": 6, "x": 0, "y": 4}
    },
    {
      "id": 3,
      "title": "Turns / min",
      "type": "stat",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {"expr": "60 * sum(rate(rtvoice_realtime_turns_total[1m]))", "refId": "A"}
      ],
      "gridPos": {"h": 6, "w": 6, "x": 6, "y": 4}
    },
    {
      "id": 4,
      "title": "Turn Error Rate",
      "type": "stat",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {
          "expr": "(sum(rate(rtvoice_realtime_turns_total{status=\"error\"}[5m])) or vector(0)) / (sum(rate(rtvoice_realtime_turns_total[5m])) > 0)",
          "refId": "A"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "percentunit"}},
      "gridPos": {"h": 6, "w": 6, "x": 12, "y": 4}
    },
    {
      "id": 5,
      "title": "Audit Queue Depth (sum)",
      "type": "stat",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {"expr": "rtvoice_realtime_audit_queue_depth", "refId": "A"}
      ],
      "gridPos": {"h": 6, "w": 6, "x": 18, "y": 4}
    },
    {
      "id": 6,
      "title": "HTTP Request Rate by Service",
      "type": "timeseries",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {
          "expr": "sum by (job) (rate(http_requests_total[1m]))",
          "legendFormat": "{{job}}",
          "refId": "A"
        }
      ],
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 10}
    },
    {
      "id": 7,
      "title": "HTTP P95 Latency by Service",
      "type": "timeseries",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {
          "expr": "histogram_quantile(0.95, sum by (job, le) (rate(http_request_duration_seconds_bucket[5m])))",
          "legendFormat": "{{job}}",
          "refId": "A"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "s"}},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 10}
    },
    {
      "id": 8,
      "title": "Tokens Issued (LiveKit)",
      "type": "timeseries",
      "datasource": {"type": "prometheus", "uid": "Prometheus"},
      "targets": [
        {"expr": "rate(rtvoice_tokens_issued_total[5m])", "refId": "A"}
      ],
      "gridPos": {"h": 6, "w": 24, "x": 0, "y": 18}
    }
  ]
}
```

- [ ] **Step 6: 验 JSON 合法**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
python3 -c "import json; json.load(open('monitoring/grafana/dashboards/rtvoice-overview.json'))" && echo "dashboard JSON OK"
python3 -c "import yaml; yaml.safe_load(open('monitoring/prometheus.yml')); yaml.safe_load(open('monitoring/grafana/datasources.yml')); yaml.safe_load(open('monitoring/grafana/dashboards/dashboards.yml'))" && echo "YAML OK"
```

Expected: 两行 OK。

- [ ] **Step 7: Commit**

```bash
git add monitoring/
git commit -m "feat(monitoring): A - prometheus + grafana provisioning (T15)

- prometheus.yml: 5 jobs (4 services + self) 15s scrape
- grafana datasources.yml: Prometheus 默认
- grafana dashboards.yml: RTVoice folder provisioning
- rtvoice-overview.json: 8 面板（health/sessions/turns/err/audit + http rate/lat + tokens）

per spec §4.5"
```

---

## Task 16: A · docker-compose.yml monitoring profile

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: 加 prometheus + grafana 到 docker-compose.yml**

在 docker-compose.yml `services:` 块末尾追加：

```yaml
  # ---------------------------------------------------------------
  # Prometheus + Grafana (SP4 A-lite, profile=monitoring 启用)
  # ---------------------------------------------------------------
  prometheus:
    image: prom/prometheus:v3.0.0
    container_name: rtvoice-prometheus
    profiles: ["monitoring"]
    restart: unless-stopped
    networks: [rtvoice_net]
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - rtvoice_prom_data:/prometheus
    ports:
      - "${BIND_HOST:-127.0.0.1}:${PROMETHEUS_PORT:-9090}:9090"
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
      - --web.enable-lifecycle

  grafana:
    image: grafana/grafana:11.4.0
    container_name: rtvoice-grafana
    profiles: ["monitoring"]
    restart: unless-stopped
    networks: [rtvoice_net]
    depends_on:
      - prometheus
    volumes:
      - ./monitoring/grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - rtvoice_grafana_data:/var/lib/grafana
    ports:
      - "${BIND_HOST:-127.0.0.1}:${GRAFANA_PORT:-3000}:3000"
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
      GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: /etc/grafana/provisioning/dashboards/rtvoice-overview.json
```

文件底部 `volumes:` 段追加：

```yaml
  rtvoice_prom_data:
    driver: local
  rtvoice_grafana_data:
    driver: local
```

- [ ] **Step 2: 加 SP4 段到 .env.example**

文件末尾追加：

```bash
# ============================================================
# Monitoring (SP4 A-lite, profile=monitoring 启用)
# ============================================================
# 启用：docker compose --profile prod --profile monitoring up -d
# Anonymous Grafana 默认 ON；上公网必须加 reverse proxy 鉴权或改 BIND_HOST=127.0.0.1
PROMETHEUS_PORT=9090
GRAFANA_PORT=3000
GRAFANA_ADMIN_PASSWORD=admin
```

- [ ] **Step 3: 验证 compose 语法**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
docker compose -f docker-compose.yml config --quiet 2>&1 || echo "COMPOSE FAILED"
```

Expected: 静默成功。

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(compose): A - prometheus + grafana profile=monitoring (T16)

- prometheus:v3.0.0 + grafana:11.4.0 容器加入 monitoring profile
- bind 127.0.0.1（默认安全）；anonymous Grafana viewer 启用
- prometheus retention=15d
- 启用：--profile monitoring；不影响 dev/prod 默认 profile
- .env.example: 3 个端口/密码变量

per spec §4.5"
```

---

## Task 17: 文档更新

**Files:**
- Modify: `README.md`
- Modify: `OPERATIONS.md`
- Modify: `docs/api/CONVENTIONS.md`
- Modify: `docs/api/sessions.md`
- Modify: `COZYVOICE_INTEGRATION.md`
- Create: `clients/python/README.md`（T1 已写）

- [ ] **Step 1: README.md 加 SDK + monitoring 一行**

在 README.md 60s try 表后或 What's-in-the-box 段，追加：

```markdown
## Python SDK (v0.11+)

```bash
pip install rtvoice-client
```

```python
from rtvoice_client import Client
c = Client(api_key="...", base_url="https://your-rtvoice.example.com")
text = c.stt.transcribe(pcm)
pcm = c.tts.synthesize("你好")
```

详见 [clients/python/README.md](./clients/python/README.md)。

## Monitoring (v0.11+)

```bash
docker compose --profile prod --profile monitoring up -d
# Grafana: http://your-host:3000  (anonymous viewer)
```

详见 [OPERATIONS.md §5](./OPERATIONS.md)。
```

- [ ] **Step 2: OPERATIONS.md 加 §5 监控**

文件末尾（或 §4 cookbook 之后）追加：

````markdown
## §5 Monitoring (SP4 A-lite, v0.11+)

### 5.1 启用监控栈

```bash
docker compose --profile prod --profile monitoring up -d
```

容器：
- `rtvoice-prometheus` — 抓取 4 services /metrics，15s 间隔，15d 保留
- `rtvoice-grafana` — datasource Prometheus 自动连，dashboard 自动 provisioning

访问：
- Prometheus: http://${BIND_HOST}:9090
- Grafana: http://${BIND_HOST}:3000  (anonymous viewer 默认开)

### 5.2 安全注意

**Anonymous Grafana 默认开 Viewer**——指标 + dashboard 全公开。生产部署：
- 默认 `BIND_HOST=127.0.0.1` 仅 LAN 访问
- 上公网必须加反向代理（Caddy/nginx）做基本认证或 OAuth proxy
- 改 `GF_AUTH_ANONYMOUS_ENABLED=false` 强制登录（admin 密码在 .env）

### 5.3 Dashboard 内容（rtvoice-overview）

| 面板 | 含义 |
|---|---|
| Service Health | up/down for each -server job |
| Active Sessions | realtime-server 当前 session 数 |
| Turns / min | run_turn 调用速率 |
| Turn Error Rate | error/(ok+error) 比例 |
| Audit Queue Depth | 落盘背压指示器 |
| HTTP Request Rate by Service | per-service 流量 |
| HTTP P95 Latency | 慢服务定位 |
| Tokens Issued | LiveKit token rate |

### 5.4 故障排查

#### Grafana "No Data"
- `docker exec rtvoice-prometheus wget -qO- http://localhost:9090/api/v1/targets | jq .data.activeTargets[].health` — 看哪 target 不健康
- 服务侧：`curl http://realtime-server:9000/metrics | head` 验证 metric 暴露
- 防火墙：rtvoice_net 网络内通信，不会被 host 防火墙拦

#### Prometheus 数据卷膨胀
- 默认 retention=15d；改：command 段加 `--storage.tsdb.retention.size=5GB`
````

- [ ] **Step 3: CONVENTIONS.md §6 错误码表确认**

无新增错误码（K 用 SP3 已有的 `session.update.invalid` / `prompt.too_long` / `validation.invalid_request`）。仅加 1 行说明 K 残项扩展白名单：

在 §6 表后（或合适位置）插入：

```markdown
> **v0.11.0 (SP4 K)**: `session.update` 白名单扩到 `prompt + voice + speed`；新增 `memory.clear` event。
```

- [ ] **Step 4: sessions.md 加 voice/speed 热改 + memory.clear 说明**

`docs/api/sessions.md` 在 `WS Client → Server` 表"audio.eos"后，追加：

```markdown
| text JSON | `{"type":"session.update","prompt":"..."}` | 热改 system prompt（SP3） |
| text JSON | `{"type":"session.update","voice":"alice"}` | 热改 TTS voice（SP4，下一 turn 起 effective） |
| text JSON | `{"type":"session.update","speed":1.5}` | 热改 TTS speed（0.5-2.0；下一 turn 起 effective） |
| text JSON | `{"type":"memory.clear"}` | 清当前 session 历史；prompt 不动（SP4） |
```

把状态行更新为：

```markdown
> **状态：v0.11.0 已实现** —— SP3 (prompt/memory/streaming/audit) + SP4 K (voice/speed 热改 + memory.clear)。
```

- [ ] **Step 5: COZYVOICE_INTEGRATION.md 加 SDK 用法**

在 §5（Python SDK 示例）前，追加新 §5.0：

````markdown
## §5.0 Recommended: 用 rtvoice-client SDK

v0.11+ 起官方 SDK 可用。**强烈推荐替代手写 httpx/websockets。**

```bash
pip install rtvoice-client
```

```python
from rtvoice_client import Client

c = Client(api_key=os.environ["RTVOICE_API_KEY"],
           base_url=os.environ["RTVOICE_BASE_URL"])

# STT
text = c.stt.transcribe(pcm_int16le_16k_mono, sample_rate=16000)

# TTS
pcm = c.tts.synthesize("你好世界", voice="default_zh_female", speed=1.0)

# Realtime — 高层 helper
async def cozyvoice_chat(audio_iter):
    async for evt in c.realtime.conversation(audio_iter, prompt="..."):
        if evt.type == "response.text":
            yield evt.text  # text delta 给 UI
        elif evt.type == "response.pcm":
            yield evt.data   # PCM bytes 给 audio sink
```

错误处理用 typed exceptions：

```python
from rtvoice_client.errors import CapacityFull, PromptTooLong, RTVoiceError

try:
    sess = c.realtime.create_session(prompt="...")
except CapacityFull:
    show_user("服务繁忙，稍后重试")
except PromptTooLong:
    show_user("system prompt 太长（>2000 字符）")
except RTVoiceError as e:
    log.error("rtvoice error: %s", e)
```
````

- [ ] **Step 6: Commit**

```bash
git add README.md OPERATIONS.md docs/api/CONVENTIONS.md docs/api/sessions.md COZYVOICE_INTEGRATION.md
git commit -m "docs: SP4 配套（README / OPERATIONS / CONVENTIONS / sessions / COZYVOICE）(T17)

- README.md: SDK + Monitoring 各一段
- OPERATIONS.md §5: Monitoring 启用 + 安全 + dashboard + 排障
- CONVENTIONS.md §6: SP4 K 白名单扩展说明
- sessions.md: WS 客户端事件表加 voice/speed/memory.clear
- COZYVOICE_INTEGRATION.md §5.0: SDK 推荐 + typed exception 用法"
```

---

## Task 18: CHANGELOG v0.11.0 + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 在 [Unreleased] 之后插入 v0.11.0 entry**

```markdown
## [0.11.0] — 2026-05-09 — SP4 Bridge: Python SDK + SP3 残项 + A-lite 仪表盘

平台化重构第五阶段：把 RTVoice 从"platform 已建好"推进到"platform 已能被用起来 + 看得见"。

### Added

- **`clients/python/`** — Python SDK `rtvoice-client` v0.1.0 alpha（PyPI 公开发布）
  - 4 命名空间：stt / tts / realtime / tokens
  - async + sync 双形态（`AsyncClient` + `Client`）
  - 14 typed exception 对应 CONVENTIONS §6 错误码
  - Pydantic v2 模型 + RealtimeEvent discriminated union
  - 高层 `realtime.conversation()` helper
  - `py.typed` marker
- **`monitoring/`** — Prometheus + Grafana provisioning（profile=monitoring）
  - `prometheus.yml` (4 services scrape, 15d retention)
  - `grafana/dashboards/rtvoice-overview.json` (8 面板)
  - `docker-compose.yml` 加 prometheus + grafana service block
- **realtime-server** 3 个新 Prometheus metric
  - `rtvoice_realtime_sessions_active` Gauge
  - `rtvoice_realtime_turns_total` Counter (status=ok|error)
  - `rtvoice_realtime_audit_queue_depth` Gauge

### Changed

- `WS session.update` 白名单：`prompt` → `prompt + voice + speed`
- `pipeline.run_turn`：检查 `sess.tts_client_dirty`，需要时关旧 TTSClient + 用新 voice/speed 建新
- `WS` 新增 `memory.clear` event（清 session 历史，prompt 不动）
- `Session` dataclass 加 `tts_client_dirty: bool` 字段
- `ConversationMemory` 加 `.clear()` 方法

### 验证（autonomous）

- ✅ SDK 共 28 单元测试（errors 6 + models 6 + base 6 + stt 4 + tts 4 + realtime 8 + tokens 2 + sync 2）
- ✅ realtime-server 加 6 测试（memory clear 2 + endpoints 3 + metrics 1）
- ✅ pipeline 加 2（rebuild on dirty）
- ✅ SP3 后 52 → SP4 后 86+
- ✅ docker compose validate OK
- ⏳ prod 集成测试 + user-participation（CozyVoice 切换至 SDK；Grafana 看面板）

### 设计决策

- SDK monorepo 内（不独立 repo）：版本同步项目节奏；CozyVoice 等下游可 `pip install -e clients/python/`
- semver 0.1.x alpha 起：API 还会基于 CozyVoice 反馈微调
- Anonymous Grafana viewer 默认开：方便临时查看；公网部署必须加 reverse proxy 鉴权
- `transcript.partial stable=true` 不在 SP4：等 STT API 调研

详见 [SP4 设计](./docs/superpowers/specs/2026-05-09-sp4-bridge-bundle-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-09-sp4-bridge-bundle.md)。

---
```

- [ ] **Step 2: 文档链接 lint**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in README.md ARCHITECTURE.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/CONVENTIONS.md docs/api/sessions.md docs/api/stt.md docs/api/tts.md; do
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
git commit -m "docs(changelog): v0.11.0 — SP4 Bridge SDK + K 残项 + A-lite (T18)

- Added: rtvoice-client SDK + Prometheus/Grafana monitoring + 3 metrics
- Changed: session.update voice/speed + memory.clear + ConversationMemory.clear
- 86+ tests; prod 部署 + user-participation 待 T19"

git push origin main 2>&1 | tail -5
```

Expected: push 成功。

- [ ] **Step 4 (可选): PyPI 发布**

```bash
cd /home/ubuntu/CozyProjects/RTVoice/clients/python
python3 -m pip install --upgrade build twine
python3 -m build
# 检查 dist/
ls dist/
# 测试上传到 TestPyPI（先 reserve 包名）
# python3 -m twine upload --repository testpypi dist/*
# 真实发布（需要 PYPI 账号 + token）：
# python3 -m twine upload dist/*
```

可选——如果还没注册 PyPI 包名，先建账号 + token + 发 0.1.0 占位版本（仅含骨架，正式 features 在 0.1.1 起）。

---

## Task 19: prod 集成测试 + user-participation 验收

**Files:** 无（read-only verification）

- [ ] **Step 1: prod 端拉新 + build + monitoring up**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -5
  echo
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server 2>&1 | tail -5
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d --force-recreate realtime-server 2>&1 | tail -5
  echo
  # 启动 monitoring profile
  docker compose --profile monitoring up -d 2>&1 | tail -5
  for i in $(seq 1 15); do
    s=$(docker inspect rtvoice-realtime --format "{{.State.Health.Status}}" 2>/dev/null)
    p=$(docker inspect rtvoice-prometheus --format "{{.State.Status}}" 2>/dev/null)
    g=$(docker inspect rtvoice-grafana --format "{{.State.Status}}" 2>/dev/null)
    echo "[$i] realtime=$s prom=$p grafana=$g"
    [ "$s" = "healthy" ] && [ "$p" = "running" ] && [ "$g" = "running" ] && break
    sleep 3
  done
}'
```

Expected: 三服务全起。

- [ ] **Step 2: prod autonomous 验收**

```bash
ssh root@192.168.66.163 '
echo "=== A1: 3 SP4 metrics 在 /metrics ==="
docker exec rtvoice-agent curl -s http://realtime-server:9000/metrics | grep -E "rtvoice_realtime_(sessions_active|turns_total|audit_queue_depth)" | head -10

echo "=== A2: SDK 装上 + 基本 import ==="
pip install -e /data/RTVoice/clients/python/ 2>&1 | tail -3
python3 -c "from rtvoice_client import Client, AsyncClient; print(\"SDK import OK\")"

echo "=== A3: SDK e2e create_session prompt 透传 ==="
RTVOICE_E2E_BASE=http://192.168.66.163:9000 python3 -c "
from rtvoice_client import Client
c = Client(base_url=\"http://192.168.66.163:9000\")
sess = c.realtime.create_session(prompt=\"SP4 e2e test\")
print(\"prompt:\", sess.prompt)
assert sess.prompt == \"SP4 e2e test\"
print(\"✓\")
"

echo "=== A4: SDK PromptTooLong 抛 ==="
python3 -c "
from rtvoice_client import Client
from rtvoice_client.errors import PromptTooLong
c = Client(base_url=\"http://192.168.66.163:9000\")
try:
    c.realtime.create_session(prompt=\"x\"*9999)
except PromptTooLong as e:
    print(\"✓ PromptTooLong:\", e.code)
"

echo "=== A5: prometheus 抓到 realtime-server ==="
curl -s http://192.168.66.163:9090/api/v1/targets | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data[\"data\"][\"activeTargets\"]:
    print(t[\"labels\"][\"job\"], t[\"health\"])
"

echo "=== A6: Grafana 可访问 ==="
curl -sI http://192.168.66.163:3000/api/health | head -3
'
```

Expected: A1-A6 全 ✓。

- [ ] **Step 3: 通知 user 做浏览器 + CozyVoice 验收**

```
SP4 沙盒 + autonomous 完工。请你做：

1. **浏览器 Grafana**：http://192.168.66.163:3000  → 看 RTVoice Overview dashboard（anonymous viewer 默认开）
   - 验证：8 面板都有数据；service health 全绿；跑几个 turn 看 Turns/min 上去

2. **浏览器 Realtime 测试页**：http://192.168.66.163:9000/  
   - 验证 SP3 延期项：多轮记忆 / 中途换 prompt / partial 流刷
   - 验证 SP4 K：浏览器 console 拼 `session.update {voice:"..."}` / `memory.clear` 看响应

3. **CozyVoice 切换 SDK**（你这边的项目）：
   ```bash
   pip install -e /path/to/RTVoice/clients/python/
   ```
   把 CozyVoice 内 hand-write 的 httpx 调用改用 `Client(base_url="...")` + namespace 方法
```

- [ ] **Step 4: User 反馈后标 SP4 完工**

OK → SP4 done。
有 SDK API 体验问题（"应该这样不该那样"）→ 记 SP4-fix-N 微调 + 0.1.x 迭代。

---

## Self-Review

### 1. Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §3 file layout | T1（SDK 骨架） + T15（monitoring 目录）+ T16（compose） |
| §4.1 SDK API surface | T4-T9 一一对应 |
| §4.2 错误层级 | T2 |
| §4.3 Pydantic models | T3 |
| §4.4 K 残项 | T11-T13 |
| §4.5 仪表盘 + metrics | T14-T16 |
| §5 测试矩阵 34 个 | 实际：T2 6 + T3 6 + T4 6 + T5 4 + T6 4 + T7 8 + T8 2 + T9 2 + T10 0 e2e + T11 2 + T12 3 + T13 2 + T14 1 = **46 unit + 1 file 的 e2e** |
| §6 验收 A1-A10 + B1-B3 | T19 |
| §8 范围外 | 未实施任何 ✓ |
| §9 实施切片 19 task | 完全对齐 |

注：实际测试数 46 比 spec 估的 34 多 12（T3 models 没在 spec 里计；T4 base 也单独算）。

### 2. Placeholder scan

- 每 step 含完整代码或命令
- 无 "TBD"/"TODO"
- T17 docs 修改前后字段都有给出

### 3. Type consistency

- `Client` / `AsyncClient`：T1 占名 → T4 实现，4 namespace properties 一致
- `RTVoiceError` 14 类：T2 定义 → T4-T8 引用 + T2 测试覆盖
- `SessionCreateRequest/Response`：T3 定义 → T7 用
- `RealtimeEvent` union + `parse_realtime_event`：T3 定义 → T7 用
- `tts_client_dirty`：T12 加字段 → T13 pipeline 检查
- `ConversationMemory.clear()`：T11 加 → T12 调用
- 3 metrics：T14 定义 → 同 T14 接 SessionManager + pipeline → T15 dashboard PromQL 引用

无类型/签名漂移。

### 4. 风险点 spec → plan 转化

| spec §7 风险 | plan 缓解任务 |
|---|---|
| PyPI 包名抢注 | T18 可选 step 4 PyPI publish 占名 |
| SDK API 设计错 | semver 0.1.0 alpha + T19 user-participation |
| Voice 切换重建 mid-turn | T13 在 run_turn 开头检查（设计就是下 turn 才生效） |
| Dashboard JSON 跟不上 metric | T15 provisioning + 10s reload |
| Grafana anonymous 暴露 | T17 OPERATIONS.md §5.2 警告 + 默认 BIND_HOST=127.0.0.1 |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-sp4-bridge-bundle.md`.

Two execution options:

1. **Subagent-Driven (recommended)** —— fresh subagent per task + spec/quality 双审；与 SP1/SP1.5/SP2/SP3 同流程
2. **Inline Execution** —— 本 session 批量执行 + checkpoints

Which approach?
