// Web Audio helpers: PCM playback + mic recording

export class PCMPlayer {
  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
    this.ctx = null;
    this.queue = [];
    this.playing = false;
  }

  ensureCtx() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: this.sampleRate,
      });
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  }

  enqueue(int16ArrayBuffer) {
    this.ensureCtx();
    const i16 = new Int16Array(int16ArrayBuffer);
    if (i16.length === 0) return;
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
    const buf = this.ctx.createBuffer(1, f32.length, this.sampleRate);
    buf.copyToChannel(f32, 0);
    this.queue.push(buf);
    if (!this.playing) this._drain();
  }

  _drain() {
    if (!this.queue.length) {
      this.playing = false;
      return;
    }
    this.playing = true;
    const buf = this.queue.shift();
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    src.onended = () => this._drain();
    src.start();
  }

  reset() {
    this.queue = [];
    this.playing = false;
  }

  async close() {
    this.reset();
    if (this.ctx) {
      try { await this.ctx.close(); } catch {}
      this.ctx = null;
    }
  }
}


// Mic 录音 → 16k mono int16 PCM chunks（流式回调）
// 返 stop 函数。
export async function recordMic16kPCM(onChunk) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  proc.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = v * 0x7FFF;
    }
    onChunk(i16.buffer);
  };
  src.connect(proc);
  proc.connect(ctx.destination);

  return async () => {
    try { proc.disconnect(); } catch {}
    try { src.disconnect(); } catch {}
    stream.getTracks().forEach((t) => t.stop());
    try { await ctx.close(); } catch {}
  };
}
