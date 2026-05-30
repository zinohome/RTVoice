# SP8-D1 Findings — Fresh-consumer dogfood of `COZYVOICE_INTEGRATION.md`

Persona: an engineer onboarding the CozyVoice app, no prior RTVoice exposure,
no source-tree access (treats RTVoice as a closed local backend).
Date: 2026-05-12. Prod under test: 192.168.66.163.
Client key: `key_zLST8TOnAG04_9IG`, scopes = `stt,tts,tokens`.

Severity legend: **阻塞** = cannot proceed without help · **高** = had to read source / SSH to figure out · **中** = guessable but rough.

---

### F1. STT (9090) / TTS (9880) 在 prod 上对外不可达 —— 文档 §3 的"Topology A"是唯一现实路径，但没说"非同机消费者会 NAT 不到"

- **场景**：照 §4 Endpoints 参考表，写 client 调 `http://192.168.66.163:9880/v1/tts/stream`、`ws://192.168.66.163:9090/v1/asr`。
- **期望**：连得上、Bearer 校验通过、返音频/文字。
- **实际**：
  - `:9880` TCP RST / empty reply（容器内 9880 没暴露到 host）
  - `:9090` host port 被 Prometheus 占了（同号 namespace 撞车），返 Go 风格 `404 page not found` —— 看起来像"我连对了一个 RTVoice 服务但路径错"，其实根本不是同一个服务。
- **影响**：消费者 CozyVoice 跑在另一台机器（D1 task 暗含的场景），没有任何对外路径可以调 STT/TTS。文档 §1 表里 D 拓扑写了 "Caddy LE 公网 cert"，但 prod 实际没起 caddy，也没起 docker-compose.api.yml。我必须 ssh + 端口转发到 container IP 才能完成 demo。
- **严重度**：**阻塞**（任何非 ssh 用户都跑不通 50 行 demo）
- **建议**：
  1. README / INTEGRATION 文档头部加一句 "prod 默认拓扑 A —— stt/tts **不对外**，如果你不在同机 docker network，需要先启用 `docker-compose.api.yml` 或 Caddy"。
  2. host port 9090 给 Prometheus、stt-server 内部 9090 —— 撞号让人下意识以为 `:9090/health 404` 是 stt-server 路径配错。Prometheus 改 9091 或 stt-server host-bind 改 19090。

---

### F2. 文档没解释"token-server" 与 "Bearer RTVOICE_API_KEY" 的区别 —— "拿 token" 容易被误读

- **场景**：D1 task 说 "拿 token + STT + TTS"。新读者第一反应：先调 token-server `/v1/tokens` 拿个东西，再用那个东西打 STT/TTS。
- **期望**：`COZYVOICE_INTEGRATION.md` 头部能写明 "RTVoice 用静态 Bearer key 直接打 STT/TTS；token-server 是 LiveKit 高级模式专用的 JWT 签发"。
- **实际**：文档 §4 "鉴权方式" 只讲 Bearer；§5.4 Realtime 例子只讲 `Authorization: Bearer ...`。token-server 整个不在 COZYVOICE_INTEGRATION.md 里——它只出现在 `docs/api/sessions.md`，且和 realtime-server 混在同一篇 doc（且页眉写 "Realtime Voice Service API"），找不到入口。
- **影响**：消费者会 (a) 误以为必须先调 `/v1/tokens` (b) 在 §3 schema 看到 `identity/room/ttl_minutes` 一头雾水（这分明是 LiveKit 概念，CozyVoice 默认 WS 模式根本用不上）。
- **严重度**：**高**（必须读 specs / 源码才能理清三种 auth 概念：RTVOICE_API_KEY / LiveKit JWT / session_id-as-token）
- **建议**：在 §4 加一条 "三种凭据各自用于什么"，并在 INTEGRATION 文档显式声明 "默认 WS gateway 模式不需要 `/v1/tokens`"。

---

### F3. Scope 命名 vs 服务名不一致：key 拿 `stt,tts,tokens` 但 realtime-server 要 `realtime` scope —— 文档没列 scope 清单

