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
cd RTVoice/deployment
cp .env.example .env       # 填写 SERVER_IP、API key 等
# 编辑 .env，至少设置：SERVER_IP=<你的服务器IP>
docker compose -f docker-compose.yml --env-file .env up -d
```

服务起来后（`SERVER_IP=192.168.66.163` 为例），所有 API 通过 Caddy 反代 HTTPS 443 端口访问：

| 想试什么 | 怎么试 |
|---|---|
| **Admin 管理后台** | 浏览器 `https://192.168.66.163/admin/` |
| **STT**（语音转文字）| `wss://192.168.66.163/v1/asr`；完整示例见 [集成指南](./COZYVOICE_INTEGRATION.md) |
| **TTS**（文字转语音）| `curl --cacert rtvoice-ca.crt -X POST https://192.168.66.163/v1/tts/stream -H "Authorization: Bearer $API_KEY" -d '{"text":"你好"}' \| ffplay -f s16le -ar 24000 -` |
| **Realtime 对话**（API 方式）| `curl --cacert rtvoice-ca.crt -X POST https://192.168.66.163/v1/sessions -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{}'` 拿 ws_url，然后 websocat 连 |
| **Realtime 对话**（浏览器）| `https://192.168.66.163/admin/` → 测试标签页 → 开启语音 → 说话 |

> **TLS 说明**：Caddy 使用 `tls internal` 自签 CA 证书（IP 地址无法申请公开可信证书）。**推荐**：先执行 `./scripts/get-rtvoice-ca.sh` 导出 CA 并安装到系统信任库（一次性操作），之后客户端无需额外配置。`rtvoice-ca.crt` 是导出的 CA 文件，`curl --cacert` 用于指定信任的 CA。详见 [QUICKSTART.md §1](./QUICKSTART.md)。

**Realtime Voice 完整能力（v0.10+）**: 多轮记忆 / 流式 transcript+text / 中途换 prompt / barge-in 打断 / 异步 audit JSONL。详见 [SP3 spec](./docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md)。

**首次启动注意**：LLM (Ollama) 需要 `ollama pull qwen2.5:1.5b`（约 1GB）。完整下好后约 3-5 分钟可对话。prod GPU 部署见 [DEPLOY.md](./DEPLOY.md)。

---

## Python SDK

SDK 源码在 `clients/python/`，尚未发布至 PyPI，使用 git URL 安装：

```bash
pip install "git+https://github.com/zinohome/RTVoice.git#subdirectory=clients/python"
```

```python
from rtvoice_client import Client
import ssl, certifi

# 使用系统信任库；若已安装 RTVoice CA 则无需额外配置
c = Client(api_key="...", base_url="https://192.168.66.163",
           verify="path/to/rtvoice-ca.crt")  # 指向导出的 CA 证书
text = c.stt.transcribe(pcm)
pcm = c.tts.synthesize("你好")
```

详见 [clients/python/README.md](./clients/python/README.md) 和 [COZYVOICE_INTEGRATION.md](./COZYVOICE_INTEGRATION.md)。

## Monitoring

```bash
# 监控组件已内置于 docker-compose.yml（monitoring profile）
cd deployment
docker compose -f docker-compose.yml --env-file .env \
  --profile monitoring up -d
# Grafana: https://192.168.66.163:3000  (或通过 Caddy 反代)
```

详见 [OPERATIONS.md §5](./OPERATIONS.md)。

---

## What's in the box

### 🎤 STT — 流式语音识别

- **接口**：`wss://<host>/v1/asr`（经 Caddy 反代）
- **引擎**：sherpa-onnx Streaming Zipformer 中英文
- **协议**：PCM int16 LE 16kHz mono in → JSON `{partial,final,error}` events out
- **配置**：`STT_PROVIDER=cpu|cuda`（设备）、`STT_QUANTIZED=true|false`（量化）
- **场景**：实时转写、麦克风听写、对话录音
- **鉴权**：`Authorization: Bearer <API_KEY>` header 或 WS subprotocol
- → [集成示例](./COZYVOICE_INTEGRATION.md) · [API spec](./docs/api/stt.md)

