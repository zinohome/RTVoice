# RTVoice v0.7 Prod 验证报告

**日期**：2026-05-07
**目标机**：192.168.66.163 (RTX 3060 12GB)
**升级路径**：v0.6.0 (CosyVoice 2) → v0.7.0 (Fun-CosyVoice 3) + agent-worker rebuild
**git head**：`da78e29 fix(v0.7): DEFAULT_PROMPT_TEXT 末尾加 <|endofprompt|>`

---

## 1. 升级流程时间线

| 阶段 | 命令 | 耗时 |
|---|---|---|
| Step 0 探查 | SSH 连通 + git/env/docker/ports 只读 | 1 min |
| Step 1 备份 | `cp .env .env.bak.20260507-142830` | <1s |
| Step 2 git pull | `4442951..29a8b49`（8 个 commit）| <5s |
| Step 3 sed 替换 .env | 3 行精确替换（v0.6 → v0.7 三 key）| <1s |
| Step 4 build tts-server | 17.5GB 镜像 | **52 min**（pip 下载 TensorRT lib 占大头）|
| Step 5 up -d tts-server | 容器启 + modelscope 下载 ~5.6GB 模型 | 5 min |
| Step 6 验 /info | 确认 `backend=cosyvoice3 text_streaming=true` | <1s |
| Step 7a build agent-worker | 缓存命中只重 COPY app | <30s |
| Step 7b up -d agent-worker | 重启 + STT/TTS 重连 + capability probe | 30s |
| **修 bug**: DEFAULT_PROMPT_TEXT | 加 `<|endofprompt|>` + push + 拉 + rebuild + restart | 8 min |

总耗时：约 **70 分钟**（其中 build 阶段占 52 分钟）

---

## 2. 自动化测试矩阵（10/10 通过）

| # | 测试项 | 方法 | 结果 |
|---|---|---|---|
| T1 | 5 服务 /health | docker exec curl-equivalent | tts/stt/livekit/token/agent 全 200 ✓ |
| T2 | TTS HTTP `/tts/stream` | 合成 4.12s 音频 | ttfb=1146ms, RTF=0.58×, 200 OK ✓ |
| T3 | TTS WS `/tts/stream_ws` | 9 deltas 流式 + EOS | ttfb=1732ms, 2 chunks, RTF=0.43×, done=True ✓ |
| T4 | STT WS `/asr` | 0.5s 静音 + EOS | final 返回（空，符合无语音）✓ |
| T5 | token-server `/token` | Bearer + JSON body | 341 字符 JWT + url ✓ |
| T6 | LLM (host ollama) | agent-worker → host.docker.internal:11434 | qwen2.5:7b 返回中文回复 ✓ |
| T7 | 30min 日志 ERROR | grep -ciE error/exception | 0 真 error；6 条 livekit dtls warn 是重启噪音 ✓ |
| T8 | GPU 显存 | nvidia-smi | 4.7G/12G 用，余 7.6G ✓ |
| T9 | 4 服务 /metrics | docker exec urllib | 138/101/157/120 行，prometheus 文本 ✓ |
| T10 | agent capability probe | grep agent log | `backend=cosyvoice3 text_streaming=True` ✓ |

### T2 详情（HTTP 单次请求）
```
status=200 ttfb=1146ms total_bytes=197760 elapsed=2.37s audio_s=4.12 sr=24000 fmt=pcm-int16-le
```
RTF (real-time factor) = audio_s / elapsed = 4.12 / 2.37 = **1.74×**（GPU 比实时快 1.7 倍）

### T3 详情（WS 双向流式）
```
ttfb=1732ms chunks=2 total=295680B audio_s=6.16 elapsed=2.66s done=True
```
Server 端日志显示 v3 内部"边收 token 边 decode"机制激活：
```
append 5 text token 15 speech token
not enough text token to decode, wait for more
...
no more text token, decode until met eos
yield speech len 4.36, rtf 0.38
yield speech len 1.8, rtf 0.49
```

### T8 GPU 资源
```
4743 MiB / 12288 MiB used   (38%)
GPU util: 0% (idle when not synthesizing)
```
预留 7.6 GB 给可能的 vLLM 切换 / 多并发。

---

## 3. 修复的真 bug

### v0.7-fix-1: `<|endofprompt|>` 必须显式拼到 prompt_text

**触发**：T2 第一次 POST `/tts/stream` 60s 超时未返回。

**根因**：CosyVoice 3 LLM `inference()` 在 `cosyvoice/llm/llm.py:479` 有硬断言：
```python
assert 151646 in text, '<|endofprompt|> not detected in CosyVoice3 text or prompt_text'
```
v3 frontend 的 `text_normalize` / `_extract_text_token` 都不自动添加该 token；
caller 必须在 `prompt_text` 末尾显式拼接。v2 不要求此 token。

