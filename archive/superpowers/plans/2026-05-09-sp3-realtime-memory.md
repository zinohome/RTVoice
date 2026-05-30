# SP3 Realtime Voice — Prompt + Memory + Streaming + Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 SP2 的"单 turn 无记忆"voice loop 升级为"prompt + 滑动窗口 memory + 流式 transcript/response + 异步 audit JSONL + mid-session 改 prompt"的完整 Realtime Voice。

**Architecture:** 升级现有 `services/realtime-server/`（不新增 docker service）；新增 2 个工具模块（memory.py / audit.py）；修改 6 个文件（config / session_manager / pipeline / main / llm_client / docker-compose）；零新依赖（asyncio.Queue + asyncio.to_thread + collections.deque）。

**Tech Stack:** FastAPI / asyncio / collections.deque / openai SDK（接受 messages）/ websockets / pytest-asyncio

**Spec:** [docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md](../specs/2026-05-09-sp3-realtime-memory-design.md)

---

## Task 1: config.py — 加 SP3 env vars

**Files:**
- Modify: `services/realtime-server/app/config.py`
- Modify: `services/realtime-server/tests/test_config.py`

- [ ] **Step 1: 写新测试**

`services/realtime-server/tests/test_config.py` 文件末尾追加：

```python
def test_sp3_defaults(monkeypatch):
    """SP3 新增 env vars 默认值（RTX 3060 调优）"""
    for k in [
        "RTVOICE_MEMORY_MAX_TURNS",
        "RTVOICE_DEFAULT_PROMPT",
        "RTVOICE_AUDIT_DIR",
        "RTVOICE_AUDIT_QUEUE_MAX",
        "RTVOICE_PROMPT_MAX_CHARS",
    ]:
        monkeypatch.delenv(k, raising=False)
    import importlib, sys
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    from app import config
    assert config.MEMORY_MAX_TURNS == 6
    assert config.DEFAULT_PROMPT == "你是语音助手。用中文简短回答（≤2 句）。"
    assert config.AUDIT_DIR == "/data/transcripts"
    assert config.AUDIT_QUEUE_MAX == 1000
    assert config.PROMPT_MAX_CHARS == 2000
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_config.py::test_sp3_defaults -v
```

Expected: FAIL with `AttributeError: module 'app.config' has no attribute 'MEMORY_MAX_TURNS'`.

- [ ] **Step 3: 在 config.py 加 5 行**

定位 `services/realtime-server/app/config.py` 中 `# Voice defaults` 段之上（约 36-43 行）插入：

```python
# SP3 — Memory + Prompt + Audit
MEMORY_MAX_TURNS = _int("RTVOICE_MEMORY_MAX_TURNS", 6)
DEFAULT_PROMPT = _str("RTVOICE_DEFAULT_PROMPT", "你是语音助手。用中文简短回答（≤2 句）。")
AUDIT_DIR = _str("RTVOICE_AUDIT_DIR", "/data/transcripts")
AUDIT_QUEUE_MAX = _int("RTVOICE_AUDIT_QUEUE_MAX", 1000)
PROMPT_MAX_CHARS = _int("RTVOICE_PROMPT_MAX_CHARS", 2000)
```

并在 `log_summary()` 内追加：

```python
    logger.info("SP3: MEMORY_MAX_TURNS=%d AUDIT_DIR=%s PROMPT_MAX_CHARS=%d",
                MEMORY_MAX_TURNS, AUDIT_DIR, PROMPT_MAX_CHARS)
```

- [ ] **Step 4: 跑测试**

```bash
cd services/realtime-server
python3 -m pytest tests/test_config.py -v
```

Expected: 3 passed（含原有 2 个）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/config.py services/realtime-server/tests/test_config.py
git commit -m "feat(realtime-server): config.py 加 SP3 env vars (T1)

- RTVOICE_MEMORY_MAX_TURNS=6
- RTVOICE_DEFAULT_PROMPT=你是语音助手...
- RTVOICE_AUDIT_DIR=/data/transcripts
- RTVOICE_AUDIT_QUEUE_MAX=1000
- RTVOICE_PROMPT_MAX_CHARS=2000

per spec D-2026-05-09-A.2/A.4/A.6"
```

---

## Task 2: memory.py — 滑动窗口工具

**Files:**
- Create: `services/realtime-server/app/memory.py`
- Create: `services/realtime-server/tests/test_memory.py`

- [ ] **Step 1: 写测试**

`services/realtime-server/tests/test_memory.py`:

```python
"""Test ConversationMemory: deque-based sliding window."""
from app.memory import ConversationMemory


def test_empty_starts_zero():
    m = ConversationMemory(max_turns=3)
    assert list(m) == []
    assert len(m) == 0


def test_append_pair_grows_two_messages():
    m = ConversationMemory(max_turns=3)
    m.append_turn("hi", "hello")
    msgs = list(m)
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert len(m) == 2


def test_evicts_oldest_pair_when_full():
    m = ConversationMemory(max_turns=2)
    m.append_turn("u1", "a1")
    m.append_turn("u2", "a2")
    m.append_turn("u3", "a3")  # 该驱逐 u1/a1
    msgs = list(m)
    assert msgs == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]


def test_assistant_text_truncated_at_size_cap():
    m = ConversationMemory(max_turns=3, assistant_max_chars=10)
    m.append_turn("u", "a" * 50)
    msgs = list(m)
    assert msgs[1]["content"] == "a" * 10
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_memory.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.memory'`.

- [ ] **Step 3: 写 memory.py**

`services/realtime-server/app/memory.py`:

```python
"""ConversationMemory: 滑动窗口对话历史 (per spec D-2026-05-09-A.2)."""
from __future__ import annotations
from collections import deque
from collections.abc import Iterator


class ConversationMemory:
    """N 轮 user/assistant 滑动窗口；每轮 = 2 条消息（user + assistant）。

    用法：
        m = ConversationMemory(max_turns=6)
        m.append_turn(user_text, assistant_text)
        messages = [{"role":"system","content":prompt}, *list(m), {"role":"user","content":new_text}]
    """

    def __init__(self, max_turns: int = 6, assistant_max_chars: int = 4000) -> None:
        self._maxlen = max_turns * 2  # user+assistant 成对
        self._buf: deque = deque(maxlen=self._maxlen)
        self._assistant_max_chars = assistant_max_chars

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        """成对 append；deque maxlen 自动驱逐最早 2 条（保持成对）。

        assistant_text 超 cap 截断（防 LLM payload 爆炸）；user 不截（STT 长度受说话时长限制）。
        """
        self._buf.append({"role": "user", "content": user_text})
        clipped = assistant_text[: self._assistant_max_chars]
        self._buf.append({"role": "assistant", "content": clipped})

    def __iter__(self) -> Iterator[dict]:
        return iter(self._buf)

    def __len__(self) -> int:
        return len(self._buf)
```

- [ ] **Step 4: 跑测试看 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_memory.py -v
```

Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/memory.py services/realtime-server/tests/test_memory.py
git commit -m "feat(realtime-server): memory.py 滑动窗口对话历史 (T2)

- ConversationMemory 包装 collections.deque
- maxlen=2*max_turns 自动驱逐最早一对
- assistant_max_chars=4000 截断防 LLM payload 爆炸
- 4 单元测试覆盖：empty/append/evict/truncate

per spec D-2026-05-09-A.2"
```

---

## Task 3: audit.py — 异步 JSONL writer

**Files:**
- Create: `services/realtime-server/app/audit.py`
- Create: `services/realtime-server/tests/test_audit.py`

- [ ] **Step 1: 写测试**

`services/realtime-server/tests/test_audit.py`:

