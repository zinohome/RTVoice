# RTVoice 运维手册

面向**已经部署 RTVoice 的运维者**：怎么升级、怎么排障、各组件失败时会怎么自愈。
新部署看 [DEPLOY.md](./DEPLOY.md)；安全契约看 [SECURITY.md](./SECURITY.md)；架构看 [ARCHITECTURE.md](./ARCHITECTURE.md)。

---

## 1. 容错矩阵（v0.6.2 完整图）

下表枚举每个长连接 / RPC 链路的故障模式与自愈策略。**绿色（✓）= 自动恢复，运维不需要介入**；其他需要看具体行为说明。

| 故障 | 自愈策略 | 实现位置 | 用户感知 |
|---|---|---|---|
| LiveKit room 断 | 5 次指数退避重连 | `agent-worker/main.py::_reconnect` | 静默恢复 |
| STT 容器 cold start | connect 5 次指数退避（1→2→4→8→16s）| `stt_client.py::_connect_with_retry` | 启动慢一点 |
| STT mid-conv 断连 | reader finally 调度后台重连 | `stt_client.py::_schedule_reconnect` | 当前 utterance 丢失，下一句正常 |
| STT 完全连不上 | retry 用尽抛 `ConnectionError` | `stt_client.py::_connect_with_retry` | agent 启动失败；运维介入 |
| LLM 容器重启 | `openai` SDK 自带 `max_retries=2` | SDK 默认 | 静默恢复 |
| LLM read 卡死（GPU thermal/OOM）| `httpx.Timeout(read=30s)` per-chunk timeout | `llm_client.py` | 当轮失败 → fallback 回复 |
| LLM 0 token 回复 | yield `LLM_FALLBACK_REPLY` | `llm_client.py::stream` | 听到"抱歉没听清，再说一次" |
| LLM 半句中异常 | 截断（不拼 fallback，避免半句续接很怪）| `llm_client.py::stream` | 听到半句话，自然要重复 |
| TTS HTTP client disconnect | server 端 `request.is_disconnected()` 检测 | `main_cosyvoice.py::_synthesize_stream` | 推理及时停止 |
| TTS WS barge-in close | `asyncio.shield(aclose)` 让 close frame 真发出 | `agent-worker/main.py::_run_pipeline_ws` | 立刻安静（GPU ≤1 chunk 浪费）|
| TTS WS server send-after-close | 接 `(WebSocketDisconnect, RuntimeError)` | `main_cosyvoice3.py::tts_stream_ws` | server 不 traceback |
| sherpa-onnx 内核 race | single-coroutine WS handler + endpoint detection 关闭 | `stt-server/main.py::asr_ws` | "STT 连接已关闭"故障消失（v0.5.3 已修）|

**规则**：可以自动恢复的就自动恢复；不能恢复的（如 retry 用尽）抛异常让上游看见，避免静默故障。

---

## 2. 重要环境变量速查

按"是否影响可用性"分级。完整列表见 `.env.example`。

### 2.1 必须配置（v0.6+）

| 变量 | 用途 |
|---|---|
| `LIVEKIT_API_KEY/SECRET` | LiveKit JWT 签名 |
| `APP_API_KEY` | token-server 鉴权（≥32 字符）|
| `BIND_HOST` | 端口绑定地址；prod 必须显式 |
| `RTVOICE_NODE_IP` | LiveKit 对外宣告 IP（prod）|
| `LIVEKIT_PUBLIC_URL` | 浏览器侧 ws 地址（prod）|

### 2.2 STT/TTS 对外鉴权（v0.6.1+）

| 变量 | 默认 | 说明 |
|---|---|---|
| `RTVOICE_API_KEY` | 空（鉴权关）| STT WS + TTS HTTP/WS Bearer；公网暴露必填 |
| `TTS_ADMIN_API_KEY` | 空（admin 关）| `/voices/add` `/voices/{id}` 单独 key |

### 2.3 容错调参（v0.6.2+）

| 变量 | 默认 | 调整时机 |
|---|---|---|
| `LLM_CONNECT_TIMEOUT_S` | 10 | 容器启动慢时调大 |
| `LLM_READ_TIMEOUT_S` | 30 | LLM cold start 长（vLLM 首次推理）调大到 60 |
| `LLM_FALLBACK_REPLY` | "抱歉，我现在没听清楚..." | 想换语气 |
| `STT_CONNECT_RETRIES` | 5 | 容器拉起慢时调大 |
| `STT_CONNECT_BACKOFF_INITIAL_S` | 1.0 | — |
| `STT_CONNECT_BACKOFF_MAX_S` | 16.0 | — |