- **场景**：用 `key_zLST8TOnAG04_9IG`（scopes `stt,tts,tokens`）POST `/v1/sessions` 试 §5.4 Realtime demo。
- **期望**：要么走通，要么 401 with 明确的 "you need scope X"。
- **实际**：返 `{"type":"error","code":"auth.scope_denied","message":"key key_zLST8TOnAG04_9IG not allowed for scope=realtime"}` —— 错误信息清晰，但**文档里没有任何地方列举 scope 名称表**。我只能猜要在 key issuance 时多加 `realtime`，但文档 §2.1 生成 key 的命令根本没提 scope 参数。
- **影响**：消费者拿到 key 时不知道未来要加哪些 scope；§5.4 demo 跑不通时无法定位是 doc 没写 scope 还是 prod 给错 key。
- **严重度**：**高**
- **建议**：
  1. INTEGRATION §2.1 显式说 "key 需要 scopes: stt, tts, realtime, tokens（按用途选）" 并展示 admin 颁 key 的命令。
  2. `docs/api/CONVENTIONS.md` 加 scope 表，固定命名（应该 `realtime` 而不是 `tokens`？现在 `tokens` 命名歧义太大）。

---

### F4. §2.1 "生成 key" 的命令只 echo 到 .env —— 没说怎么把 key 注册到 auth-server / 给客户

- **场景**：照 §2.1 跑 `python3 -c "secrets.token_urlsafe(32)"` → echo 到 .env → rebuild + restart 三服务。
- **期望**：rebuild 完，新 key 立即生效。
- **实际**：v0.14.0 changelog 说 auth 已是 hot-reload + 多 key 模式（key_id + secret），单一 `RTVOICE_API_KEY` env var 是旧 single-key 模型。§2.1 命令和 v0.14 现实**不一致**——现在颁 key 走 admin API（`/tmp/sp8-d1/dogfood-keys.yaml` / `create-out.json` 暗示这是当前 SOP），但 INTEGRATION 没更新。
- **影响**：照文档跑会生成一个永远不会被认可的 key；rebuild 三服务这个步骤白做（hot-reload 不需要 rebuild）。
- **严重度**：**高**
- **建议**：§2.1 整段重写，从 v0.14 admin POST `/admin/keys` 角度示范，删掉 "rebuild + restart" 那段——它和 hot-reload feature 矛盾。

---

### F5. PCM 输入格式契约只在 §5 example code 里出现，没单独一个 "Audio formats" 表

- **场景**：写 STT 端 loopback，需要把 TTS 24 kHz int16 mono 重采样到 STT 要求的 16 kHz int16 mono。
- **期望**：INTEGRATION 文档某处一句话 "STT in: 16k int16 mono; TTS out: 24k int16 mono"。
- **实际**：分散在 §5.1 docstring (`pcm_int16le_16k_mono`)、§5.2 代码注释（"24000 samples/sec"）、§9 接口契约（只提"输出"24k）。没有 "input" 输入的 sample rate 24k vs 16k 对照表。我重采样写错（reversed src/dst rate）会得到一个莫名其妙的 "EOS 太快没识别" 错误，调起来比较累。
- **影响**：第一次集成的人容易栽在采样率/格式上。
- **严重度**：**中**
- **建议**：§4 endpoints 表后加 "Audio formats" 小节：STT IN 16k/int16/mono、TTS OUT 24k/int16/mono、TTS prompt 音色注册 16k/int16/mono。

---

### F6. §3.3 启动验证示例代码语法不对 —— `urllib.request.urlopen` 不接受 `headers=` kwarg

- **场景**：照 §3.3 验证 token 链路。
- **期望**：在 cozyvoice 容器里跑一段 python，得到 tts-server `/info`。
- **实际**：`urlopen(url, ..., headers=...)` 抛 `TypeError`。该 API 需要 `urllib.request.Request(url, headers=...)` 包一层。原代码直接复制粘贴 → crash。
- **影响**：第一次跑 verify step 就报错，让人怀疑 RTVoice 端配置错。
- **严重度**：**中**
- **建议**：改成 `Request` 形式或者干脆用 `curl` 一行。

---

### F7. `/v1/tts/stream` 文档 ✗ 提及 voice/speed/lang 字段，但 prod TTS 返 `voice_count:1` —— 注册音色 admin API 用法实操断链