```python
"""Test AuditWriter: async JSONL append, queue full drop, IO error swallow."""
import asyncio
import json
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_writes_jsonl_lines(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_abc", str(tmp_path), queue_max=100)
    await w.write({"event": "transcript.final", "text": "hi"})
    await w.write({"event": "response.done", "text": "hello"})
    await w.aclose()
    # path 应在 {tmp_path}/{YYYY-MM-DD}/sess_abc.jsonl
    files = list(tmp_path.rglob("sess_abc.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["event"] == "transcript.final"
    assert "ts" in e1
    assert e1["text"] == "hi"


@pytest.mark.asyncio
async def test_queue_full_drops_event(tmp_path):
    from app.audit import AuditWriter
    w = AuditWriter("sess_qf", str(tmp_path), queue_max=2)
    # 把 _loop 暂停（hold the asyncio.Queue.get await），手动塞满
    # 简化：直接调 put_nowait 多次
    await w.write({"event": "e1"})
    await w.write({"event": "e2"})
    # 此时 queue ≤ 2；第 3 条可能在 _loop 已 drain 后能成功
    # 测策略：填很多，aclose 后看文件至少有一些（不抛异常）
    for i in range(50):
        await w.write({"event": f"e{i}"})
    await w.aclose()
    files = list(tmp_path.rglob("sess_qf.jsonl"))
    assert len(files) == 1  # 文件存在，部分事件落盘


@pytest.mark.asyncio
async def test_aclose_drains_pending(tmp_path):
    """aclose 前 put 的事件，aclose 后应该都已落盘."""
    from app.audit import AuditWriter
    w = AuditWriter("sess_drain", str(tmp_path))
    for i in range(10):
        await w.write({"event": f"e{i}"})
    await w.aclose()
    files = list(tmp_path.rglob("sess_drain.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 10


@pytest.mark.asyncio
async def test_dir_unwritable_swallows_error(tmp_path, monkeypatch, caplog):
    """audit dir 不可写时 write() 不抛；warn log 即可."""
    from app.audit import AuditWriter
    bad = tmp_path / "nonexistent" / "deep"
    # 让 mkdir 失败：用一个文件占位
    f = tmp_path / "blocked"
    f.write_text("x")
    w = AuditWriter("sess_e", str(f / "sub"), queue_max=10)  # f 是文件，不能 mkdir 子目录
    await w.write({"event": "x"})  # 不应抛
    await w.aclose()


@pytest.mark.asyncio
async def test_path_uses_session_creation_date(tmp_path, monkeypatch):
    """audit path 用 session 创建日期，全程一个文件."""
    from app.audit import AuditWriter
    w = AuditWriter("sess_d", str(tmp_path))
    p = w.path
    # path 形如 {tmp_path}/2026-05-09/sess_d.jsonl
    assert p.parent.parent == tmp_path
    assert len(p.parent.name) == 10  # YYYY-MM-DD
    assert p.name == "sess_d.jsonl"
    await w.aclose()
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_audit.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.audit'`.

- [ ] **Step 3: 写 audit.py**

`services/realtime-server/app/audit.py`:

```python
"""AuditWriter: per-session 异步 JSONL writer (per spec §5.3)."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("rtvoice.realtime.audit")


class AuditWriter:
    """异步 append-only JSONL；turn 永不阻塞。

    路径：{base_dir}/{YYYY-MM-DD}/{session_id}.jsonl
    日期取自构造时刻，全程一个文件（即便 session 跨 0 点）。
    """

    def __init__(self, session_id: str, base_dir: str, queue_max: int = 1000) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.path = Path(base_dir) / date / f"{session_id}.jsonl"
        self._dir_ok = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.warning("audit dir mkdir failed for %s: %s", self.path, e)
            self._dir_ok = False
        self._q: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._closed = False
        self._task: asyncio.Task = asyncio.create_task(self._loop())

    async def write(self, event: dict) -> None:
        """O(1) 微秒级；queue full 直接 drop + warn。"""
        if self._closed or not self._dir_ok:
            return
        item = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **event}
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("audit queue full for %s, dropping %s",
                        self.path.name, item.get("event"))

    async def _loop(self) -> None:
        while True:
            try:
                first = await self._q.get()
            except asyncio.CancelledError:
                return
            batch = [first]
            # opportunistic batching：drain up to 50 ready items
            for _ in range(49):
                try:
                    batch.append(self._q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await asyncio.to_thread(self._flush_sync, batch)
            except Exception:
                log.exception("audit flush failed for %s", self.path.name)

    def _flush_sync(self, batch: list[dict]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for item in batch:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def aclose(self) -> None:
        """停接收 + drain 剩余 + cancel writer task."""
        if self._closed:
            return
        self._closed = True
        # drain：等 queue 空 + 给 _loop 时间 batch flush
        # 简单做法：sleep + cancel；实际可循环 wait queue.empty
        for _ in range(20):
            if self._q.empty():
                # 还有可能 batch 还没 flush 完；让出一次
                await asyncio.sleep(0.01)
                if self._q.empty():
                    break
            await asyncio.sleep(0.05)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: 跑测试看 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_audit.py -v
```

Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/audit.py services/realtime-server/tests/test_audit.py
git commit -m "feat(realtime-server): audit.py 异步 JSONL writer (T3)

- AuditWriter: asyncio.Queue + asyncio.to_thread IO；turn 不阻塞
- path: {AUDIT_DIR}/{YYYY-MM-DD}/{session_id}.jsonl，session 创建日期
- batch flush（≤50 events 一次 open+write+close）
- queue full → drop + warn；dir mkdir 失败 → 全程 no-op
- aclose drain pending；5 单元测试

per spec D-2026-05-09-A.4 + §5.3"
```

---

## Task 4: session_manager.py — 扩字段 prompt / memory / audit_writer

**Files:**
- Modify: `services/realtime-server/app/session_manager.py`
- Modify: `services/realtime-server/tests/test_session_manager.py`

- [ ] **Step 1: 写新测试**

`services/realtime-server/tests/test_session_manager.py` 文件末尾追加：

```python
@pytest.mark.asyncio
async def test_create_with_prompt(monkeypatch):
    monkeypatch.setattr("app.config.MAX_CONCURRENT_SESSIONS", 5)
    from app import session_manager
    mgr = session_manager.SessionManager()
    sess = await mgr.create("h", "v", 1.0, prompt="hello world", audit_persist=False)
    assert sess.prompt == "hello world"
    assert sess.audit_persist is False
    assert sess.audit_writer is None
    # memory 是 ConversationMemory 实例
    from app.memory import ConversationMemory
    assert isinstance(sess.memory, ConversationMemory)