### 2.4 v0.7 (Fun-CosyVoice 3)

| 变量 | 说明 |
|---|---|
| `TTS_DOCKERFILE=Dockerfile.cosyvoice3` | 切到 v3 镜像构建 |
| `TTS_IMAGE=rtvoice/tts-server-cosyvoice3:v0.7.0` | 切到 v3 镜像 tag |
| `TTS_MEM_LIMIT=10G` | v3 模型 ~5.6GB 推理 buffer |
| `SKIP_RL_MODEL=1` | 默认跳过 `llm.rl.pt`（省 2GB）|

---

## 3. 升级路径

> 原则：**永远 v0.6.1 优先稳定**。v0.7 验证完再切，**镜像/卷各自独立可瞬间回滚**。

### 3.1 dev → v0.6.1（首次升级）

```bash
git pull
# .env 加新变量（生成 32 字符 key）：
python3 -c "import secrets; print('RTVOICE_API_KEY=' + secrets.token_urlsafe(32))" >> .env
python3 -c "import secrets; print('TTS_ADMIN_API_KEY=' + secrets.token_urlsafe(32))" >> .env

# 重建 + 重启
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile prod build agent-worker tts-server stt-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile prod up -d
```

**冒烟测试**：
```bash
# 应 401（无 token）
curl -i http://127.0.0.1:9880/voices
# 加 token 应 200
curl -i -H "Authorization: Bearer $RTVOICE_API_KEY" http://127.0.0.1:9880/voices
# agent-worker 日志应有 [TTS-probe] backend=cosyvoice2 text_streaming=False
docker logs rtvoice-agent | grep TTS-probe
```

### 3.2 v0.6.1 → v0.7（CosyVoice 3 切换）

```bash
# .env 加：
cat >> .env <<'EOF'
TTS_DOCKERFILE=Dockerfile.cosyvoice3
TTS_IMAGE=rtvoice/tts-server-cosyvoice3:v0.7.0
TTS_MEM_LIMIT=10G
EOF

# 重建（首次 ~10 分钟）+ 拉模型（首启 ~5.6GB）
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile prod build tts-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile prod up -d tts-server agent-worker
```

**验收**：
```bash
# /info 应有 backend=cosyvoice3 + text_streaming=true
curl -s -H "Authorization: Bearer $RTVOICE_API_KEY" http://127.0.0.1:9880/info | jq

# agent 日志应转向 ws 路径
docker logs -f rtvoice-agent | grep -E "TTS-probe|WS-pipeline"
# 期望：
#   [TTS-probe] backend=cosyvoice3 text_streaming=True
#   [WS-pipeline] first_audio_ms=...    ← 期望 < 500ms（理想 ≤ 200ms）
```

### 3.3 v0.7 → v0.6.1（回滚）

```bash
# .env 删 v0.7 三行（或注释）：
sed -i '/^TTS_DOCKERFILE=/d; /^TTS_IMAGE=/d; /^TTS_MEM_LIMIT=/d' .env

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile prod up -d tts-server agent-worker
```

回滚秒级生效（v0.6 镜像 + 模型卷都还在）。

---

## 4. 排障 cookbook

### 4.1 agent 不说话（沉默）

按概率从高到低：

1. **TTS 健康**：`curl /health`；`docker logs rtvoice-tts`，确认 `recognizer ready`/`CosyVoice 加载完成`
2. **LLM 路径**：`docker logs rtvoice-agent | grep "[LLM]"` 找最近的 `user=...` 和 `reply=...`
   - 看到 `0 token emitted → 发 fallback` = LLM 真返回空。检查 LLM_BASE_URL 是否对、模型名是否对、ollama list 看模型是否拉了
   - 看到 `stream 异常` = LLM 连接失败。检查容器、网络
3. **STT 路径**：`grep "[STT]"`，看 `final` 是否非空。空 → 麦克风/VAD 问题，看 ARCHITECTURE.md 调 VAD 阈值
4. **WS 路径下"agent 沉默+服务端日志正常"**：可能是 LiveKit publish_track 失败。看 `[FSM] -> SPEAKING` 是否触发

### 4.2 STT 连接拒绝 / 重连日志刷屏

