# RTVoice 使用说明

> **当前部署状态**：RTVoice v0.20.1，部署在 `192.168.66.163`，所有服务 healthy。

## 快速导航

| 我想做什么 | 跳转 |
|-----------|------|
| 打开管理界面管理 Key 和音色 | [Admin Console](#admin-console) |
| 通过 API 进行实时语音对话 | [Realtime API](#realtime-api-实时语音对话) |
| 通过 API 进行语音合成（TTS）| [TTS API](#tts-api-语音合成) |
| 通过 API 进行语音识别（STT）| [STT API](#stt-api-语音识别) |
| 注册自定义声音 | [音色管理](#音色管理) |
| 查看系统监控 | [监控](#监控) |

---

## 访问地址

| 服务 | 地址 |
|------|------|
| Admin Console | `https://192.168.66.163/admin-v2/` |
| Grafana 监控 | `http://192.168.66.163:13000`（admin/admin） |
| LiveKit WebSocket | `ws://192.168.66.163:7880` |

---

## Admin Console

### 登录

浏览器打开 `https://192.168.66.163/admin-v2/`，输入：

- **用户名**：`admin`
- **密码**：`RTVoice@2026`

> **自签证书提示**：首次访问浏览器会提示证书不受信任。内网使用可直接点"继续访问"，或按 [deployment/README.md](../deployment/README.md#第-5-步信任-ca-证书客户端) 信任 CA 证书。

### 主要功能

| 模块 | 功能 |
|------|------|
| **Dashboard（首页）** | 系统状态总览：服务健康、GPU 显存、实时 session 数 |
| **API Keys** | 创建、查看、吊销、轮转 API Key；管理各 key 的权限 scope 和频率限制 |
| **Voice Keys** | 注册、预览、删除自定义音色；查看注册后的规范化参数 |
| **Realtime 测试** | 浏览器内直接测试实时语音对话 |
| **TTS 测试** | 测试合成效果，选择音色和语速 |
| **STT 测试** | 测试语音识别 |
| **Monitor** | 查看各服务实时状态 |

---

## API 鉴权

所有 API 调用需要在请求头携带 Bearer Token：

```
Authorization: Bearer <YOUR_API_KEY>
```

API Key 在 Admin Console → **API Keys** 创建。根据用途选择合适的 scope：

| Scope | 用途 |
|-------|------|
| `realtime` | 实时语音对话 |
| `tts` | 语音合成 |
| `stt` | 语音识别 |
| `tokens` | LiveKit 房间 token |
| `admin` | 管理 API Key 和音色（高权限） |

WebSocket 端点支持三种鉴权方式（任选其一）：

```
# 方式 1：Subprotocol（浏览器 WebSocket 推荐）
Sec-WebSocket-Protocol: bearer.<YOUR_API_KEY>

# 方式 2：Header（服务端调用推荐）
Authorization: Bearer <YOUR_API_KEY>

# 方式 3：Query 参数（调试用，有日志安全风险）
?token=<YOUR_API_KEY>
```

---

## Realtime API（实时语音对话）

基于 WebSocket 的实时语音对话，支持 STT → LLM → TTS 全链路流式处理。

### 步骤 1：创建 Session

```bash
curl -X POST https://192.168.66.163/v1/sessions \
  -H "Authorization: Bearer <YOUR_REALTIME_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "voice": "default_zh_female",
    "speed": 1.0,
    "prompt": "你是一个语音助手，请用简短的中文回答。",
    "audit_persist": false
  }'
```

响应：

```json
{
  "session_id": "sess_abc123",
  "ws_url": "wss://192.168.66.163/v1/realtime/sess_abc123",
  "expires_at": "2026-05-29T11:00:00Z",
  "voice": "default_zh_female",
  "speed": 1.0,
  "prompt": "你是一个语音助手，请用简短的中文回答。",
  "audit_persist": false
}
```

### 步骤 2：连接 WebSocket

```python
import asyncio, json, websockets

async def realtime_demo(session_id: str, ws_url: str, api_key: str):
    async with websockets.connect(
        ws_url,
        additional_headers={"Authorization": f"Bearer {api_key}"},
    ) as ws:
        # 发送 PCM 音频（int16 LE 16kHz mono）
        with open("user_voice.pcm", "rb") as f:
            pcm_data = f.read()
        
        # 每 100ms 一帧（16kHz × 0.1s × 2 字节 = 3200 bytes）
        for i in range(0, len(pcm_data), 3200):
            await ws.send(pcm_data[i:i+3200])
        
        # 发送用户发言结束信号
        await ws.send("audio.eos")
        
        # 接收响应
        async for msg in ws:
            if isinstance(msg, bytes):
                # 助手语音回复（PCM int16 LE 24kHz mono）
                with open("assistant_voice.pcm", "ab") as f:
                    f.write(msg)
            else:
                ev = json.loads(msg)
                if ev["type"] == "transcript.partial":
                    print(f"[识别中] {ev['text']}")
                elif ev["type"] == "transcript.final":
                    print(f"[识别完成] {ev['text']}")
                elif ev["type"] == "response.text":
                    print(f"[AI回复] {ev['text']}")
                elif ev["type"] == "response.done":
                    print("[本轮结束]")
                    break
```

### Session 控制事件

在 WebSocket 连接中发送 JSON 可实时调整对话参数：

```json
// 更新系统 Prompt
{"type": "session.update", "prompt": "你是客服助手，用正式语气回答"}

// 更换音色
{"type": "session.update", "voice": "alice"}

// 调整语速（0.5-2.0）
{"type": "session.update", "speed": 1.2}

// 清除对话历史（memory.clear，不影响 prompt）
{"type": "memory.clear"}
```

### Session 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `voice` | string | `default_zh_female` | TTS 音色 ID |
| `speed` | float | `1.0` | 语速，0.5-2.0 |
| `prompt` | string | 系统默认 | System Prompt，最多 2000 字符 |
| `audit_persist` | bool | `false` | 是否持久化对话记录到 JSONL |

### WebSocket 事件完整列表

**客户端 → 服务端**

| 类型 | 内容 | 说明 |
|------|------|------|
| binary | PCM int16 LE 16kHz mono | 用户麦克风音频 |
| text `"audio.eos"` | — | 用户发言结束，触发 AI 处理 |
| text JSON | `{"type":"session.update",...}` | 更新会话参数 |
| text JSON | `{"type":"memory.clear"}` | 清除对话历史 |

**服务端 → 客户端**

| 类型 | 内容 | 时机 |
|------|------|------|
| text | `{"type":"transcript.partial","text":"..."}` | STT 实时识别 |
| text | `{"type":"transcript.final","text":"..."}` | STT 最终识别结果 |
| text | `{"type":"response.text","text":"..."}` | AI 回复文本（流式） |
| binary | PCM int16 LE 24kHz mono | AI 语音回复 |
| text | `{"type":"response.done"}` | 本轮回复结束 |
| text | `{"type":"error","code":"...","message":"..."}` | 错误 |

---

## TTS API（语音合成）

将文本转换为音频。

### HTTP 单次合成

```bash
curl -X POST https://192.168.66.163/v1/tts/stream \
  -H "Authorization: Bearer <YOUR_TTS_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，欢迎使用语音合成服务。","voice":"default_zh_female","speed":1.0}' \
  --output output.pcm

# 用 ffmpeg 转换为 WAV
ffmpeg -f s16le -ar 24000 -ac 1 -i output.pcm output.wav
```

### 响应格式

音频以 chunked PCM 流返回：

| 响应头 | 值 | 说明 |
|--------|-----|------|
| `Content-Type` | `application/octet-stream` | 原始字节流 |
| `X-Sample-Rate` | `24000` | 采样率 Hz |
| `X-Channels` | `1` | 单声道 |
| `X-Format` | `pcm-int16-le` | PCM int16 小端序 |

### 请求参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | string | — | 必填，1-2000 字符 |
| `voice` | string | `default_zh_female` | 音色 ID |
| `speed` | float | `1.0` | 语速，0.5-2.0 |

### WebSocket 流式合成（低延迟，~150ms 首字节）

适合 LLM streaming 输出场景，边生成文本边合成音频：

```python
import asyncio, json, websockets

async def stream_tts(text_chunks, api_key):
    async with websockets.connect(
        "wss://192.168.66.163/v1/tts/stream_ws",
        additional_headers={"Authorization": f"Bearer {api_key}"},
    ) as ws:
        # 首帧：发送 metadata
        await ws.send(json.dumps({"voice": "default_zh_female", "speed": 1.0}))
        
        # 逐块发送文本
        for chunk in text_chunks:
            await ws.send(chunk)
        
        # 发送结束标志
        await ws.send("EOS")
        
        # 接收 PCM 音频
        pcm = bytearray()
        async for msg in ws:
            if isinstance(msg, bytes):
                pcm.extend(msg)
            else:
                ev = json.loads(msg)
                if ev["type"] == "done":
                    break
        return bytes(pcm)
```

---

## STT API（语音识别）

实时流式语音转文字（中英双语）。

```python
import asyncio, json, websockets

async def transcribe(pcm_bytes: bytes, api_key: str) -> str:
    """将 PCM 音频转写为文字。
    
    音频格式要求：PCM int16 LE，16kHz，单声道（mono）
    """
    async with websockets.connect(
        "wss://192.168.66.163/v1/asr",
        additional_headers={"Authorization": f"Bearer {api_key}"},
    ) as ws:
        # 每 100ms 一帧（16kHz × 0.1s × 2 字节 = 3200 bytes）
        chunk_size = 3200
        for i in range(0, len(pcm_bytes), chunk_size):
            await ws.send(pcm_bytes[i:i+chunk_size])
        
        # 发送结束信号
        await ws.send("EOS")
        
        # 等待 final 结果
        async for msg in ws:
            ev = json.loads(msg)
            if ev["type"] == "final":
                return ev["text"]
            elif ev["type"] == "partial":
                print(f"[识别中] {ev['text']}")
        return ""

# 使用示例
text = asyncio.run(transcribe(open("speech.pcm","rb").read(), "your-stt-key"))
print(text)
```

### 音频格式要求

| 参数 | 要求 |
|------|------|
| 编码 | PCM int16 小端序（signed 16-bit little-endian） |
| 采样率 | 16000 Hz |
| 声道 | 1（mono） |
| 帧大小 | 建议 20-100ms（320-1600 bytes） |

---

## 音色管理

### 内置音色

| 音色 ID | 说明 |
|---------|------|
| `default_zh_female` | 默认中文女声（内置，不可删除） |

### 注册自定义音色

通过 Admin Console 或 API 注册（需要 Admin 权限）：

**通过 API：**

```bash
curl -X POST https://192.168.66.163/v1/voices \
  -H "Authorization: Bearer <TTS_ADMIN_API_KEY>" \
  -F "spk_id=alice" \
  -F "prompt_text=这是参考音频的完整文字内容" \
  -F "file=@reference.wav"
```

**v0.20.1 自动规范化**：上传任何格式的音频（WAV/MP3/FLAC 等），系统自动：
- 转换为 16kHz 单声道
- 去除前导静音
- 截断到 8 秒
- 按比例截断对应文本

**响应包含规范化信息：**

```json
{
  "spk_id": "alice",
  "voice_count": 2,
  "original_duration": 30.0,
  "effective_duration": 8.0,
  "effective_text": "这是参考音频的完整"
}
```

### 删除音色

```bash
curl -X DELETE https://192.168.66.163/v1/voices/alice \
  -H "Authorization: Bearer <TTS_ADMIN_API_KEY>"
```

> 注意：`default_zh_female` 为保护音色，不可删除。

---

## 监控

### Grafana 看板

访问 `http://192.168.66.163:13000`（初始账号 admin/admin）。

主要指标：

- GPU 显存使用（目标 < 80%，约 9.8GB/12GB）
- TTS 合成延迟
- Realtime Session 数
- API 请求量和错误率

### 服务健康检查

```bash
# 检查各服务状态
for service in realtime stt tts; do
  echo -n "$service: "
  curl -s https://192.168.66.163/v1/console/services 2>/dev/null | \
    python3 -c "import sys,json; s=json.load(sys.stdin); print(next((x['status'] for x in s if '$service' in x['name']), 'N/A'))"
done
```

### Docker 日志查看

```bash
# 在 192.168.66.163 上执行
sudo ssh 192.168.66.163

# 查看各服务状态
cd /data/RTVoice/deployment
docker compose ps

# 查看 TTS 日志（常见排查对象）
docker logs -f rtvoice-tts

# 查看 GPU 显存
nvidia-smi
```

---

## 常见问题

### TTS 合成无声音

1. 检查 GPU 显存：`nvidia-smi`，确认显存未满（< 10GB）
2. 查看 TTS 日志：`docker logs rtvoice-tts`
3. 确认 API Key 有 `tts` scope
4. 重启 TTS 服务：`docker compose restart tts-server`

### Realtime 无法连接

1. 确认 Session 未过期（默认 30 分钟）
2. 确认 WebSocket URL 中的 IP 可达
3. 确认使用 session owner 的 API Key 连接 WebSocket

### 自定义音色注册后效果差

- 推荐上传 3-30 秒的清晰人声，无背景噪音
- 确认文本与音频内容完全对应
- 如已有长音频，系统自动截取前 8 秒有效语音段，无需预处理

### Admin Console 登录失败

确认密码为 `RTVoice@2026`（已于 2026-05-29 更新）。