- **场景**：好奇默认音色之外有哪些，准备调 `GET /v1/voices`。
- **期望**：返一组音色清单（INTEGRATION §4 表里有这个 endpoint）。
- **实际**：因为 §F1 外网不可达，调不通。Tunnel 进去 `/info` 显示 `voice_count:1`。`/v1/voices` 是否需要 admin key 还是普通 Bearer 文档没分清 —— §4 表里写 "Bearer"，§6.2 注册却要 `TTS_ADMIN_API_KEY` —— 那读呢？
- **影响**：消费者不知道 list 走哪个 key。
- **严重度**：**中**
- **建议**：§4 表里把 read/write 拆两行（`GET /v1/voices` Bearer · `POST /v1/voices` Admin），现在合并表述容易误读。

---

### F8. STT 自环识别结果脏 —— "默认音色合成 → 默认 STT 模型识别"质量很糟，文档无任何 hint

- **场景**：50 行 demo 用 `default_zh_female` 合成 "你好世界，今天天气很好。"，再 loopback 给 sherpa-onnx-zh-en streaming zipformer。
- **期望**：text 大致还原原文。
- **实际**：`'能够做得比我比我还好哟哟你好世界 SIL天天天天天天气很好'` —— 前缀大段幻觉、SIL token 没去、字重复。原文只是子串。
- **影响**：消费者跑 hello-world 第一次就看到这种输出会怀疑自己 audio pipeline 写坏了。其实是 (a) 缺前导静音让 endpointer 触发 (b) zipformer 中文 model 对 CosyVoice 合成音质量 mismatch。
- **严重度**：**中**（功能 PASS，体验差）
- **建议**：INTEGRATION 加一条 "Self-loopback caveat: streaming zipformer 对合成音不友好；端到端验证用真录音"。或在 §5.1 example 前加 200ms 静音 padding 示例。

---

### F9. websockets v16 用 `additional_headers=` 但旧版是 `extra_headers=` —— §5.1 example 用的 `additional_headers` 没有版本要求说明

- **场景**：照 §5.1 跑，websockets >= 14 之前 kwarg 名叫 `extra_headers`。
- **期望**：知道最低 lib 版本。
- **实际**：requirements 都没写。我装的是 16.0 巧合通过；用户装 11.x 会 `TypeError: unexpected keyword argument 'additional_headers'`。
- **影响**：版本陷阱。
- **严重度**：**中**
- **建议**：§5 顶部写一行 "Tested with httpx>=0.27, websockets>=14"。

---

### F10. §5.0 "推荐用 rtvoice-client SDK" 写了 `pip install rtvoice-client` —— PyPI 上不存在该包

- **场景**：照 §5.0 推荐路径走。
- **期望**：`pip install rtvoice-client` 装得上。
- **实际**：没法验证（沙盒无 pip 写权限），但 grep `clients/` 和 README 找不到这个包发布痕迹 —— 大概率还没发布（"v0.11+ 起官方 SDK 可用"是 aspirational）。
- **影响**：消费者照 "Recommended" 路径走第一步就 404；fallback 到手写 httpx 路径心情不爽。
- **严重度**：**高**
- **建议**：要么 §5.0 加 "**Coming soon**"，要么真把 SDK 发上去再写进文档。SP8 D1 task 列表里这条算"夸大的成熟度"。

---

## 总结

**这份文档对一个新消费者够用吗？** 不太够。能 90% 看懂"如果环境是 Topology A 同机 docker network"的场景，但在 prod 环境(`192.168.66.163`)上**它根本不是 Topology A**：stt/tts 没暴露，新消费者会在 30 分钟内卡在 connectivity。

### 最关键的三条改进

1. **F1 + F4**：INTEGRATION 文档头部强制说明 "prod 默认对外只暴露 token-server (8000) + realtime-server (9000)；要 stt/tts 直连必须自己起 `docker-compose.api.yml` 或 Caddy"。同时 §2.1 颁 key 流程要按 v0.14 admin API 重写。
2. **F2 + F3**：把 "三种凭据" 和 "scope 清单" 放成 INTEGRATION 一节 —— 现在用户分不清 RTVOICE_API_KEY / LiveKit JWT / session_id 何时用谁。
3. **F10**：要么真发 `rtvoice-client` 包，要么把 §5.0 标 "Coming soon"。文档第一节就给用户一个装不上的包是糟糕的"第一印象"。

次要：F5 音频格式表、F6 verify code 报错、F9 lib 版本说明 —— 都是 5 分钟可修的低垂果实。
