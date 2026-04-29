# tts-server

**职责**：流式 TTS 服务。HTTP chunked，输入文本，输出 PCM 24kHz mono int16 流。

**技术栈**：
- 开发：mock（吐 sine wave 或预录音频，验证管道）
- 生产：CosyVoice 2-0.5B 流式 fp16

**端口**：`${TTS_SERVER_PORT}`（默认 9880，仅 docker network）

**GPU**：dev 否 / prod 是

**HTTP 协议（草案）**：
```
POST /tts/stream
Content-Type: application/json
Body: {"text": "你好", "voice_id": "default"}

Response: 200 OK
Content-Type: application/octet-stream
Transfer-Encoding: chunked
Body: 持续吐 PCM int16 LE 24kHz mono 字节流
```

**性能目标**：首包 ≤ 250ms（CosyVoice 2 实测 150ms）

**待实现**：v0.2 (mock), v0.5 (CosyVoice)