**修复**：commit `da78e29`，把 `DEFAULT_PROMPT_TEXT` 从 `"希望你以后能够做的比我还好呦。"` 改为 `"希望你以后能够做的比我还好呦。<|endofprompt|>"`。

**为什么 v0.6 沙盒 mock 测试发现不了**：FakeCosyVoice3 没真跑 LLM forward，断言不会触发。**这条 bug 必须在真 GPU 上才暴露**。OPERATIONS.md §3.2 已经标了"真 CosyVoice 3 推理性能 → prod GPU 实测" — 一发即中。

**Lesson learned**：开源 ML 项目的 undocumented contract（如 v3 的 `<|endofprompt|>` 必须显式拼）只能靠真实推理触发。沙盒/CI 单元测试覆盖不了"调用方约定"层。

---

## 4. 当前 prod 全景

```
SERVICE          IMAGE                                  STATUS
agent-worker     rtvoice/agent-worker:v0.5.0            Up (healthy, rebuilt 2026-05-07)
livekit-server   livekit/livekit-server:v1.11.0         Up 3 days
stt-server       rtvoice/stt-server-gpu:v0.5.0          Up (recreated, image unchanged)
token-server     rtvoice/token-server:v0.5.0            Up 3 days
tts-server       rtvoice/tts-server-cosyvoice3:v0.7.0   Up (NEW, 17.5GB image)
```

git: `da78e29` (origin/main 同步)

---

## 5. ⏳ 仅人能做的人工验证（待用户）

以下 5 项依赖**主观体感 + LiveKit 浏览器端**，SSH 内自动化测不了。

### 5.1 端到端对话（**必做**）
1. 浏览器打开 LiveKit 测试页或前端，拿 token 接入 `ws://192.168.66.163:7880` room=`rtvoice-test`
2. 说一句"你好，今天天气怎么样"
3. 听 agent 回复

**预期**：
- agent 在 1-2 秒内开始说话
- 中文女声（CosyVoice 3 zero-shot reference）
- 自然流畅、无电流声/卡顿

**自动化追溯**：操作完后看 `docker logs rtvoice-agent | grep WS-pipeline` 应有 `[WS-pipeline] first_audio_ms=...` 数字。

### 5.2 barge-in 验证
agent 说话期间，用户再次说话 → agent 应在 ≤ 1 秒安静下来（v0.7 加固过 `asyncio.shield(aclose)`）。

### 5.3 主观音质 vs v0.6 (可选)
回滚到 v0.6（OPERATIONS.md §3.3，秒级），说同一句对比 v0.7：自然度、音色一致性、停顿/语调。

### 5.4 长对话稳定性（>5 分钟）
**观察项**：
- `watch -n 5 nvidia-smi`：GPU mem 是否稳定（防泄漏）
- agent 是否卡住 / STT 重连日志频率
- LLM 响应时间分布（看 grafana 或 `/metrics`）

### 5.5 可选：启 RTVOICE_API_KEY
若要给"另一个项目"用 STT/TTS：
1. `.env` 设 `RTVOICE_API_KEY` 和 `TTS_ADMIN_API_KEY`（≥32 字符）
2. rebuild stt-server + tts-server + agent-worker
3. 验 `curl /info` 不带 token 返 401

---

## 6. 已知运维风险（轻量）

- **build 缓存增长**：每次 rebuild tts-server 会多攒 buildkit 缓存。当前 169GB（120GB 可回收）。**不要主动 prune**；硬盘紧张时再考虑。
- **livekit 重启 dtls 警告**：每次 agent-worker 重启会刷 6 条 `dtls timeout` warn。无害。
- **/data 89% 满**：现有占用与升级无关；如果用户后续要扩 voice clone wav 库或日志，注意 80%+ 阈值。

---

## 7. 回滚命令（紧急）

```bash
ssh root@192.168.66.163
cd /data/RTVoice
sed -i '/^TTS_DOCKERFILE=Dockerfile.cosyvoice3$/d' .env
sed -i '/^TTS_IMAGE=rtvoice\/tts-server-cosyvoice3:v0.7.0$/d' .env
sed -i '/^TTS_MEM_LIMIT=10G$/d' .env
# 加回 v0.6 三行（备份在 .env.bak.20260507-142830 可对照）
# 或手工：
# echo "TTS_DOCKERFILE=Dockerfile.cosyvoice" >> .env
# echo "TTS_IMAGE=rtvoice/tts-server-cosyvoice:v0.6.0" >> .env
# echo "TTS_MEM_LIMIT=8G" >> .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod up -d tts-server agent-worker
```

镜像/卷都未删，秒级回到 v0.6。
