// PCM 采集 AudioWorklet：在专用音频线程上采集麦克风，降采样到 16k Int16，
// postMessage 回主线程。取代已废弃的 ScriptProcessorNode——后者跑在主线程，
// 长录音 + 频繁 React 重渲染时会丢音频帧，导致转写丢字。Worklet 在音频线程
// 采集，主线程繁忙时消息排队而非丢帧。
const TARGET_RATE = 16000;

// Float32 [-1,1] @inputRate → 16k Int16 LE。与 lib/audio.ts 的 floatTo16kPCM 同款
// 区间平均抗混叠算法（worklet 无法 import 应用模块，故此处独立实现）。
function downTo16kInt16(input, inputRate) {
  let samples;
  if (inputRate === TARGET_RATE) {
    samples = input;
  } else {
    const ratio = inputRate / TARGET_RATE;
    const outLen = Math.floor(input.length / ratio);
    samples = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(Math.floor((i + 1) * ratio), input.length);
      let sum = 0, n = 0;
      for (let j = start; j < end; j++) { sum += input[j]; n++; }
      samples[i] = n > 0 ? sum / n : 0;
    }
  }
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._acc = [];
    this._accLen = 0;
    this._emitEvery = 2048; // 累积 ~43ms@48k 再 post，降低消息频率
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      // process() 复用同一缓冲，必须 copy
      this._acc.push(new Float32Array(input[0]));
      this._accLen += input[0].length;
      if (this._accLen >= this._emitEvery) {
        const merged = new Float32Array(this._accLen);
        let off = 0;
        for (const c of this._acc) { merged.set(c, off); off += c.length; }
        this._acc = [];
        this._accLen = 0;
        const pcm = downTo16kInt16(merged, sampleRate);
        this.port.postMessage(pcm, [pcm]); // transfer，零拷贝
      }
    }
    return true; // 保持处理器存活
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