@pytest.mark.asyncio
async def test_create_with_audit_persist_creates_writer(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.MAX_CONCURRENT_SESSIONS", 5)
    monkeypatch.setattr("app.config.AUDIT_DIR", str(tmp_path))
    from app import session_manager
    mgr = session_manager.SessionManager()
    sess = await mgr.create("h", "v", 1.0, prompt="x", audit_persist=True)
    from app.audit import AuditWriter
    assert isinstance(sess.audit_writer, AuditWriter)
    await mgr.cleanup(sess.id, "test")  # 清理后台 task


@pytest.mark.asyncio
async def test_cleanup_aclose_audit_writer(tmp_path, monkeypatch):
    """cleanup 时 audit_writer.aclose() 必须被调用."""
    monkeypatch.setattr("app.config.MAX_CONCURRENT_SESSIONS", 5)
    monkeypatch.setattr("app.config.AUDIT_DIR", str(tmp_path))
    from app import session_manager
    mgr = session_manager.SessionManager()
    sess = await mgr.create("h", "v", 1.0, prompt="x", audit_persist=True)
    aw = sess.audit_writer
    await sess.audit_writer.write({"event": "test"})
    await mgr.cleanup(sess.id, "test")
    assert aw._closed is True
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_session_manager.py -k "with_prompt or audit_persist or aclose_audit" -v
```

Expected: FAIL（create() 不接受新参数等）。

- [ ] **Step 3: 改 session_manager.py**

修改 `services/realtime-server/app/session_manager.py`:

3a. 文件顶部 import 段（约 9-11 行后）追加：
```python
from app.memory import ConversationMemory
from app.audit import AuditWriter
```

3b. `Session` dataclass 加字段（约 23-37 行）。把第 23 行 `@dataclass` 块改成：
```python
@dataclass
class Session:
    id: str
    creator_key_hash: str
    voice: str
    speed: float
    created_at: datetime
    expires_at: datetime
    state: SessionState = "CREATED"
    ws: Any = None
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_turn_task: Optional[asyncio.Task] = None
    stt_client: Any = None
    llm_client: Any = None
    tts_client: Any = None
    # SP3 fields
    prompt: str = ""
    memory: Any = None             # ConversationMemory 实例
    audit_persist: bool = False
    audit_writer: Any = None       # AuditWriter 实例（仅 audit_persist=True 时）
```

3c. `create()` 签名加 prompt + audit_persist；初始化 memory / audit_writer。把 `async def create(...)` 整体替换为：
```python
    async def create(
        self,
        creator_key_hash: str,
        voice: str,
        speed: float,
        prompt: str = "",
        audit_persist: bool = False,
    ) -> Session:
        async with self._capacity_lock:
            if self.active_count() >= config.MAX_CONCURRENT_SESSIONS:
                raise CapacityFull(
                    f"max {config.MAX_CONCURRENT_SESSIONS} concurrent sessions"
                )
            now = _now()
            sess = Session(
                id=_new_session_id(),
                creator_key_hash=creator_key_hash,
                voice=voice,
                speed=speed,
                created_at=now,
                expires_at=now + timedelta(seconds=config.SESSION_MAX_LIFETIME_S),
                last_activity=now,
                prompt=prompt,
                memory=ConversationMemory(max_turns=config.MEMORY_MAX_TURNS),
                audit_persist=audit_persist,
            )
            if audit_persist:
                sess.audit_writer = AuditWriter(
                    sess.id,
                    base_dir=config.AUDIT_DIR,
                    queue_max=config.AUDIT_QUEUE_MAX,
                )
            self._sessions[sess.id] = sess
            log.info("session created: id=%s voice=%s speed=%.2f audit=%s expires=%s",
                     sess.id, sess.voice, sess.speed, audit_persist,
                     sess.expires_at.isoformat())
            return sess
```

3d. `cleanup()` 内（在 close clients 循环前后）加 audit_writer 关闭。在第 114 行（`for c in (sess.stt_client, ...)`）之前插入：
```python
        if sess.audit_writer is not None:
            try:
                await sess.audit_writer.aclose()
            except Exception:
                log.exception("audit_writer.aclose failed for %s", session_id)
```

- [ ] **Step 4: 跑测试看 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_session_manager.py -v
```

Expected: 14 passed（11 旧 + 3 新）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/session_manager.py services/realtime-server/tests/test_session_manager.py
git commit -m "feat(realtime-server): Session 加 prompt / memory / audit (T4)

- Session dataclass 新字段：prompt, memory(ConversationMemory),
  audit_persist, audit_writer(AuditWriter or None)
- create(): 加 prompt + audit_persist 参数；audit_persist=True 时创 writer
- cleanup(): 关 audit_writer.aclose() 进 drain pending events
- +3 单元测试（with_prompt / audit_persist / cleanup_aclose）

per spec §5.1"
```

---

## Task 5: llm_client.py — 改签名 stream(messages)

**Files:**
- Modify: `services/realtime-server/app/llm_client.py`
- Create: `services/realtime-server/tests/test_llm_client.py`

- [ ] **Step 1: 写测试**

`services/realtime-server/tests/test_llm_client.py`:

```python
"""Test LLMClient.stream(messages) signature 改造（SP3 D-2026-05-09-A.5）."""
import asyncio
import inspect
import pytest


def test_stream_signature_takes_messages_list():
    """stream() 第一参数应该叫 messages，类型是 list；废弃 user_text 旧签名."""
    from app.llm_client import LLMClient
    sig = inspect.signature(LLMClient.stream)
    params = list(sig.parameters.keys())
    # self + messages
    assert params[0] == "self"
    assert params[1] == "messages"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_llm_client.py -v
```

Expected: FAIL（params[1] 是 `user_text`）。

- [ ] **Step 3: 改 llm_client.py**

修改 `services/realtime-server/app/llm_client.py`:

3a. 删除 `_raw_stream` 内 messages 组装；签名改为接受 messages：

把 `_raw_stream(self, user_text: str)` 整体替换为：
```python
    async def _raw_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
```

3b. 把 `stream(self, user_text: str)` 整体替换为：
```python
    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """yield delta 字符串；保证至少有一段输出避免沉默。

        messages 由 caller 组装：[{role:system,...}, ...history, {role:user,...}]。
        失败模式：与 SP2 同（cancel re-raise / 半句中止 / 0-token fallback）。
        """
        # log first user msg 内容（最后一条 user role）
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        log.info("[LLM] user=%r (msgs=%d)", last_user, len(messages))
        emitted = 0
        full: list[str] = []
        try:
            async for delta in self._raw_stream(messages):
                emitted += 1
                full.append(delta)
                yield delta
        except asyncio.CancelledError:
            log.info("[LLM] stream cancelled (emitted=%d)", emitted)
            raise
        except Exception as e:
            log.warning("[LLM] stream 异常 emitted=%d: %s", emitted, e)
            if emitted > 0:
                log.info("[LLM] 半句中止；reply_so_far=%r", "".join(full))
                return
        if emitted == 0:
            log.warning("[LLM] 0 token emitted → 发 fallback %r", self.fallback_reply)
            yield self.fallback_reply
        else:
            log.info("[LLM] reply=%r", "".join(full))
```

3c. `LLMClient.__init__` 删除 `system_prompt` 参数及相关字段（pipeline 现在自己组 system msg）：

把构造方法签名改为：
```python
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "ollama",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.6,
        connect_timeout_s: float = LLM_CONNECT_TIMEOUT_S,
        read_timeout_s: float = LLM_READ_TIMEOUT_S,
        fallback_reply: str = LLM_FALLBACK_REPLY,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.fallback_reply = fallback_reply
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=10.0,
                pool=5.0,
            ),
        )
```

并删除文件顶部的 `DEFAULT_SYSTEM_PROMPT` 与 `SYSTEM_PROMPT` 常量（不再需要）。

- [ ] **Step 4: 跑测试看 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_llm_client.py -v
```

Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/llm_client.py services/realtime-server/tests/test_llm_client.py
git commit -m "feat(realtime-server): LLMClient.stream(messages) 改签名 (T5)

- stream(messages: list[dict])，pipeline 负责组 [system, ...history, user]
- 删除 LLMClient.system_prompt 字段（SP3 prompt 由 sess 维护）
- 保留 fallback / cancel re-raise / 半句中止 行为
- +1 签名测试

per spec D-2026-05-09-A.5"
```

---

## Task 6: pipeline.py — 重写 run_turn

**Files:**
- Modify: `services/realtime-server/app/pipeline.py`
- Modify: `services/realtime-server/tests/test_pipeline_mock.py`

- [ ] **Step 1: 扩 mock 类 + 写新测试**

修改 `services/realtime-server/tests/test_pipeline_mock.py`：

1a. 把 `_make_session()` 改成支持 SP3 字段：
```python
def _make_session(prompt="sys", audit_writer=None):
    from app.session_manager import Session
    from app.memory import ConversationMemory
    from datetime import datetime, timezone
    return Session(
        id="sess_test123",
        creator_key_hash="h",
        voice="default_zh_female",
        speed=1.0,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        prompt=prompt,
        memory=ConversationMemory(max_turns=3),
        audit_persist=audit_writer is not None,
        audit_writer=audit_writer,
    )
```

1b. 改 `FakeLLMClient.stream` 接 messages（不是 text）：
```python
class FakeLLMClient:
    def __init__(self, deltas=None):
        self.deltas = deltas or ["你好", "世界"]
        self.last_messages = None

    async def stream(self, messages):
        self.last_messages = messages
        for d in self.deltas:
            yield d
```

1c. 加 `FakeAuditWriter`：
```python
class FakeAuditWriter:
    def __init__(self):
        self.events = []
        self._closed = False
    async def write(self, event):
        self.events.append(event)
    async def aclose(self):
        self._closed = True
```

1d. 在文件末尾追加 5 个新测试：
```python
@pytest.mark.asyncio
async def test_run_turn_builds_messages_with_prompt_and_history():
    """messages = [system(prompt), ...memory, {user:final_text}]"""
    from app.pipeline import run_turn
    sess = _make_session(prompt="你是助手")
    sess.memory.append_turn("u_old", "a_old")
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    msgs = sess.llm_client.last_messages
    assert msgs[0] == {"role": "system", "content": "你是助手"}
    assert msgs[1] == {"role": "user", "content": "u_old"}
    assert msgs[2] == {"role": "assistant", "content": "a_old"}
    assert msgs[3] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_run_turn_emits_response_text_per_delta():
    """每个 LLM delta → 一条 response.text 事件."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient(deltas=["A", "B", "C"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    rtx = [e for e in text_events if e.get("type") == "response.text"]
    assert [e["text"] for e in rtx] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_run_turn_response_done_carries_full_text():
    """response.done 带 text=完整 assistant 回复."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient(deltas=["你", "好", "啊"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    done = [e for e in text_events if e.get("type") == "response.done"]
    assert len(done) == 1
    assert done[0]["text"] == "你好啊"


