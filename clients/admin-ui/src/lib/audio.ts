/**
 * 浏览器音频采集/解码工具：统一产出 STT 需要的 PCM int16 LE 16kHz mono。
 *
 * - 麦克风：getUserMedia → AudioContext → ScriptProcessor → 降采样到 16k → Int16 帧
 * - 文件：decodeAudioData → 取首声道 → 降采样到 16k → 单个 Int16 缓冲
 *
 * ScriptProcessorNode 虽已废弃，但无需额外 worklet 文件、在子路径部署下零配置，
 * 对测试控制台足够可靠。
 */

const TARGET_RATE = 16000;

/** Float32 [-1,1] → 降采样到 16k → Int16 LE bytes。 */
function floatTo16kPCM(input: Float32Array, inputRate: number): ArrayBuffer {
  let samples: Float32Array;
  if (inputRate === TARGET_RATE) {
    samples = input;
  } else {
    const ratio = inputRate / TARGET_RATE;
    const outLen = Math.floor(input.length / ratio);
    samples = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      // 区间平均，抗混叠
      const start = Math.floor(i * ratio);
      const end = Math.min(Math.floor((i + 1) * ratio), input.length);
      let sum = 0;
      let n = 0;
      for (let j = start; j < end; j++) {
        sum += input[j];
        n++;
      }
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

export interface MicRecorder {
  stop: () => void;
}

/** 开始麦克风采集，每帧回调 16k Int16 PCM bytes。返回 stop 句柄。 */
export async function startMicCapture(
  onFrame: (pcm: ArrayBuffer) => void,
): Promise<MicRecorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  source.connect(processor);
  processor.connect(ctx.destination);
  processor.onaudioprocess = (e) => {
    const input = e.inputBuffer.getChannelData(0);
    onFrame(floatTo16kPCM(new Float32Array(input), ctx.sampleRate));
  };
  return {
    stop: () => {
      try {
        processor.disconnect();
        source.disconnect();
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close();
      } catch {
        /* noop */
      }
    },
  };
}

/** 把音频文件解码为单段 16k Int16 PCM bytes。 */
export async function decodeFileTo16kPCM(file: File): Promise<ArrayBuffer> {
  const buf = await file.arrayBuffer();
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AudioCtx();
  try {
    const decoded = await ctx.decodeAudioData(buf.slice(0));
    const ch0 = decoded.getChannelData(0);
    return floatTo16kPCM(new Float32Array(ch0), decoded.sampleRate);
  } finally {
    void ctx.close();
  }
}
