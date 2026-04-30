# stt-server

**职责**：流式 ASR 服务。WebSocket 协议，输入 PCM 16kHz mono int16 流，输出 partial+final 文本事件。

**状态**：✅ v0.3（CPU + 中文流式 Zipformer）

## 技术栈

| 组件 | 选型 |
|---|---|
| ASR 引擎 | `sherpa-onnx==1.13.0`（C++ 内核，不拖 torch） |
| 模型 | `sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20` (int8) |
| Web 框架 | FastAPI + uvicorn[standard] |

**镜像大小**：~1.14GB（其中 ~200MB 是 Zipformer 模型 int8 三件套）

## 模型选型

ENGINES.md §2 原计划用 Paraformer streaming，但其 HuggingFace 仓库匿名下载受限（401 鉴权墙）。
**Zipformer streaming bilingual zh-en** 是 ENGINES.md 同档备选（中文 SOTA + CPU 友好），
公开可下载、协议兼容，零额外代码改动。生产 v0.5 阶段会用实测 CER 数据决定是否切回 Paraformer。

| 文件 | 大小 |
|---|---|
| encoder-epoch-99-avg-1.int8.onnx | 181.9MB |
| decoder-epoch-99-avg-1.int8.onnx | 13.1MB |
| joiner-epoch-99-avg-1.int8.onnx | 3.2MB |
| tokens.txt | ~10KB |

## WS 协议

```
ws://stt-server:9090/asr
```

### Client → Server

| 帧类型 | 内容 | 说明 |
|---|---|---|
| binary | PCM int16 LE 16kHz mono | 任意长度，建议 20-100ms 一帧 |
| text   | `EOS` | 声明 utterance 结束，期待 final |
| text   | `RESET` | 丢弃当前 stream 状态（不发 final） |

### Server → Client

```json
{"type": "partial", "text": "..."}     // 流式中间结果
{"type": "final",   "text": "..."}     // EOS 或端点检测后的 final（之后 server 自动 reset）
{"type": "error",   "message": "..."}  // 错误
```

## HTTP 接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` 当 recognizer 就绪 |
| GET | `/info` | 模型名、采样率、端点检测参数 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `STT_MODEL` | zipformer-bilingual | 模型目录名（必须存在于 /app/models/） |
| `STT_NUM_THREADS` | 2 | onnxruntime intra-op 线程数 |
| `STT_RULE1_SILENCE` | 1.2 | 端点检测：有结果后多少秒静音判端点 |
| `STT_RULE2_SILENCE` | 0.8 | 端点检测：无结果时多少秒静音判端点 |
| `STT_RULE3_MIN_UTT` | 20.0 | 端点检测：utterance 最小长度（秒） |
| `LOG_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR |

## 自动验证（v0.3 实测）

模型加载耗时：~5s（CPU，2 threads）

WS 协议测试（用模型自带 test_wavs）：
| 输入 | 输出 final |
|---|---|
| test_wavs/0.wav (10s 中英混合) | `昨天是 MONDAY` |
| test_wavs/1.wav (5s 中英混合) | `这是第一种第二种叫呃与 ALWAYS ALWAYS什么` |

## v0.3 已知限制

- ⚠️ **CPU 推理**：实时倍率 ~0.3-0.5×（每秒音频 300-500ms 解码）。生产 v0.5 切 GPU
- ⚠️ **单 recognizer 多 stream**：sherpa-onnx 文档说 thread-safe，但高并发未验证；v0.5 加压测
- ⚠️ **无鉴权**：仅 docker network 内可达。若未来对外暴露需加 token 验证
- ⚠️ **模型固定**：通过 env 切目录名能换，但要求文件命名规则一致