@pytest.mark.asyncio
async def test_run_turn_appends_memory_on_success():
    """success → memory.append_turn(user, assistant_full)."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="问句")
    sess.llm_client = FakeLLMClient(deltas=["回", "答"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    msgs = list(sess.memory)
    assert msgs == [
        {"role": "user", "content": "问句"},
        {"role": "assistant", "content": "回答"},
    ]


@pytest.mark.asyncio
async def test_run_turn_no_memory_on_llm_error():
    """LLM 异常 → memory 不动."""
    from app.pipeline import run_turn

    class BrokenLLM:
        last_messages = None
        async def stream(self, messages):
            BrokenLLM.last_messages = messages
            if False:
                yield None
            raise RuntimeError("llm crashed")

    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="x")
    sess.llm_client = BrokenLLM()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    assert list(sess.memory) == []  # 出错不污染 memory


@pytest.mark.asyncio
async def test_run_turn_audit_writes_events():
    """audit_writer 在 final / response.done 时被调用."""
    from app.pipeline import run_turn
    aw = FakeAuditWriter()
    sess = _make_session(audit_writer=aw)
    sess.stt_client = FakeSTTClient(final_text="hi")
    sess.llm_client = FakeLLMClient(deltas=["yo"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    events = [e["event"] for e in aw.events]
    assert "transcript.final" in events
    assert "response.done" in events
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -v
```

Expected: 旧 4 个 pass，新 6 个 fail（pipeline 还没改）。

- [ ] **Step 3: 重写 pipeline.py**

把 `services/realtime-server/app/pipeline.py` 整体替换为：

```python
"""Per-turn pipeline (SP3): STT final → LLM stream w/ memory → TTS → client.

新增于 SP3：
  - 组 messages = [system(prompt), ...memory, {user:final}] 喂给 llm_client
  - LLM delta 同时 ws.send_json(response.text) 和 tts_ws.send_text
  - response.done 带 text=完整 assistant 回复
  - 成功 turn → memory.append_turn(user, assistant)
  - 全程 audit.write(event) 异步落 JSONL
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.session_manager import Session

log = logging.getLogger("rtvoice.realtime.pipeline")


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "turn.timeout"
    s = str(exc).lower()
    if "stt" in s:
        return "stt.failed"
    if "tts" in s:
        return "tts.failed"
    if "llm" in s or "openai" in s or "ollama" in s:
        return "llm.failed"
    return "internal.unknown"


async def _audit(sess, event: dict) -> None:
    """audit_writer 可选；写错不抛."""
    if sess.audit_writer is None:
        return
    try:
        await sess.audit_writer.write(event)
    except Exception:
        log.exception("audit.write failed (continuing)")


async def run_turn(sess, ws):
    """SP3 single turn with memory + streaming + audit."""
    sess.current_turn_task = asyncio.current_task()
    user_text = ""
    assistant_chunks: list[str] = []
    try:
        # 1. STT final
        try:
            user_text = await sess.stt_client.request_final(
                timeout=config.STT_FINAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "code": "stt.timeout",
                                "message": "STT final timeout", "request_id": None})
            return

        if not user_text or not user_text.strip():
            await ws.send_json({"type": "error", "code": "stt.empty",
                                "message": "no speech detected", "request_id": None})
            return

        await ws.send_json({"type": "transcript.final", "text": user_text})
        await _audit(sess, {"event": "transcript.final", "text": user_text})

        # 2. 组 messages = [system, ...memory, user]
        messages: list[dict] = []
        if sess.prompt:
            messages.append({"role": "system", "content": sess.prompt})
        messages.extend(list(sess.memory))
        messages.append({"role": "user", "content": user_text})

        # 3. LLM stream + 并行 TTS feed + ws.response.text emit
        tts_ws = await sess.tts_client.open_ws()
        try:
            async def feeder():
                try:
                    async for delta in sess.llm_client.stream(messages):
                        if delta:
                            assistant_chunks.append(delta)
                            await ws.send_json({"type": "response.text", "text": delta})
                            await tts_ws.send_text(delta)
                finally:
                    await tts_ws.eos()

            feed_task = asyncio.create_task(feeder())
            try:
                async for pcm in tts_ws.audio_chunks():
                    if pcm:
                        await ws.send_bytes(pcm)
                await feed_task
            finally:
                if not feed_task.done():
                    feed_task.cancel()
                    try:
                        await feed_task
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            await tts_ws.aclose()

        # 4. response.done + memory + audit
        assistant_text = "".join(assistant_chunks)
        await ws.send_json({"type": "response.done", "text": assistant_text})
        await _audit(sess, {"event": "response.done", "text": assistant_text})

        # 仅成功 turn 写 memory（异常路径不污染历史）
        if assistant_text:
            sess.memory.append_turn(user_text, assistant_text)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("turn failed: %s", e)
        try:
            await ws.send_json({"type": "error", "code": _classify_error(e),
                                "message": str(e)[:200], "request_id": None})
        except Exception:
            pass
        await _audit(sess, {"event": "error", "code": _classify_error(e),
                            "message": str(e)[:200]})
    finally:
        sess.current_turn_task = None
        sess.last_activity = datetime.now(timezone.utc)
```

- [ ] **Step 4: 跑测试看全 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/test_pipeline_mock.py -v
```

Expected: 10 passed（旧 4 + 新 6）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/pipeline.py services/realtime-server/tests/test_pipeline_mock.py
git commit -m "feat(realtime-server): pipeline.run_turn() 加 memory + streaming + audit (T6)

- 组 messages = [system(prompt), ...memory, {user:final}]
- LLM delta → ws.send_json(response.text) 同时 → tts_ws.send_text
- response.done 带 text=完整 assistant 回复
- 成功 turn → sess.memory.append_turn；异常路径 memory 不动
- audit.write 在 transcript.final / response.done / error 三处
- pipeline 测试 4→10 个

per spec §5.2"
```

---

## Task 7: main.py — POST 入参 + GET /info + WS session.update + transcript.partial 回调

**Files:**
- Modify: `services/realtime-server/app/main.py`
- Modify: `services/realtime-server/tests/test_endpoints.py`

- [ ] **Step 1: 写新测试**

`services/realtime-server/tests/test_endpoints.py` 文件末尾追加：

```python
def test_create_session_with_prompt(client):
    r = client.post("/v1/sessions", json={"prompt": "你是 IT 客服"})
    assert r.status_code == 201
    body = r.json()
    assert body["prompt"] == "你是 IT 客服"
    assert body["audit_persist"] is False


def test_create_session_default_prompt_from_env(client):
    """不传 prompt 用 env default."""
    r = client.post("/v1/sessions", json={})
    body = r.json()
    # config.DEFAULT_PROMPT 默认值
    assert body["prompt"] == "你是语音助手。用中文简短回答（≤2 句）。"


def test_create_session_prompt_too_long_returns_422(client, monkeypatch):
    monkeypatch.setattr("app.config.PROMPT_MAX_CHARS", 100)
    # 重启 app 不便；直接 post 一个超长 prompt 看 server 端校验
    long_prompt = "x" * 200
    r = client.post("/v1/sessions", json={"prompt": long_prompt})
    # 注：monkeypatch 在 module-level config 已生效；但 main.py 在 fixture create 时
    # 已 import config，PROMPT_MAX_CHARS 在 main 内是 attribute reference，
    # 改 attr 会即时生效
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "prompt.too_long"


def test_info_includes_sp3_capabilities(client):
    r = client.get("/info")
    caps = r.json()["capabilities"]
    assert caps["memory"] is True
    assert caps["memory_max_turns"] == 6
    assert caps["transcript_partial"] is True
    assert caps["response_text"] is True
    assert "default_prompt" in caps
    assert isinstance(caps["default_prompt"], str)