看 `[STT 自动重连开始]` `[STT 连接失败 #N]`：
- 偶尔几次 + 最终成功 = 正常自愈，不用管
- 持续 5 次失败 → `STT 自动重连失败` = stt-server 真挂了。`docker compose ps`、`docker logs rtvoice-stt`

如果想加大容忍度：
```bash
echo "STT_CONNECT_RETRIES=10" >> .env
echo "STT_CONNECT_BACKOFF_MAX_S=30" >> .env
docker compose ... up -d agent-worker
```

### 4.3 v0.7 切换后没看到 ws 路径激活

诊断顺序：
1. `curl /info | jq .text_streaming` → 必须是 `true`。false = 还在用 v0.6 镜像，检查 `docker images | grep tts-server-cosyvoice3` 是否存在
2. `[TTS-probe] backend=cosyvoice3 text_streaming=True` 是否出现 → 否 → agent-worker 没重启或没拉到 /info
3. 第一次对话后看 `[WS-pipeline] first_audio_ms=...` → 没出现 = ws 连接失败，看上一行错误
4. 验本地 ws 通：
```bash
python3 -c "
import asyncio, websockets, json
async def t():
  async with websockets.connect('ws://127.0.0.1:9880/tts/stream_ws',
    additional_headers={'Authorization':'Bearer $RTVOICE_API_KEY'}) as ws:
    await ws.send(json.dumps({'voice':'default_zh_female'}))
    await ws.send('你好')
    await ws.send('EOS')
    async for m in ws:
      if isinstance(m, bytes): print(f'pcm {len(m)}B'); continue
      print(m); break
asyncio.run(t())
"
```

### 4.4 barge-in 后还能听到 1-2 秒残余音频

正常现象。`[ws-tts] client disconnected` 后 server 还要消耗 inference pipeline 里已经入队的 PCM。**预期上限 1 个 chunk** (~200ms)。

如果残余 > 2 秒持续出现，说明 `asyncio.shield(ws.aclose(), timeout=2)` 里的 close frame 没发到 server，server 只能靠 TCP RST 才察觉。看 agent 日志是否有 `aclose` 异常；通常是 LiveKit room cancel 抢占了 shield —— 暂时没好办法（asyncio 限制），只能加大 inference batch 节奏让浪费变小。

### 4.5 voice clone：注册自定义音色

```bash
# 准备 16kHz mono wav（3-30 秒），ffmpeg 转：
ffmpeg -i input.mp3 -ar 16000 -ac 1 -sample_fmt s16 ref.wav

# POST 注册
curl -X POST http://127.0.0.1:9880/voices/add \
  -H "Authorization: Bearer $TTS_ADMIN_API_KEY" \
  -F spk_id=alice \
  -F prompt_text="参考音频对应的文本（≥3 秒发音内容）" \
  -F file=@ref.wav

# 验证 + 用
curl -s -H "Authorization: Bearer $RTVOICE_API_KEY" \
  http://127.0.0.1:9880/voices | jq
# .env 改 TTS_VOICE=alice 重启 agent-worker

# 删除
curl -X DELETE http://127.0.0.1:9880/voices/alice \
  -H "Authorization: Bearer $TTS_ADMIN_API_KEY"
```

注册的 wav 持久化到 named volume（`rtvoice_cosyvoice_models` 或 `_v3` 卷的 `voices/` 子目录）。删 volume 才会丢。

---

## 5. 监控检查项

如果你跑了 `docker-compose.monitoring.yml`，Grafana 看这些指标判健康：

- `rtvoice_round_seconds` p95 < 5s
- `rtvoice_first_audio_seconds` p95 < 1s（v0.6）/ < 0.3s（v0.7 ws 路径）
- `rtvoice_stt_decode_seconds` p95 < 0.05s
- `rtvoice_tts_phrase_rtf` median > 1.0（rt-factor，>1 = 比实时快）
- `rtvoice_tts_ttfb_seconds` p95 < 0.8s（v0.6）/ < 0.2s（v0.7）

容器层指标看 `cadvisor`/`node-exporter`：
- agent-worker 内存稳定 < 600MB；超过说明 PCM 队列在堆积，看是否 LiveKit publish 卡住
- tts-server GPU 显存稳定（v0.6 ~3.5GB；v0.7 ~5.5GB）

---

## 6. 已知限制