### 🔊 TTS — 流式语音合成 + 音色克隆

- **接口**：`POST https://<host>/v1/tts/stream`（单次）+ `wss://<host>/v1/tts/stream_ws`（双向流式）
- **引擎**：Fun-CosyVoice 3 (0.5B GPU)
- **协议**：text in（HTTP body 或 WS 流）→ chunked PCM int16 LE 24kHz mono out
- **特性**：音色克隆（POST /v1/voices）、speed 0.5-2.0
- → [集成示例](./COZYVOICE_INTEGRATION.md) · [API spec](./docs/api/tts.md)

### 💬 Realtime Voice — 实时语音对话

- **接口**：`POST https://<host>/v1/sessions` 创建 + `wss://<host>/v1/realtime/{session_id}` 连接
- **协议**：客户端发 PCM in / 收 PCM + transcript events out（OpenAI Realtime 风格）
- **引擎**：内部 STT (sherpa) + LLM (Ollama / vLLM) + TTS (Fun-CosyVoice 3)
- **特性**：双向流式、多轮记忆、同步 transcript、换音色、**barge-in 打断**（发送 `{"type":"interrupt"}` 即时终止当前回复）
- **超时配置**：`SESSION_IDLE_TIMEOUT_S`（默认 120s）、`SESSION_MAX_LIFETIME_S`（默认 3600s）
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
| **Admin 管理后台** | `https://192.168.66.163/admin/` | 管理 UI（会话、配置、监控等）；根路径自动跳转此处 |
| **Realtime Voice API** | `https://192.168.66.163/v1/sessions`（HTTP）<br>`wss://192.168.66.163/v1/realtime/{session_id}`（WebSocket） | 创建会话 / 实时语音通道 |
| **STT API** | `wss://192.168.66.163/v1/asr` | 流式语音识别 WebSocket |
| **TTS API（单次）** | `https://192.168.66.163/v1/tts/stream` | 流式语音合成 HTTP POST |
| **TTS API（流式）** | `wss://192.168.66.163/v1/tts/stream_ws` | 双向流式 TTS WebSocket |
| **音色管理** | `https://192.168.66.163/v1/voices` | GET 列表 / POST 注册音色 |
| **Token Server** | `https://192.168.66.163/v1/tokens` | LiveKit JWT 签发 |
| **LiveKit SFU** | `ws://192.168.66.163:7880` | WebRTC 信令（此端口独立暴露，不经过 Caddy） |

#### 自签 TLS 证书说明

测试环境的 Caddy 使用 `tls internal` 生成自签 CA 证书（IP 地址无法申请公开可信证书）。

**推荐做法（一次性配置）**：

```bash
# 从服务器导出 CA 证书
ssh root@192.168.66.163 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > rtvoice-ca.crt

# macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain rtvoice-ca.crt
# Linux
sudo cp rtvoice-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
# Windows：双击 rtvoice-ca.crt → 安装到"受信任的根证书颁发机构"
```

完成后浏览器和 curl 均无需额外配置即可正常访问。API 客户端中指定 CA 文件：`curl --cacert rtvoice-ca.crt ...` / Python `requests.get(url, verify="rtvoice-ca.crt")`。

**鉴权**：所有 STT/TTS/Realtime API 调用需在请求头带 `Authorization: Bearer <RTVOICE_API_KEY>`。测试页若 `DEV_AUTO_INJECT_KEY=true` 则自动注入，否则需手动输入。具体 key 值请向运维确认或参考 [QUICKSTART.md §2](./QUICKSTART.md)。

---

## License & 贡献

[LICENSE](./LICENSE) · [CONTRIBUTING.md](./CONTRIBUTING.md)