```

- [ ] **Step 2: 跑测试看 fail**

```bash
cd services/realtime-server
python3 -m pytest tests/test_endpoints.py -v
```

Expected: 旧 11 passed + 4 new fail。

- [ ] **Step 3: 改 main.py**

修改 `services/realtime-server/app/main.py`:

3a. `SessionCreateRequest` 加字段（约 105 行）：
```python
class SessionCreateRequest(BaseModel):
    voice: str | None = Field(None, description="TTS voice spk_id, default: default_zh_female")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="TTS speed factor")
    prompt: str | None = Field(None, description="System prompt; default: env RTVOICE_DEFAULT_PROMPT")
    audit_persist: bool = Field(False, description="If true, persist transcript JSONL to AUDIT_DIR")
```

3b. `SessionCreateResponse` 加字段：
```python
class SessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str
    expires_at: str
    voice: str
    speed: float
    prompt: str
    audit_persist: bool
```

3c. `/info` 加字段（找到 `def info`，把 `capabilities` dict 替换为）：
```python
        "capabilities": {
            "session_api": True,
            "ws_realtime": True,
            "transcript_final": True,
            "transcript_partial": True,
            "response_text": True,
            "memory": True,
            "memory_max_turns": config.MEMORY_MAX_TURNS,
            "audit_persist": True,
            "default_prompt": config.DEFAULT_PROMPT,
            "max_concurrent_sessions": config.MAX_CONCURRENT_SESSIONS,
            "session_idle_timeout_s": config.SESSION_IDLE_TIMEOUT_S,
            "session_max_lifetime_s": config.SESSION_MAX_LIFETIME_S,
        },
