# benchmark template

每次实测保存为 `<version>-<hardware>.md`，例如：
- `v0.5.1-rtx3060.md`
- `v0.5.1-i5-12400.md`

## 测试环境

- **硬件**：CPU 型号 / RAM / GPU / 操作系统
- **版本**：commit hash
- **测试日期**：YYYY-MM-DD
- **测试数据**：自录 N 条中文 / 英文 / 中英混合

## 端到端延迟

|  | p50 | p95 | p99 |
|---|---|---|---|
| user 说完 → STT final | | | |
| STT final → LLM 首 token | | | |
| LLM 首 token → TTS 首包 | | | |
| TTS 首包 → 浏览器播放 | | | |
| **end-to-end** | | | |

## 各引擎单独 benchmark

### STT (sherpa-onnx)
- RTF（real-time factor）：
- CER（字错误率）@ 50 句中文：

### LLM (qwen2.5:1.5b / 3b)
- 首 token：ms
- 输出速度：tok/s
- 中文 MMLU 子样本得分：

### TTS (Kokoro / CosyVoice 2)
- TTFB：ms
- RTF：
- MOS（5 人盲听 1-5）：

## 资源占用

```
nvidia-smi  → VRAM 使用
docker stats → CPU/RAM
```

## 已知问题与权衡

- ...

## 改进建议

- ...
