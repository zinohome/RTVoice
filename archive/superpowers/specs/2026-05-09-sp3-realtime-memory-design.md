# SP3 Realtime Voice — Prompt + Memory + Streaming Transcript Design

**日期**：2026-05-09
**前置**：SP2 (v0.9.0) 已上 prod；realtime-server 提供 POST /v1/sessions + WS /v1/realtime/{id}，PCM in/out + transcript.final + response.done。
**目标版本**：v0.10.0
**作用域**：升级现有 realtime-server 的对话能力，**不新增 docker service**。

---

## 1. 目标

把 SP2 的"单 turn 无记忆"voice loop 升级为"多轮上下文 + 流式 UX + 可审计"的 Realtime Voice。具体落到 6 项能力：

1. `prompt` —— 创建 session 时传入 system message，定义 agent 角色 / 风格
2. `memory` —— 同 session 内多轮对话历史自动喂给 LLM（滑动窗口）
3. `transcript.partial` —— STT 中途文本实时推给 client（"边说边显示"）
4. `response.text` —— LLM delta 文本推给 client（agent 边回答边显示文字）
5. `session.update` —— mid-session 热改 prompt（不断 ws）
6. `audit_persist` —— 整段对话异步落 JSONL 盘文件

---

## 2. 关键决策（D-2026-05-09）

| ID | 决策 | 备选 | 理由 |
|---|---|---|---|
| **A.1** | SP3 范围含全部 6 项能力 | 仅 prompt+memory+partial | 用户决策 (Q1=C) |
| **A.2** | 滑动窗口 N 轮（默认 6 轮 = 12 条 user/assistant 消息），纯 in-memory `collections.deque(maxlen=2N)` | token-budget+tokenizer | 性能优先：每 turn O(1)，零 tokenizer 开销，TTFT 不退化 |
| **A.3** | `session.update` 仅热改 `prompt`；voice/speed/memory.clear 不在范围 | 全字段 / +memory.clear | 实现最小，覆盖 80% 场景 |
| **A.4** | audit 文件路径 `${RTVOICE_AUDIT_DIR}/{YYYY-MM-DD}/{session_id}.jsonl`；用 session 创建日期，全程一个文件 | 跨 0 点切换文件 | 单 session 不切文件，简化 reader 侧 |
| **A.5** | LLM 接口改签名为 `stream(messages: list[dict])`；pipeline 自己组装 messages | client 内部组装 / 旧+新双方法 | LLM client 是薄包装，messages 是 OpenAI 原生格式，组装责任归 pipeline 最自然 |
| **A.6** | env `RTVOICE_DEFAULT_PROMPT` 提供出厂默认；GET /info `capabilities.default_prompt` 暴露当前值 | 无默认 / 仅 env 不暴露 | 客户端可发现；运维 / client / mid-session 三层都可改 |

---

## 3. 架构 & 文件变更

```
services/realtime-server/app/
├── main.py            ★ POST /v1/sessions 加 prompt/audit_persist；
│                        GET /info 加 capabilities.default_prompt 等；
│                        WS handler 处理 session.update 事件
├── session_manager.py ★ Session 加字段：prompt / memory(deque) / audit_persist / audit_writer
├── pipeline.py        ★ 重写 run_turn：组 messages、emit transcript.partial/response.text、
│                        累积 assistant_text、turn 末 append memory + 写 audit done
├── memory.py          + 新建：sliding-window 工具（thin wrapper on collections.deque）
├── audit.py           + 新建：异步 JSONL writer（asyncio.Queue + asyncio.to_thread IO）
├── llm_client.py      ★ 改：stream(messages) 接受 list[dict]
├── stt_client.py      ★ 改：暴露 partial 回调（pipeline 接它转成 ws.send_json transcript.partial）
└── config.py          ★ 加 RTVOICE_MEMORY_MAX_TURNS / RTVOICE_DEFAULT_PROMPT /
                         RTVOICE_AUDIT_DIR / RTVOICE_AUDIT_QUEUE_MAX
```

