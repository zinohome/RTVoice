# stt-server

**职责**：流式 ASR 服务。WebSocket 协议，输入 PCM 16kHz mono int16 流，输出 partial+final 文本事件。

**技术栈**：
- 开发：sherpa-onnx CPU + Paraformer-tiny 中文流式
- 生产：sherpa-onnx GPU + Paraformer-large 中文流式

**端口**：`${STT_SERVER_PORT}`（默认 9090，仅 docker network）

**GPU**：dev 否 / prod 是

**WS 协议（草案）**：
```
client → server: binary frames (PCM int16 LE, 16kHz mono, 任意长度)
client → server: text "EOS" 表示结束本轮
server → client: {"type": "partial", "text": "..."}
server → client: {"type": "final", "text": "..."}
```

**待实现**：v0.3
