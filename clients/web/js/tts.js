import cfg from "./config.js";
import { log } from "./app.js";
import { PCMPlayer } from "./audio.js";

export function setupTTS() {
  const goBtn = document.getElementById("tts-go");
  const stopBtn = document.getElementById("tts-stop-btn");
  const status = document.getElementById("tts-status");
  const meta = document.getElementById("tts-meta");

  let player = null;
  let abortCtl = null;

  goBtn.addEventListener("click", async () => {
    const text = document.getElementById("tts-text").value;
    const voice = document.getElementById("tts-voice").value;
    const speed = parseFloat(document.getElementById("tts-speed").value);
    const lang = document.getElementById("tts-lang").value;
    if (!text.trim()) {
      log("err", "TTS: empty text");
      return;
    }
    goBtn.disabled = true;
    stopBtn.disabled = false;
    status.textContent = "请求中…";
    meta.textContent = "";
    if (player) await player.close();
    player = new PCMPlayer(24000);
    abortCtl = new AbortController();

    let totalBytes = 0;
    const startedAt = Date.now();
    try {
      const r = await fetch(`${cfg.base}/v1/tts/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...cfg.authHeaders(),
        },
        body: JSON.stringify({ text, voice, speed, lang }),
        signal: abortCtl.signal,
      });
      if (!r.ok) {
        log("err", `TTS: HTTP ${r.status}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      log("evt", "TTS: stream open");
      const reader = r.body.getReader();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value && value.byteLength) {
          player.enqueue(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
          totalBytes += value.byteLength;
          status.textContent = `播放中… (${totalBytes} bytes)`;
        }
      }
      const elapsed = ((Date.now() - startedAt) / 1000).toFixed(2);
      meta.textContent = `共 ${totalBytes} bytes，stream 用时 ${elapsed}s`;
      log("agent", `TTS done: ${totalBytes} bytes`);
      status.textContent = "完成（播放中）";
    } catch (e) {
      if (e.name === "AbortError") {
        log("evt", "TTS: aborted by user");
        status.textContent = "已停止";
      } else {
        log("err", `TTS: ${e.message}`);
        status.textContent = "失败";
      }
    } finally {
      goBtn.disabled = false;
      stopBtn.disabled = true;
    }
  });

  stopBtn.addEventListener("click", () => {
    if (abortCtl) abortCtl.abort();
    if (player) player.reset();
  });
}