**新文件**：2（memory.py / audit.py）
**修改文件**：6
**新增依赖**：0（用 stdlib `collections.deque` + `asyncio.Queue` + `asyncio.to_thread`）
**SP2 兼容**：API 增量；client 不传 prompt/audit_persist 时行为与 SP2 默认一致（除 memory 始终启用）

---

## 4. API 增量

### 4.1 POST /v1/sessions

Request 加：
```json
{
  "voice": "default_zh_female",
  "speed": 1.0,
  "prompt": "你是 IT 客服，用中文简短回答",
  "audit_persist": false
}
```
- `prompt`：optional，缺则 `RTVOICE_DEFAULT_PROMPT`；长度 1-2000 字符（超 → 422 `prompt.too_long`）
- `audit_persist`：optional，默认 false

Response 加：
```json
{
  "session_id": "sess_xxx",
  "ws_url": "...",
  "expires_at": "...",
  "voice": "default_zh_female",
  "speed": 1.0,
  "prompt": "<实际生效的，含 env 默认>",
  "audit_persist": false
}
```

### 4.2 GET /info

`capabilities` 加：
```json
{
  "memory": true,
  "memory_max_turns": 6,
  "audit_persist": true,
  "transcript_partial": true,
  "response_text": true,
  "default_prompt": "你是语音助手。用中文简短回答（≤2 句）。"
}
```

### 4.3 WS Server→Client（新增 2 种事件）

| Type | Payload | 时机 |
|---|---|---|
| `transcript.partial` | `{"type":"transcript.partial","text":"...","stable":false}` | STT 流式中途，每收到 partial 立即转发 |
| `response.text` | `{"type":"response.text","text":"<delta>"}` | LLM 每 yield 一个 delta，与喂 TTS 并行 |

`response.done` 增字段 `text`：
```json
{"type":"response.done","text":"<完整 assistant 回复>"}
```

### 4.4 WS Client→Server（新增 1 种事件）

| Type | Payload | 说明 |
|---|---|---|
| `session.update` | `{"type":"session.update","prompt":"<new>"}` | 仅 `prompt` 字段进白名单；其它字段 → emit error `session.update.invalid` |

`session.update` 在 turn 进行中收到也接受，但**下一 turn 才生效**（不打断当前 turn）。

### 4.5 新增 error code

| Code | HTTP/WS | 含义 |
|---|---|---|
| `prompt.too_long` | 422 | POST 入 prompt > 2000 字符 |
| `session.update.invalid` | WS event | session.update 字段不在白名单（仅 prompt） |
| `audit.write_failed` | log only | 落盘异常（不发 ws，不打断 turn）|

`docs/api/CONVENTIONS.md` §6 表追加这 3 行。

---

## 5. Memory & Audit 流水线

### 5.1 Session 字段（增量）

```python
@dataclass
class Session:
    # ... SP2 已有：id, creator_key_hash, voice, speed, created_at, expires_at, state, ws,
    #     last_activity, current_turn_task, stt_client, llm_client, tts_client ...
    prompt: str                                # 必有：POST 传或 env 默认
    memory: deque                              # maxlen = 2 * MEMORY_MAX_TURNS
    audit_persist: bool                        # POST 传或 false
    audit_writer: Any = None                   # AuditWriter 实例（仅 audit_persist=True 时）
```

`SessionManager.create()` 入参加 `prompt: str, audit_persist: bool`，初始化 `memory = collections.deque(maxlen=2*config.MEMORY_MAX_TURNS)`。

### 5.2 turn 数据流（pipeline.run_turn 新版本）