- **重连后 sherpa-onnx 是新 stream**：当前 utterance 数据丢失，用户需重复一次。这是设计权衡（用"丢局部"换"全局可用"），不是 bug。
- **mid-stream LLM 异常截断不拼 fallback**：用户听到半句话停止。设计上是为了避免"半句拼一句兜底"听起来很奇怪。如果想改成更激进的恢复，改 `llm_client.py::stream`。
- **CosyVoice 3 实际 150ms 延迟未在受限沙盒验证**：所有协议层测试通过，但端到端延迟数字必须在 prod GPU 上测出来。如果 ws 路径没明显比 v0.6 phrase pipeline 快，看是不是 GPU 显存满了 / 模型 fp16 没生效。

---

## 7. 升级历史快查

| 版本 | 主线变化 | 核心 commit |
|---|---|---|
| v0.6.0 | CosyVoice 2 GPU 接入，Kokoro 仍可切 | `4442951` (zero-shot fix) |
| v0.6.1 | env-prompt + voice clone API + Bearer auth + TLS 模板 | `2ce1621` `4bff9f0` `5da290a` |
| v0.7.0 | Fun-CosyVoice 3 baseline（与 v0.6 并存）| `af919a4` |
| v0.7.0 | TTS WebSocket 双向流式（agent 自动检测）| `b658573` |
| v0.7.0 | barge-in cancel 加固 | `c54f963` |
| v0.6.2 | LLM timeout/fallback + STT 重连 | `4ad21fc` `82b8628` |
| v0.7-fix-1 | prod 实测 bug：v3 prompt_text 必须含 `<|endofprompt|>` | `da78e29` |
| v0.7.1 | Dockerfile chown 重排，code-only rebuild 240× 加速 | `8ed9a4d` |

完整记录见 [CHANGELOG.md](./CHANGELOG.md)。

---

## 8. Build 性能 & Docker 缓存（v0.7.1+）

ML 镜像（venv 60GB+）的 build 缓存策略与普通 web 镜像不同。`Dockerfile.cosyvoice3` 已按下面规则优化（v0.7.1，commit `8ed9a4d`）。

### 8.1 黄金法则：易变层放后面，重型操作放前面

错误做法（v0.7.0 一开始）:
```dockerfile
RUN pip install -r requirements.txt        # 缓存稳定
COPY app /app/app                           # 易变
RUN useradd && chown -R /app /opt/venv      # ❌ 跟 COPY 后面 → 每次都跑 215s
```

正确做法（v0.7.1）:
```dockerfile
RUN pip install -r requirements.txt        # 缓存稳定
RUN useradd && chown -R /opt/venv /opt/CosyVoice   # ✅ 重型 chown 在 COPY 前 → 缓存命中
COPY --chown=appuser:appuser entrypoint /entrypoint.sh
COPY --chown=appuser:appuser app /app/app  # ✅ 用 --chown 内联，避免后续重 chown
USER appuser
```

### 8.2 实测对比（rtvoice/tts-server-cosyvoice3:v0.7.0）

| 场景 | 优化前 | 优化后 | 加速 |
|---|---|---|---|
| 全量 build（缓存空） | ~52 min | ~52 min | — |
| **code-only rebuild** | **~215 sec** | **~1 sec** | **215×** |
| 无改动 rebuild | ~30s | ~1s | 30× |

### 8.3 BuildKit content-hash 陷阱

**用 `touch` 改 mtime 不会触发 BuildKit cache miss**。BuildKit 看的是文件内容 SHA，不是 mtime。从老 docker build 思维迁移时常踩此坑。

测 cache 行为时：
- ❌ `touch app/main.py && docker compose build`  →  cache 全命中（误判优化失效）
- ✅ `echo "" >> app/main.py && docker compose build`  →  cache 失效（真测）
- ✅ `git diff` 显示有变化才算"真改动"

### 8.4 chown -R 在大目录上的成本

实测 `chown -R appuser:appuser /opt/venv`（约 60GB，1.5M+ 文件）= **215 秒**（ext4，单磁盘）。这种 inode 操作没法靠 `--no-cache` 之外的方法跳过，只能避免它落在易变层后面。

### 8.5 不要主动 prune build cache

`/var/lib/docker/buildkit` 里的 build cache 看起来很大（120GB+ 可回收）但**不要主动 prune**：
- 全量 rebuild 一次要 50+ 分钟，缓存能让 code-only rebuild 变 1 秒
- 仅在硬盘真不够（< 30GB 余量）时才 `docker buildx prune --keep-storage 50G`
- 永远 `--keep-storage` 设阈值，不要光秃秃 `prune -a -f`