```

3d. `create_session()` 加 prompt 校验 + 透传 + 返回。把 `async def create_session(...)` 整体替换：
```python
async def create_session(
    req: SessionCreateRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> SessionCreateResponse:
    bearer = _check_bearer_http(authorization)
    voice = req.voice or config.DEFAULT_VOICE
    prompt = req.prompt if req.prompt is not None else config.DEFAULT_PROMPT
    if len(prompt) > config.PROMPT_MAX_CHARS:
        raise api_error(422, "prompt.too_long",
                        f"prompt > {config.PROMPT_MAX_CHARS} chars")

    try:
        sess = await session_mgr.create(
            creator_key_hash=hash_key(bearer),
            voice=voice,
            speed=req.speed,
            prompt=prompt,
            audit_persist=req.audit_persist,
        )
    except CapacityFull as e:
        raise api_error(503, "session.capacity_full", str(e))

    return SessionCreateResponse(
        session_id=sess.id,
        ws_url=f"{config.PUBLIC_WS_BASE}/v1/realtime/{sess.id}",
        expires_at=sess.expires_at.isoformat(),
        voice=voice,
        speed=req.speed,
        prompt=prompt,
        audit_persist=req.audit_persist,
    )
```

3e. WS handler 内：
- STT client 构造时传 `on_partial` 回调
- main loop 加 session.update 处理

找到 `sess.stt_client = STTClient(...)` 那行（约第 234 行附近），改为：
```python
    async def _on_stt_partial(text: str) -> None:
        if not text:
            return
        try:
            await ws.send_json({"type": "transcript.partial", "text": text, "stable": False})
            if sess.audit_writer is not None:
                await sess.audit_writer.write({"event": "transcript.partial", "text": text})
        except Exception:
            log.exception("on_partial forward failed")

    sess.stt_client = STTClient(
        config.STT_WS_URL,
        on_partial=_on_stt_partial,
        api_key=config.RTVOICE_API_KEY or None,
    )
```

并在 main loop 内（音频处理分支后）加 session.update text 路径。找到现有 `elif msg.get("text") == "audio.eos":` 把它的整段处理改为：
```python
            elif msg.get("text"):
                # 解析 client→server text 消息
                text_msg = msg["text"]
                if text_msg == "audio.eos":
                    if sess.current_turn_task and not sess.current_turn_task.done():
                        await ws.send_json({
                            "type": "error", "code": "turn.in_progress",
                            "message": "previous turn not yet done",
                            "request_id": None,
                        })
                    else:
                        asyncio.create_task(run_turn(sess, ws))
                else:
                    # 尝试 JSON
                    import json as _json
                    try:
                        ev = _json.loads(text_msg)
                    except Exception:
                        log.debug("session %s: non-JSON text %r", session_id, text_msg[:80])
                        continue
                    if ev.get("type") == "session.update":
                        # 仅 prompt 进白名单
                        allowed = {"type", "prompt"}
                        extra = set(ev.keys()) - allowed
                        if extra:
                            await ws.send_json({
                                "type": "error",
                                "code": "session.update.invalid",
                                "message": f"only 'prompt' is hot-editable; got extra: {sorted(extra)}",
                                "request_id": None,
                            })
                        elif "prompt" in ev:
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
                    else:
                        log.debug("session %s: unknown event %r",
                                  session_id, ev.get("type"))
```

- [ ] **Step 4: 跑测试看全 pass**

```bash
cd services/realtime-server
python3 -m pytest tests/ -v
```

Expected: 全部 39+ passed（不会破坏旧测试）。

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
git add services/realtime-server/app/main.py services/realtime-server/tests/test_endpoints.py
git commit -m "feat(realtime-server): main.py SP3 endpoints + WS handlers (T7)

- POST /v1/sessions: 加 prompt + audit_persist 入参；prompt > MAX → 422
- response 含 prompt + audit_persist
- GET /info capabilities: memory/transcript_partial/response_text/default_prompt
- WS handler: STT on_partial 回调 → transcript.partial event (+ audit)
- WS handler: session.update 处理（白名单 prompt；其它 → error）
- +4 endpoint 测试

per spec §4 + §5.6"
```

---

## Task 8: docker-compose.yml + .env.example — 加 audit volume + SP3 env vars

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: 在 docker-compose.yml 的 realtime-server block 加 volume + env**

定位 realtime-server 的 `expose:` 行之上插入 `volumes`（如已有则追加），并在 `environment:` 段追加 SP3 环境变量。

`environment:` 段最后追加：
```yaml
      RTVOICE_MEMORY_MAX_TURNS: ${RTVOICE_MEMORY_MAX_TURNS:-6}
      RTVOICE_DEFAULT_PROMPT: ${RTVOICE_DEFAULT_PROMPT:-你是语音助手。用中文简短回答（≤2 句）。}
      RTVOICE_AUDIT_DIR: ${RTVOICE_AUDIT_DIR:-/data/transcripts}
      RTVOICE_AUDIT_QUEUE_MAX: ${RTVOICE_AUDIT_QUEUE_MAX:-1000}
      RTVOICE_PROMPT_MAX_CHARS: ${RTVOICE_PROMPT_MAX_CHARS:-2000}
```

并在 realtime-server service 的 service block 内（`expose:` 之上或并列）插入：
```yaml
    volumes:
      - ${RTVOICE_AUDIT_HOST_DIR:-./data/transcripts}:/data/transcripts:rw
```

- [ ] **Step 2: .env.example 追加 SP3 段**

文件末尾或 SP2 段后追加：
```bash
# ============================================================
# Realtime Voice — SP3 (v0.10+)
# ============================================================
RTVOICE_MEMORY_MAX_TURNS=6
RTVOICE_DEFAULT_PROMPT=你是语音助手。用中文简短回答（≤2 句）。
RTVOICE_PROMPT_MAX_CHARS=2000

# Audit (transcript JSONL persistence)
RTVOICE_AUDIT_DIR=/data/transcripts
RTVOICE_AUDIT_QUEUE_MAX=1000
# Host 路径（compose volume），默认 ./data/transcripts；prod 改 /var/data/rtvoice/transcripts
RTVOICE_AUDIT_HOST_DIR=./data/transcripts
```

- [ ] **Step 3: 校验 compose 语法**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
docker compose -f docker-compose.yml config --quiet 2>&1 || echo "compose VALIDATION FAILED"
```

Expected: 无输出（`--quiet` 静默成功）或 `compose VALIDATION FAILED` 之外的错误信息。

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(compose): realtime-server 加 audit volume + SP3 env vars (T8)

- volume: \${RTVOICE_AUDIT_HOST_DIR}:/data/transcripts:rw
- 5 个 SP3 env vars
- .env.example SP3 段

per spec §5.5"
```

---

## Task 9: 文档更新（CONVENTIONS / sessions / OPERATIONS / README）

**Files:**
- Modify: `docs/api/CONVENTIONS.md`
- Modify: `docs/api/sessions.md`
- Modify: `OPERATIONS.md`
- Modify: `README.md`
- Modify: `COZYVOICE_INTEGRATION.md`

- [ ] **Step 1: CONVENTIONS.md §6 加 3 条 error code**

在 §6 错误代码表追加：
```markdown
| `prompt.too_long` | POST/WS prompt 字符数超 PROMPT_MAX_CHARS |
| `session.update.invalid` | WS session.update 字段不在白名单 |
| `audit.write_failed` | 服务端落盘异常（不发 client，仅 log） |
```

- [ ] **Step 2: docs/api/sessions.md 把"SP3 启动时创建"改为指向已实现**

把第 64 行 `详细 session 生命周期 / memory 管理 / prompt 透传规则 → SP3 设计文档（SP3 启动时创建）。`
改为：
```markdown
详细 session 生命周期 / memory 管理 / prompt 透传规则 → [SP3 设计文档](../superpowers/specs/2026-05-09-sp3-realtime-memory-design.md)。
```

并把状态行更新：
```markdown
> **状态：v0.10.0 已实现**（prompt + memory + transcript.partial + response.text + session.update + audit_persist）。
```

POST /v1/sessions 的 Request 例已含 prompt + audit_persist；增 Response 例：
```markdown
### Response (201 Created)
\`\`\`json
{
  "session_id": "sess_abc123",
  "ws_url": "ws://localhost:9000/v1/realtime/sess_abc123",
  "expires_at": "2026-05-09T16:30:00Z",
  "voice": "default_zh_female",
  "speed": 1.0,
  "prompt": "你是语音助手。用中文简短回答（≤2 句）。",
  "audit_persist": false
}
\`\`\`
```

- [ ] **Step 3: OPERATIONS.md 加 SP3 段**

在 §2 之后加：
```markdown
### 2.6 Realtime Voice SP3 (v0.10+)

| 变量 | 默认 | 调整时机 |
|---|---|---|
| `RTVOICE_MEMORY_MAX_TURNS` | 6 | 上下文需求多 → 调高（注意 LLM context 限制）|
| `RTVOICE_DEFAULT_PROMPT` | 中文短回答 | 客户端不传时的兜底 |
| `RTVOICE_AUDIT_DIR` | `/data/transcripts` | 改路径需同步 compose volume |
| `RTVOICE_PROMPT_MAX_CHARS` | 2000 | 客户端 prompt 上限 |
```

§3 升级路径加：
````markdown
### 3.5 v0.9.x → v0.10.0（SP3 加 prompt + memory + audit）

```bash
# 1. 创建宿主 transcripts 目录（首次）
mkdir -p /var/data/rtvoice/transcripts && chown 1000:1000 /var/data/rtvoice/transcripts

# 2. .env 加：
# RTVOICE_AUDIT_HOST_DIR=/var/data/rtvoice/transcripts

# 3. 部署
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
               up -d --build realtime-server
```

**验证**：
```bash
curl -s http://127.0.0.1:9000/info | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['capabilities']['memory'], d['capabilities']['default_prompt'])"
```
````

§4 cookbook 加：
```markdown
### 4.8 audit JSONL 文件没出现

- 检查 RTVOICE_AUDIT_DIR 在容器内权限：`docker exec rtvoice-realtime ls -la /data/transcripts`
- 用户必须传 `audit_persist=true`；默认 false
- mkdir 失败 → 看 `docker logs rtvoice-realtime | grep "audit dir"`

### 4.9 多轮对话不连续（agent 不记得前文）

- 看 `/info` 返回 `memory: true`
- 看 audit JSONL 文件每个 turn 的 messages 长度（client console / response.text 累计）
- MEMORY_MAX_TURNS=0 会禁用 memory；确认 env
```

- [ ] **Step 4: README.md 升级 capabilities 表**

定位 README.md 中关于 Realtime Voice 的 card 或 features 段，把"单 turn 无 memory"等表述改为"多轮对话 + 流式 transcript + audit"。

具体修改：找到含 "memory: false" 或 "(SP3 实现)" 字样的位置，改为反映 v0.10。如无明确字样，在 60s try 表后加：
```markdown
**Realtime Voice 完整能力（v0.10+）**: 多轮记忆 / 流式 transcript+text / 中途换 prompt / 异步 audit JSONL。详见 [SP3 spec](./docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md)。
```

- [ ] **Step 5: COZYVOICE_INTEGRATION.md §5.4 加 prompt + memory 用法**

在 §5.4 Realtime Voice 客户端 Python 例后追加："多轮对话 + 自定义 prompt" 用法注：
```markdown
**用法 — 自定义 prompt + audit**：

```python
sess = await client.create_session(prompt="你是 IT 客服，用中文简短回答", audit_persist=True)
# 之后所有 turn agent 用此 prompt + 自动滚 6 轮历史
# audit JSONL 在 server 端 /data/transcripts/{date}/{session_id}.jsonl
```

**中途换 prompt**：

```python
async with websockets.connect(ws_url) as ws:
    await ws.send(json.dumps({"type": "session.update", "prompt": "改用英文"}))
    # 下一 turn 起 agent 用新 prompt
```
```

- [ ] **Step 6: Commit**

```bash
git add docs/api/CONVENTIONS.md docs/api/sessions.md OPERATIONS.md README.md COZYVOICE_INTEGRATION.md
git commit -m "docs: SP3 配套（CONVENTIONS / sessions / OPERATIONS / README / COZYVOICE）(T9)

- CONVENTIONS.md §6: +3 错误码（prompt.too_long / session.update.invalid / audit.write_failed）
- sessions.md: 状态升级 v0.10.0；指向 SP3 spec；Response 例
- OPERATIONS.md: §2.6 env vars / §3.5 升级路径 / §4.8-4.9 cookbook
- README.md: Realtime Voice 完整能力一行
- COZYVOICE_INTEGRATION.md §5.4: 自定义 prompt + session.update 用法

per spec §4.5"
```

---

## Task 10: Static 测试页（多轮对话 + transcript.partial + response.text 显示）

**Files:**
- Create: `services/realtime-server/static/index.html`
- Modify: `services/realtime-server/Dockerfile`（COPY static 进镜像）
- Modify: `services/realtime-server/app/main.py`（mount static）

- [ ] **Step 1: 写最小可用浏览器测试页**

`services/realtime-server/static/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>RTVoice Realtime — SP3 Test</title>
<style>
body{font-family:sans-serif;max-width:780px;margin:2em auto;padding:0 1em}
input,textarea,button{font-size:14px;padding:6px}
.row{margin:.5em 0}
#log{border:1px solid #ddd;padding:.5em;height:300px;overflow:auto;white-space:pre-wrap;font-family:monospace;font-size:12px}
.user{color:#0a0}.agent{color:#06c}.evt{color:#888}.err{color:#c00}
</style>
</head>
<body>
<h1>RTVoice Realtime — SP3 Test Page</h1>
<div class="row">
  <label>API base: <input id="api" value="http://127.0.0.1:9000" size="40"></label>
</div>
<div class="row">
  <label>Bearer (空=dev): <input id="bearer" size="40"></label>
</div>
<div class="row">
  <label>Prompt (空=server default):</label><br>
  <textarea id="prompt" rows="2" cols="80"></textarea>
</div>
<div class="row">
  <label><input type="checkbox" id="audit"> audit_persist</label>
</div>
<div class="row">
  <button id="create">1) 创建 session</button>
  <button id="connect" disabled>2) 连 WS</button>
  <button id="rec" disabled>3) 开始录音</button>
  <button id="eos" disabled>4) 结束 turn</button>
  <button id="updp" disabled>5) 改 prompt</button>
</div>
<div class="row" id="info"></div>
<div id="log"></div>

<script>
const $=id=>document.getElementById(id);
const log=(c,t)=>{const d=document.createElement("div");d.className=c;d.textContent=t;$('log').appendChild(d);$('log').scrollTop=$('log').scrollHeight;};
let ws,session,mediaRec,audioCtx,procNode,mic,playBuf=[];

$('create').onclick=async()=>{
  const r=await fetch($('api').value+"/v1/sessions",{
    method:"POST",
    headers:{"Content-Type":"application/json", ...($('bearer').value?{"Authorization":"Bearer "+$('bearer').value}:{})},
    body:JSON.stringify({prompt:$('prompt').value||undefined, audit_persist:$('audit').checked})
  });
  if(!r.ok){log('err',"create failed: "+r.status+" "+await r.text());return}
  session=await r.json();
  $('info').textContent="session: "+session.session_id+" | prompt: "+session.prompt.slice(0,40)+"...";
  log('evt','created '+session.session_id);
  $('connect').disabled=false;
};

$('connect').onclick=()=>{
  let url=session.ws_url.replace("realtime-server","127.0.0.1"); // dev rewrite
  const proto=$('bearer').value?["bearer."+$('bearer').value]:[];
  ws=new WebSocket(url, proto);
  ws.binaryType="arraybuffer";
  ws.onopen=()=>{log('evt','ws open');$('rec').disabled=false;$('eos').disabled=false;$('updp').disabled=false;};
  ws.onmessage=e=>{
    if(typeof e.data==='string'){
      const ev=JSON.parse(e.data);
      const t=ev.type;
      if(t==='transcript.partial'){log('user','部分: '+ev.text);}
      else if(t==='transcript.final'){log('user','你说: '+ev.text);}
      else if(t==='response.text'){log('agent','agent: '+ev.text);}
      else if(t==='response.done'){log('evt','done: '+(ev.text||'').slice(0,60));}
      else if(t==='error'){log('err','error: '+ev.code+' '+ev.message);}
      else log('evt',JSON.stringify(ev));
    }else{
      // PCM bytes — 简化：只计数（接 AudioContext 播放略）
      log('evt','pcm '+e.data.byteLength+' bytes');
    }
  };
  ws.onclose=e=>log('evt','ws close '+e.code);
  ws.onerror=e=>log('err','ws err');
};

$('rec').onclick=async()=>{
  if(!audioCtx){audioCtx=new AudioContext({sampleRate:16000});}
  mic=await navigator.mediaDevices.getUserMedia({audio:{sampleRate:16000,channelCount:1,echoCancellation:true}});
  const src=audioCtx.createMediaStreamSource(mic);
  procNode=audioCtx.createScriptProcessor(2048,1,1);
  procNode.onaudioprocess=e=>{
    const f32=e.inputBuffer.getChannelData(0);
    const i16=new Int16Array(f32.length);
    for(let i=0;i<f32.length;i++)i16[i]=Math.max(-1,Math.min(1,f32[i]))*0x7FFF;
    if(ws&&ws.readyState===1)ws.send(i16.buffer);
  };
  src.connect(procNode);procNode.connect(audioCtx.destination);
  log('evt','录音中…');
};

$('eos').onclick=()=>{ws.send("audio.eos");log('evt','EOS sent');};
$('updp').onclick=()=>{
  const p=prompt("新 prompt:");
  if(!p)return;
  ws.send(JSON.stringify({type:"session.update",prompt:p}));
  log('evt','session.update sent');
};
</script>
</body>
</html>
```

- [ ] **Step 2: Dockerfile 加 COPY static**

修改 `services/realtime-server/Dockerfile`，在 `COPY app /app/app` 行后追加：
```dockerfile
COPY static /app/static
```

- [ ] **Step 3: main.py mount static + 加 / route 返回 index**

修改 `services/realtime-server/app/main.py`：

3a. 在 `from fastapi import (...)` 后追加：
```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
```

3b. 在 `app = FastAPI(...)` 之后插入 mount：
```python
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def index() -> HTMLResponse:
    idx = _STATIC_DIR / "index.html"
    if not idx.is_file():
        return HTMLResponse("<h1>RTVoice Realtime</h1><p>静态测试页未部署。</p>")
    return HTMLResponse(idx.read_text(encoding="utf-8"))
```

- [ ] **Step 4: 验证镜像 build**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
docker compose -f docker-compose.yml --profile dev build realtime-server 2>&1 | tail -10
```

Expected: build 成功。

- [ ] **Step 5: Commit**

```bash
git add services/realtime-server/static/ services/realtime-server/Dockerfile services/realtime-server/app/main.py
git commit -m "feat(realtime-server): 静态测试页 + GET / 入口 (T10)

- static/index.html: 简易浏览器测试 UI（创 session / 连 WS / 录音 /
  audio.eos / session.update / 显示 transcript.partial+response.text）
- Dockerfile: COPY static
- main.py: mount /static, GET / 返回 index

per spec §7.2 user-participation 验收"
```

---

## Task 11: CHANGELOG v0.10.0 + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 在 [Unreleased] 之后插入 v0.10.0 entry**

定位 `## [Unreleased]` 与 `## [0.9.0]` 之间，插入：

```markdown
## [0.10.0] — 2026-05-09 — SP3 Realtime Voice prompt + memory + 流式 + audit

平台化重构第四阶段：升级 realtime-server 从"单 turn 无记忆"到"多轮对话 + 流式 transcript/response + 异步 audit"。

### Added

- `services/realtime-server/app/memory.py`：ConversationMemory 滑动窗口
- `services/realtime-server/app/audit.py`：AuditWriter 异步 JSONL
- `services/realtime-server/static/index.html`：浏览器测试页
- POST /v1/sessions 加 `prompt` + `audit_persist` 入参
- WS 事件：`transcript.partial`、`response.text`、`session.update`
- WS `response.done` 加 `text` 字段（完整 assistant 回复）
- GET /info `capabilities` 加 memory / transcript_partial / response_text / default_prompt
- 5 个 env vars：RTVOICE_MEMORY_MAX_TURNS / RTVOICE_DEFAULT_PROMPT / RTVOICE_AUDIT_DIR / RTVOICE_AUDIT_QUEUE_MAX / RTVOICE_PROMPT_MAX_CHARS
- docker-compose volume：宿主 transcripts dir → /data/transcripts

### Changed

- `Session` dataclass 加 prompt / memory / audit_persist / audit_writer
- `LLMClient.stream()` 签名 `(messages: list[dict])`，pipeline 自己组消息列表
- `pipeline.run_turn`：构造 messages、emit response.text、turn 末写 memory + audit
- `STTClient` 在 main.py 创建时传 on_partial 回调（→ ws.send_json transcript.partial）

### 验证（autonomous）

- ✅ unit memory 4 测试 / audit 5 测试 / session_manager +3 / pipeline +6 / endpoints +4
- ✅ 总测试 28 → 49+
- ✅ OpenAPI schema 含 prompt + audit_persist
- ⏳ prod 集成测试（user-participation 浏览器验收，合并 SP2 延期项）

### 设计决策

- 滑动窗口纯 deque（O(1) append + 自动驱逐），无 tokenizer，TTFT 不退化
- audit 路径用 session 创建日期，全程一个文件（不跨 0 点切）
- session.update 仅 prompt 进白名单（YAGNI；voice/memory.clear 留 SP4+）
- LLMClient 改签名是 breaking，但 realtime-server 是 SP2 起的全新 service；agent-worker 用自己的 client copy 不受影响

详见 [SP3 设计](./docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-09-sp3-realtime-memory.md)。

---
```

- [ ] **Step 2: 全文档链接 lint**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
for f in README.md ARCHITECTURE.md DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md docs/api/CONVENTIONS.md docs/api/stt.md docs/api/tts.md docs/api/sessions.md; do
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
git commit -m "docs(changelog): v0.10.0 — SP3 prompt + memory + 流式 + audit (T11)

- Added: memory.py / audit.py / static / 5 env vars / volume
- Changed: Session 字段 / LLMClient.stream(messages) / pipeline 重写
- 49+ 测试；prod 浏览器验收合 SP2 延期项"

git push origin main 2>&1 | tail -10
```

Expected: push 成功。

---

## Task 12: prod 集成测试 + user-participation 验收

**Files:** 无（read-only verification）

- [ ] **Step 1: prod 端 git pull + build + up**

```bash
ssh root@192.168.66.163 'cd /data/RTVoice && {
  cp .env .env.bak.$(date +%Y%m%d-%H%M%S)
  git pull origin main 2>&1 | tail -3
  echo
  # 创建 audit host dir（首次）
  mkdir -p /var/data/rtvoice/transcripts
  chown 1000:1000 /var/data/rtvoice/transcripts
  # 加 RTVOICE_AUDIT_HOST_DIR 到 .env（如未设）
  grep -q "^RTVOICE_AUDIT_HOST_DIR=" .env || echo "RTVOICE_AUDIT_HOST_DIR=/var/data/rtvoice/transcripts" >> .env
  echo
  t1=$(date +%s)
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 build realtime-server 2>&1 | tail -5
  t2=$(date +%s)
  echo "build: $((t2-t1))s"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod \
                 up -d realtime-server 2>&1 | tail -5
  echo
  for i in $(seq 1 20); do
    s=$(docker inspect rtvoice-realtime --format "{{.State.Health.Status}}" 2>/dev/null)
    echo "[$i] $s"
    [ "$s" = "healthy" ] && break
    sleep 3
  done
}'
```

Expected: build 成功，realtime-server healthy。

- [ ] **Step 2: prod autonomous 验收**

```bash
ssh root@192.168.66.163 'echo "=== A1: prompt 透传 ===" && docker exec rtvoice-agent python3 -c "
import urllib.request, json
req = urllib.request.Request(
    \"http://realtime-server:9000/v1/sessions\",
    data=json.dumps({\"prompt\":\"你是 IT 客服\"}).encode(),
    headers={\"Content-Type\":\"application/json\"},
)
r = urllib.request.urlopen(req, timeout=10)
b = json.loads(r.read())
print(\"prompt:\", b[\"prompt\"])
assert b[\"prompt\"] == \"你是 IT 客服\"
print(\"✓ A1\")
" && echo "=== A2: default prompt ===" && docker exec rtvoice-agent python3 -c "
import urllib.request, json
req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\", data=b\"{}\", headers={\"Content-Type\":\"application/json\"})
r = urllib.request.urlopen(req, timeout=10)
b = json.loads(r.read())
print(\"default:\", b[\"prompt\"][:30], \"...\")
assert \"语音助手\" in b[\"prompt\"]
print(\"✓ A2\")
" && echo "=== A3: prompt too long ===" && docker exec rtvoice-agent python3 -c "
import urllib.request, json
data = json.dumps({\"prompt\":\"x\"*3000}).encode()
req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\", data=data, headers={\"Content-Type\":\"application/json\"})
try:
    urllib.request.urlopen(req, timeout=10)
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    print(\"code:\", body[\"code\"])
    assert e.code == 422
    assert body[\"code\"] == \"prompt.too_long\"
    print(\"✓ A3\")
" && echo "=== A4: GET /info ===" && docker exec rtvoice-agent python3 -c "
import urllib.request, json
r = urllib.request.urlopen(\"http://realtime-server:9000/info\", timeout=5)
caps = json.loads(r.read())[\"capabilities\"]
assert caps[\"memory\"] is True
assert caps[\"memory_max_turns\"] == 6
assert caps[\"transcript_partial\"] is True
assert caps[\"response_text\"] is True
assert isinstance(caps[\"default_prompt\"], str)
print(\"✓ A4 caps:\", {k:v for k,v in caps.items() if k.startswith((\"memory\",\"transcript\",\"response\",\"default\"))})
" && echo "=== A5: OpenAPI ===" && docker exec rtvoice-agent python3 -c "
import urllib.request, json
r = urllib.request.urlopen(\"http://realtime-server:9000/openapi.json\", timeout=5)
schema = json.loads(r.read())
post = schema[\"paths\"][\"/v1/sessions\"][\"post\"]
body_schema = post[\"requestBody\"][\"content\"][\"application/json\"][\"schema\"]
ref = body_schema.get(\"\$ref\", \"\").split(\"/\")[-1]
props = schema[\"components\"][\"schemas\"][ref][\"properties\"]
assert \"prompt\" in props
assert \"audit_persist\" in props
print(\"✓ A5 props:\", list(props.keys()))
"'
```

Expected: 全 ✓。

- [ ] **Step 3: prod audit 落盘验证**

```bash
ssh root@192.168.66.163 'docker exec rtvoice-agent python3 -c "
import urllib.request, json
data = json.dumps({\"prompt\":\"test\",\"audit_persist\":True}).encode()
req = urllib.request.Request(\"http://realtime-server:9000/v1/sessions\", data=data, headers={\"Content-Type\":\"application/json\"})
r = urllib.request.urlopen(req, timeout=10)
sid = json.loads(r.read())[\"session_id\"]
print(\"sid:\", sid)
" && sleep 2 && ls -la /var/data/rtvoice/transcripts/ 2>/dev/null && echo "(audit dir 内容 — session 创建后无 turn 时为空属正常)"'
```

Expected: 创建成功；audit 目录存在（具体 jsonl 文件需要 turn 触发，浏览器验收后才能看到内容）。

- [ ] **Step 4: 通知 user 做浏览器验收**

```
SP3 沙盒 + autonomous 完工。请你做浏览器验收（合 SP2 延期项）：

1. 浏览器开 http://192.168.66.163:9000/  （或你的 prod 公网域名）
2. 填 API base / Bearer / Prompt（空=用 server default）
3. 点【创建 session】→【连 WS】→【开始录音】→ 说一句 → 【结束 turn】
4. 验证：
   B1 多轮：连续 4 次"录音→EOS"，第 4 次提"刚才你说啥"→ agent 复述（验 memory）
   B2 第 7 次起：浏览器 console F12 看 ws 流量 / response.text 累计；audit JSONL
      在 /var/data/rtvoice/transcripts/{date}/{sid}.jsonl 应有完整 turn 记录
   B3 中途点【改 prompt】填新内容 → 下一 turn 风格切换
   B4 边说边显示 transcript.partial（绿色 partial 行刷新）
   B5 audit_persist 勾选时 → 服务器看 jsonl 文件
```

- [ ] **Step 5: User 反馈后标 SP3 完工**

OK → SP3 done；问题 → fix loop。

---

## Self-Review

### Spec coverage

| Spec 节 | Plan Task |
|---|---|
| §1 目标 6 项能力 | T2 memory / T3 audit / T6 partial+text+memory / T7 session.update / T8 audit volume |
| §2 6 决策 | T1-T7 各对应 |
| §3 文件结构 | T1-T7 一一对应（含 stt_client 已有 on_partial 在 T7 wire） |
| §4 API 增量 | T7 (POST/info/WS handlers) |
| §5 memory + audit pipeline | T2 + T3 + T6 |
| §6 错误处理 | T7 (prompt.too_long / session.update.invalid) + T9 (CONVENTIONS) |
| §7 验收 | T12 |
| §8 测试矩阵 | T1 +1 / T2 4 / T3 5 / T4 +3 / T5 +1 / T6 +6 / T7 +4 = 24（spec 说 21；实际 24，更全） |
| §9 风险 | 已在 T3 audit IO swallow / T6 异常路径 memory 不动 / T11 docs |
| §10 范围外 | 未实施任何，OK |
| §11 实施切片 | 12 task 完全对齐（T8 compose / T9 docs / T10 测试页 / T11 changelog / T12 prod） |

无遗漏。

### Placeholder scan

- 所有 step 含完整代码或命令
- 无 "TBD" / "TODO"
- T9 docs 修改前后字段都给出
- T7 step 3 给出整段替换代码（不是"修改某行"）

### Type consistency

- `Session` 字段：T4 加 prompt/memory/audit_persist/audit_writer，T5 不动 LLMClient.system_prompt（已删），T6 用 `sess.memory.append_turn(u,a)` / `sess.audit_writer.write({...})` 与 T2/T3 签名匹配 ✓
- `ConversationMemory.append_turn(user, assistant)` 一致（T2 定义，T6 调用）
- `AuditWriter.write(event: dict)` 一致（T3 定义，T6 调用，T7 调用）
- `LLMClient.stream(messages)` 一致（T5 定义，T6 调用）
- session.update 字段白名单：spec §5.6 说仅 `prompt`，T7 实现校验 `allowed = {"type","prompt"}` ✓

无类型/签名漂移。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-sp3-realtime-memory.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + spec/quality 双审；与 SP1/SP1.5/SP2 同流程
2. **Inline Execution** — 本 session 批量执行 + checkpoints

Which approach?