```
[A] STT 流到来：
    stt_client 暴露 partial 回调 → pipeline 转成 ws.send_json {transcript.partial}
                                  → audit.write({"event":"transcript.partial",...})

[B] STT final：
    final_text = await sess.stt_client.request_final(timeout=...)
    if empty → emit error stt.empty + return
    ws.send_json {transcript.final, text=final_text}
    audit.write({"event":"transcript.final", "text":final_text})

[C] 组 messages：
    messages = [{"role":"system","content": sess.prompt},
                *list(sess.memory),                        # 已存历史 ≤2N 条
                {"role":"user","content": final_text}]

[D] LLM stream + 并行 TTS feed + ws response.text emit：
    assistant_chunks = []
    tts_ws = await sess.tts_client.open_ws()
    async def feeder():
        async for delta in sess.llm_client.stream(messages):
            if delta:
                assistant_chunks.append(delta)
                await ws.send_json({"type":"response.text","text":delta})
                await tts_ws.send_text(delta)
        await tts_ws.eos()
    feed_task = asyncio.create_task(feeder())
    async for pcm in tts_ws.audio_chunks():
        if pcm:
            await ws.send_bytes(pcm)
    await feed_task
    await tts_ws.aclose()

[E] turn 收尾：
    assistant_text = "".join(assistant_chunks)
    ws.send_json {response.done, text=assistant_text}
    audit.write({"event":"response.done", "text":assistant_text})

    # 写 memory（成对追加；deque maxlen 自动驱逐最早一对）
    sess.memory.append({"role":"user","content": final_text})
    sess.memory.append({"role":"assistant","content": assistant_text[:4000]})  # 4000 字符截断防爆
```

**memory 写入时机：仅在 turn 完整结束后**（response.done 已发）。turn 中途异常 → 不污染 memory（用户重发即可）。

### 5.3 Audit Writer 模块

`app/audit.py`:

```python
class AuditWriter:
    def __init__(self, session_id: str, base_dir: str, queue_max: int = 1000):
        date = datetime.utcnow().strftime("%Y-%m-%d")
        self.path = Path(base_dir) / date / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._q: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._task: asyncio.Task = asyncio.create_task(self._loop())

    async def write(self, event: dict) -> None:
        """O(1) 微秒级；queue full → drop + warn."""
        item = {"ts": datetime.utcnow().isoformat()+"Z", **event}
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("audit queue full for %s, dropping %s", self.path.name, item.get("event"))

    async def _loop(self) -> None:
        while True:
            try:
                item = await self._q.get()
            except asyncio.CancelledError:
                break
            # batch drain：收到一条尝试 drain 至 50 条一起 flush
            batch = [item]
            for _ in range(49):
                try:
                    batch.append(self._q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await asyncio.to_thread(self._flush, batch)
            except Exception:
                log.exception("audit write failed for %s", self.path.name)
                # 不重试 / 不进 dlq；落盘是 best-effort

    def _flush(self, batch: list[dict]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for item in batch:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def aclose(self) -> None:
        # 1) 停接收新 event
        # 2) drain 剩余队列
        # 3) cancel writer task
        # 调用方：session_manager.cleanup() 内调
        ...
```

### 5.4 性能契约

| 操作 | 复杂度 | 阻塞 event loop？ |
|---|---|---|
| `audit.write({...})` | O(1) | 否（put_nowait） |
| memory deque append（含驱逐） | O(1) | 否 |
| messages 组装 | O(N), N≤12 | 否 |
| 真正落盘 IO | 后台 task `asyncio.to_thread` | 否 |
| transcript.partial → ws.send_json | O(text len) | 否 |

**TTFT 影响**：相比 SP2，SP3 在 turn 末多了 2 次 `deque.append`（O(1)）+ 1 次 `audit.write`（O(1)）+ 若干 `audit.write` 在 partial/final/done 三处。所有热路径都是常数微秒，**TTFT 不退化**。

### 5.5 配置

