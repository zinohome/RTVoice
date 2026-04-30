# tts-server

**职责**：流式 TTS 服务。HTTP chunked，输入文本 + 音色，输出 PCM 24kHz mono int16 流。

**状态**：✅ v0.5（Kokoro 82M ONNX CPU）

## 技术栈

| 组件 | 选型 |
|---|---|
| 推理引擎 | `kokoro-onnx==0.5.0`（纯 ONNX，**不拖 torch**） |
| 模型 | `kokoro-v1.0.onnx` (~325MB) + `voices-v1.0.bin` (~28MB) |
| 音素化 | espeakng-loader（包含 libespeak-ng）+ phonemizer-fork |
| 系统依赖 | apt: `espeak-ng-data`（语言数据，含中文 cmn） |
| Web 框架 | FastAPI + uvicorn[standard] |

**镜像大小**：~1.93GB

## 选型理由（ENGINES.md §3）

CosyVoice 2 在 CPU 上 5-10× realtime，破坏 voice agent 流式承诺。
Kokoro 82M 在**现代 CPU（AVX2/AVX-512 支持）** 上 1× realtime+。
v0.5 prod 切 GPU 后用 CosyVoice 2，质量大幅提升（同协议，agent 代码零改动）。

## HTTP 协议

```
POST /tts/stream
Content-Type: application/json

{
  "text": "你好，今天天气真好。",
  "voice": "zf_xiaobei",       // 可选；默认 TTS_DEFAULT_VOICE
  "lang": "cmn",               // 可选；espeak-ng 语言代码
  "speed": 1.0                 // 可选；0.5-1.5
}
```

响应：
```
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Transfer-Encoding: chunked
X-Sample-Rate: 24000
X-Channels: 1
X-Format: pcm-int16-le

[PCM int16 LE bytes 流]
```

辅助端点：`GET /health`、`GET /info`、`GET /voices`

## 流式策略

Kokoro 模型本身**非流式**（一次输入 → 一次输出整段 PCM）。
为了模拟流式，server 按中英文标点切短语，**逐句合成 → 逐句推**。
首包延迟 = 首句合成时间。长文本回复在前几句已开始播放。

## 音色列表

54 个音色，命名约定：`<性别>_<语言>_<人名>`：
- 中文：`zf_xiaobei`, `zf_xiaoni`, `zm_yunjian`, `zm_yunxia`, `zm_yunyang`
- 英文：`af_*`, `am_*`, `bf_*`, `bm_*` 等
- 完整列表：`GET /voices`

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TTS_DEFAULT_VOICE` | `zf_xiaobei` | 中文女声（小贝） |
| `TTS_DEFAULT_LANG` | `cmn` | espeak-ng 语言代码（cmn=Mandarin） |
| `TTS_MODELS_DIR` | `/app/models` | 模型目录 |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |

## ⚠️ 性能：硬件依赖巨大

**dev 沙盒环境（Intel Xeon E5-2697 v2 @ 2.7GHz, 2013 款）实测 RTF ≈ 4-8**
（即合成 1s 音频要 4-8s）：

| 输入 | 实测 TTFB | 实测总耗时 | 音频时长 | RTF |
|---|---|---|---|---|
| 5 字 | 11s | 11s | 1.26s | 0.11× |
| 7 字 | 14s | 14s | 2.03s | 0.14× |
| 24 字 | 23s | 23s | 4.52s | 0.19× |

**这不是 Kokoro 慢，是 dev 沙盒 CPU 太老**（无 AVX2 优化）。

**预期表现**：
| 硬件 | 预期 RTF |
|---|---|
| 现代 CPU（i5-8代+ / Ryzen 3000+，AVX2） | 1.0-2.0× ✅ 可用 |
| 移动笔电 CPU (i7-1185G7 等) | 1.0-1.5× |
| **当前 dev 沙盒（Xeon E5-2697 v2）** | **0.1-0.2×** ❌ |
| 服务器 CPU（Xeon Silver+） | 1.5-3.0× |
| RTX 3060 GPU（v0.5 prod 切 CosyVoice 2） | 5-10× ✅ 流畅 |

**结论**：本环境用于**协议正确性**验证。**用户家的现代 CPU + RTX 3060 是真实运行环境**。

## v0.5 已知限制

- ⚠️ **本沙盒太慢**：见上表；用户 home box 应能跑实时
- ⚠️ **句级流式不是 token 级**：句子越长首包越慢
- ⚠️ **Kokoro 中文质量**：ENGINES.md 评级"过得去"，比 CosyVoice 2 / GPT-SoVITS 差一档
- ⚠️ **无鉴权**：仅 docker network 内可达
- ⚠️ **CPU 限制**：docker-compose.yml 配 4 cpus（旧 CPU 上仍不够；现代 CPU 上 4 核已够）

## 演进路线

| 版本 | 变化 |
|---|---|
| v0.5 | Kokoro 82M ONNX CPU（当前） |
| v0.5 prod | docker-compose.prod.yml 切镜像到 CosyVoice 2-0.5B GPU |
| v0.6 | Kokoro 多音色配置；Tone control |
