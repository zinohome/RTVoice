# RTVoice

**RTVoice** —— self-hosted 语音服务平台，三个 service 一等公民：

1. **STT 服务** —— 实时流式转写（sherpa-onnx，WebSocket）
2. **TTS 服务** —— 流式合成 + 音色克隆（Fun-CosyVoice 3，HTTP + WebSocket）
3. **Realtime Voice 服务** —— 端到端语音对话（默认 WebSocket gateway / 可选 LiveKit；本地 LLM；支持 prompt+memory + 同步 transcript + 换音色）

全栈本地推理，单 GPU ≤ 12GB（RTX 3060/4060 适配），docker-compose 一键启停。

通过标准 HTTP / WebSocket API 给任意应用接入；内置鉴权、审计开关、用量监控、管理 Web UI。

---

## ⚡ 60 秒试一下

```bash
git clone https://github.com/zinohome/RTVoice.git
cd RTVoice
cp .env.example .env       # 默认 dev 配置已可用
docker compose --profile dev up -d
```

服务起来后:

| 想试什么 | 怎么试 |
|---|---|
| **STT**（语音转文字）| [测试页](http://127.0.0.1:8000/) 录一段；或编程方式见 [STT 集成示例](./COZYVOICE_INTEGRATION.md) |
| **TTS**（文字转语音）| `curl -X POST http://127.0.0.1:9880/v1/tts/stream -d '{"text":"你好"}' \| ffplay -f s16le -ar 24000 -` |
| **Realtime 对话**（API 方式）| `curl -X POST http://127.0.0.1:9000/v1/sessions -d '{}' -H "Content-Type: application/json"` 拿 ws_url，然后 websocat 连 |
| **Realtime 对话**| 浏览器 [测试页](http://127.0.0.1:8000/) → 加入语音 → 说话 |

**Realtime Voice 完整能力（v0.10+）**: 多轮记忆 / 流式 transcript+text / 中途换 prompt / 异步 audit JSONL。详见 [SP3 spec](./docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md)。

**首次启动注意**：LLM (Ollama) 需要 `ollama pull qwen2.5:1.5b`（约 1GB）。完整下好后约 3-5 分钟可对话。prod GPU 部署见 [DEPLOY.md](./DEPLOY.md)。

---

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

---

## What's in the box

### 🎤 STT — 流式语音识别

- **接口**：WS `/v1/asr`
- **引擎**：sherpa-onnx Streaming Zipformer 中英文
- **协议**：PCM int16 LE 16kHz mono in → JSON `{partial,final,error}` events out
- **场景**：实时转写、麦克风听写、对话录音
- → [集成示例](./COZYVOICE_INTEGRATION.md) · [API spec](./docs/api/stt.md)（即将上线）

### 🔊 TTS — 流式语音合成 + 音色克隆

- **接口**：HTTP POST `/v1/tts/stream`（单次）+ WS `/v1/tts/stream_ws`（双向流式）
- **引擎**：Fun-CosyVoice 3 (0.5B GPU)
- **协议**：text in（HTTP body 或 WS 流）→ chunked PCM int16 LE 24kHz mono out
- **特性**：音色克隆（POST /v1/voices）、speed 0.5-2.0
- → [集成示例](./COZYVOICE_INTEGRATION.md) · [API spec](./docs/api/tts.md)（即将上线）

### 💬 Realtime Voice — 实时语音对话

- **接口**：HTTP POST `/v1/sessions` 创建 + WS `/v1/realtime/{session_id}` 连接
- **协议**：客户端发 PCM in / 收 PCM + transcript events out（OpenAI Realtime 风格）
- **引擎**：内部 STT (sherpa) + LLM (Ollama / vLLM) + TTS (Fun-CosyVoice 3)
- **特性**：双向流式、prompt+memory、同步 transcript、换音色、barge-in
- **高级模式**：LiveKit endpoint 可选保留（适合 end-user 跨公网移动场景）
- → [集成示例](./COZYVOICE_INTEGRATION.md) · [API spec](./docs/api/sessions.md)

---

## 🔌 集成 (Integration)

给客户端 / 应用开发者：怎么把 RTVoice 接到你的项目。

- 完整集成手册：[COZYVOICE_INTEGRATION.md](./COZYVOICE_INTEGRATION.md)
- API spec（路径/鉴权/错误码统一规范）：`docs/api/`（即将上线，SP1.5）
- 客户端示例代码：Python、curl、JavaScript（在 COZYVOICE_INTEGRATION 里）
- 鉴权：Bearer token（[SECURITY.md](./SECURITY.md)）
- 部署拓扑选择（同机 docker network / 跨机 TLS / 公网 LE）：见集成手册 §1

---

## 🛠 部署 (Deployment)

给运维 / 部署人员：怎么把 RTVoice 跑起来。

- **首次部署**：[DEPLOY.md](./DEPLOY.md)
- **运维手册**（容错矩阵 / 排障 / 升级路径 / build 性能）：[OPERATIONS.md](./OPERATIONS.md)
- **硬件要求**：单 GPU ≤ 12GB（RTX 3060/4060 实测 OK）；CPU only 模式仅 dev 用（性能不足）
- **监控**：可选启 `--profile monitoring` 起 Prometheus + Grafana
- **安全**：[SECURITY.md](./SECURITY.md)（公网部署必读）
- **生产实测报告**：[PROD_VALIDATION.md](./PROD_VALIDATION.md)

---

## 📚 概念 (Concepts)

给好奇者 / 新贡献者：RTVoice 怎么工作。

- **完整架构**：[ARCHITECTURE.md](./ARCHITECTURE.md)
- **引擎选型对比**（为什么用 sherpa / CosyVoice / vLLM）：[ENGINES.md](./ENGINES.md)
- **设计决策与教训**：[OPERATIONS.md §1 容错矩阵](./OPERATIONS.md) + [ARCHITECTURE.md §7 决策日志](./ARCHITECTURE.md)
- **版本史**：[CHANGELOG.md](./CHANGELOG.md)

---

## 🗺 现状 / Roadmap

**已完成**（v0.7）：3 service 单 tenant 可用 + 容错完备 + 双向流式 TTS

**进行中**（platform-first 重构 sub-projects）：

- SP1 ✅ 平台定位 + 文档骨架（你现在看到的就是）
- SP1.5 API 规范 + OpenAPI
- SP2 Multi-tenant Realtime session（动态 session）
- SP3 prompt + memory + 同步 transcript
- SP4 音色克隆 + 语气语调暴露
- SP5 审计 + 对话记录持久化
- SP6 用量追踪 + 限流
- SP7 Management Web UI

详见 [CHANGELOG.md](./CHANGELOG.md) Unreleased 段。

---

## 🏷 环境使用约定

| 环境 | 用途 | 地址 | GPU |
|---|---|---|---|
| **开发机（本机）** | 部署前预检：验证链路、状态机、编排逻辑 | `127.0.0.1` | 无 |
| **测试环境** | 功能测试：验证模型质量、端到端语音效果 | `192.168.66.163` | NVIDIA RTX 3060 12GB |

### 规则

1. **开发机本机仅做"部署到测试环境前的预检"**，不做功能测试。本机无 GPU，STT/TTS/LLM 使用 mock 或 CPU 小模型，测试结果不代表真实效果。
2. **功能测试统一在 `192.168.66.163`（测试环境）进行。** 该环境有 GPU，运行 prod profile，使用真实引擎（sherpa-onnx GPU、Fun-CosyVoice 3、Ollama/vLLM）。
3. **本机不应长期运行 `docker-compose`。** 预检完成后请执行 `docker compose down` 释放资源，避免"本地能跑就以为没问题"的误判。
4. **所有功能验收以测试环境结果为准，本机运行结果不作为通过依据。**

### 测试环境访问信息

> 前提：需要处于内网环境（或通过 VPN 连入 `192.168.66.x` 网段）。

测试环境使用 Caddy 反向代理 + 自签 TLS 证书，**唯一对外入口为 HTTPS 443 端口**（LiveKit 除外）。各服务内部端口不对外暴露，请使用下表中的代理地址访问：

| 服务 | 地址 | 说明 |
|---|---|---|
| **前端测试页** | `https://192.168.66.163/` | Web Demo 语音对话页面 |
| **Admin 管理页** | `https://192.168.66.163/admin/` | 8-tab 管理 UI（会话、配置、监控等） |
| **Realtime Voice API** | `https://192.168.66.163/v1/sessions`（HTTP）<br>`wss://192.168.66.163/v1/realtime/{session_id}`（WebSocket） | 创建会话 / 实时语音通道 |
| **STT API** | `wss://192.168.66.163/v1/asr` | 流式语音识别 WebSocket |
| **TTS API** | `https://192.168.66.163/v1/tts/stream` | 流式语音合成 HTTP |
| **LiveKit SFU** | `ws://192.168.66.163:7880` | WebRTC 信令（此端口独立暴露，不经过 Caddy） |

#### 自签 TLS 证书说明

测试环境的 Caddy 使用 `tls internal` 生成自签 CA 证书（IP 地址无法申请公开可信证书）。首次通过浏览器访问 `https://192.168.66.163/` 时会弹出"连接不安全"警告：

- **快速方式**：点击「高级」→「继续访问（不安全）」即可进入页面。
- **免警告方式**：从测试服务器导出 Caddy root CA 证书（`/data/caddy/pki/authorities/local/root.crt`），导入到本机的系统/浏览器信任证书库。

使用 API 客户端（curl、Postman 等）时，需加 `-k`（curl）或关闭 SSL 校验，否则请求会因证书不受信任而失败。

**鉴权**：如测试环境已配置 `RTVOICE_API_KEY`，访问 STT/TTS/Realtime API 需在请求头带 `Authorization: Bearer <key>`。测试页（Token Server）若 `DEV_AUTO_INJECT_KEY=true` 则自动注入，否则需手动输入。具体 key 值请向运维确认。

---

## License & 贡献

[LICENSE](./LICENSE) · [CONTRIBUTING.md](./CONTRIBUTING.md)