`config.py` 加：
| 变量 | 默认 | 说明 |
|---|---|---|
| `RTVOICE_MEMORY_MAX_TURNS` | 6 | 滑动窗口轮数（实际存 12 条消息） |
| `RTVOICE_DEFAULT_PROMPT` | `"你是语音助手。用中文简短回答（≤2 句）。"` | 出厂默认 system message |
| `RTVOICE_AUDIT_DIR` | `/data/transcripts` | 落盘根路径（容器 volume） |
| `RTVOICE_AUDIT_QUEUE_MAX` | 1000 | per-session audit queue 上限 |
| `RTVOICE_PROMPT_MAX_CHARS` | 2000 | POST prompt 长度上限 |

`docker-compose.yml` 加 volume：`/data/transcripts:/data/transcripts:rw`。

### 5.6 session.update 处理

main.py WS 主循环收到 text 时分支：
- `"audio.eos"` → 触发 run_turn（SP2 已有）
- text JSON 且 `type == "session.update"` → 校验字段名（白名单：`{"prompt"}`）；通过则 `sess.prompt = new_value`（thread-safe：单 ws 单 task，无并发）；非白名单字段 → emit error `session.update.invalid`
- text JSON 且 `type == "memory.clear"`（白名单外）→ emit error
- 其它 → log debug 忽略

---

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| audit IO 异常（disk full / permission） | log warn；不发 ws；turn 正常完成 |
| audit queue 满 | drop 该 event + log warn；turn 正常完成 |
| memory deque 中 assistant_text 截断后超 4000 字符 | hard truncate，log debug |
| LLM 异常 | turn 异常路径不写 memory；audit 写 `{"event":"error",...}` |
| session.update 字段超白名单 | emit `{"type":"error","code":"session.update.invalid",...}` |
| prompt > MAX_CHARS（POST） | 422 + `prompt.too_long` |
| audit_dir 不存在且不可创建 | session 创建照常成功；audit_writer=None；后续 write 调用 no-op + log warn |

---

## 7. 验收标准

### 7.1 autonomous（沙盒 + prod）

- A1 POST /v1/sessions `{"prompt":"x"}` → response `prompt="x"`
- A2 POST /v1/sessions `{}` → response `prompt = RTVOICE_DEFAULT_PROMPT`
- A3 POST /v1/sessions `{"prompt": "y"*2001}` → 422 `prompt.too_long`
- A4 GET /info `capabilities.default_prompt` 等于 env 值；`memory=true`，`memory_max_turns=6`
- A5 OpenAPI schema 含 `prompt` + `audit_persist` 入参
- A6 `audit_persist=true` + 1 个完整 turn → `/data/transcripts/{date}/{sid}.jsonl` 含 ≥4 行（partial/final/text累计/done）
- A7 audit dir 不可写 → ws 仍正常完成 turn（仅 log warn）

### 7.2 user-participation（浏览器）

与 SP2 延期的浏览器验收合并。需要一个**最小测试页**（SP3 plan 内含 T 加测试页 HTML）展示：

- B1 多轮对话：第 4 轮提"刚才你说什么" → agent 复述（验 memory 喂 LLM）
- B2 第 7 轮起：浏览器 console 看 `messages.length` 不再增长（验驱逐）
- B3 中途发 `session.update {prompt:...}` → 下一 turn 风格切换
- B4 STT 边说边显示 `transcript.partial`；agent 边答边显示 `response.text` delta
- B5 `audit_persist=true` 创建的 session 完成对话后，prod 服务器 `cat /data/transcripts/{date}/{sid}.jsonl` 看完整记录

---

## 8. 测试矩阵

