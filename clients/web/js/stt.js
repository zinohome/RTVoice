import cfg from "./config.js";
import { log } from "./app.js";
import { recordMic16kPCM } from "./audio.js";

export function setupSTT() {
  const recBtn = document.getElementById("stt-record");
  const stopBtn = document.getElementById("stt-stop");
  const status = document.getElementById("stt-status");
  const resultIn = document.getElementById("stt-result");

  let stopRec = null;
  let chunks = [];

  recBtn.addEventListener("click", async () => {
    chunks = [];
    try {
      stopRec = await recordMic16kPCM((buf) => {
        chunks.push(new Int16Array(buf));
      });
      recBtn.disabled = true;
      stopBtn.disabled = false;
      status.textContent = "录音中…";
      log("evt", "STT: recording started");
    } catch (e) {
      log("err", `STT: mic 失败: ${e.message}`);
    }
  });

  stopBtn.addEventListener("click", async () => {
    if (stopRec) await stopRec();
    stopRec = null;
    recBtn.disabled = false;
    stopBtn.disabled = true;
    status.textContent = "识别中…";

    const total = chunks.reduce((s, c) => s + c.length, 0);
    const merged = new Int16Array(total);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }
    log("evt", `STT: captured ${merged.byteLength} bytes`);

    try {
      const r = await fetch(`${cfg.base}/v1/asr?sample_rate=16000`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          ...cfg.authHeaders(),
        },
        body: merged.buffer,
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `STT: HTTP ${r.status} ${body.slice(0, 100)}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      const j = await r.json();
      resultIn.value = j.text || "";
      log("user", `STT: ${j.text}`);
      status.textContent = "完成";
    } catch (e) {
      log("err", `STT: ${e.message}`);
      status.textContent = "失败";
    }
  });
}