| 类别 | 文件 | 数量 |
|---|---|---|
| unit `memory.py`（构造 / append / 驱逐 / size cap） | tests/test_memory.py | 4 |
| unit `audit.py`（put_nowait / queue full drop / IO 异常吞掉 / aclose drain / 路径生成） | tests/test_audit.py | 5 |
| session_manager 加 prompt/audit 字段（创建带 prompt / 带 audit_persist / hash_key 不变） | tests/test_session_manager.py 扩 | +3 |
| pipeline mock（messages 组装含 system+memory+user / response.text emit / memory append on done / memory NOT append on error / audit write call counts） | tests/test_pipeline_mock.py 扩 | +5 |
| endpoints（POST 含 prompt 入参 / 422 prompt 长 / info 含 default_prompt / WS session.update 改 prompt 路径） | tests/test_endpoints.py 扩 | +4 |
| **新增小计** | — | **21** |

SP2 时 28 测试；SP3 后 ≥49。

---

## 9. 风险表

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| audit IO 卡盘 → queue 持续满 | M | audit 数据丢失 | drop 策略保证 turn SLA；告警依赖 log warn 计数 |
| LLM 接口改签名破坏 agent-worker（v0.7 LiveKit demo） | L | demo 路径失效 | agent-worker 有自己的 llm_client copy；realtime-server 改不影响 |
| memory 内存膨胀 | L | OOM | cap=5 session × 12 条 × ≤4KB ≈ 240KB；远低风险 |
| session 跨 0 点的 audit 文件路径 | L | 切文件 | 已决：用创建日期，全程一个文件 |
| `session.update` 在 turn 进行中发 → 当下生效 vs 下一 turn 不一致 | M | 客户端预期歧义 | 已决：**下一 turn 生效**；明记 spec / docs/api/sessions.md |
| prompt 超长被 LLM 截断（context 满）| M | 回答质量降 | 2000 字符 cap + memory 6 轮 cap 联合控制；监测 LLM context overflow log |
| audit JSONL 文件被外部 tail/cat 时半行 | L | 解析失败 | flush 后是 line-buffered；`with open("a")` 一次写全行；行内无 newline（json.dumps 默认转义）|

---

## 10. 范围外（明确 NOT in SP3）

- voice / speed 热改（Q3=A 排除）
- `memory.clear` 单独事件（Q3=A 排除）
- prompt token 数验证（YAGNI；字符 cap 足够防滥用）
- audit 文件压缩 / 滚动归档（运维 cron 解决）
- 跨 session memory 共享 / 多用户隔离 / 知识库（SP4+ 范畴）
- WebRTC / LiveKit 集成（agent-worker v0.7 demo 维护模式，不演进）
- prompt 模板 / 变量插值（YAGNI；client 自己拼）

---

## 11. 实施切片建议（供 writing-plans 参考）

| Task | 文件 | 自治测试 |
|---|---|---|
| T1 | config.py 加 5 env vars + 测试 | 2 |
| T2 | memory.py + 测试 | 4 |
| T3 | audit.py + 测试 | 5 |
| T4 | session_manager.py 扩字段 + 测试 | +3 |
| T5 | llm_client.py 改签名 stream(messages) + 测试 | +1 |
| T6 | pipeline.py 重写 run_turn + 测试 | +5 |
| T7 | main.py POST 入参 + GET /info + WS session.update 路由 + 测试 | +4 |
| T8 | docker-compose.yml volume + .env.example | — |
| T9 | docs（README / OPERATIONS / CONVENTIONS / sessions.md / COZYVOICE_INTEGRATION 例） | — |
| T10 | static 测试页 HTML（多轮对话 + transcript.partial + response.text 显示） | — |
| T11 | CHANGELOG v0.10.0 + push | — |
| T12 | prod 部署 + autonomous 验收 + user 浏览器验收（合 SP2 延期项） | — |

---

## 附录：相关文档

- 前置：[SP2 设计](./2026-05-08-sp2-realtime-session-design.md) / [SP2 plan](../plans/2026-05-08-sp2-realtime-session.md)
- 协议：[CONVENTIONS.md](../../api/CONVENTIONS.md) / [sessions.md](../../api/sessions.md)
- 平台：[SP1 设计](./2026-05-07-sp1-platform-positioning-design.md)
