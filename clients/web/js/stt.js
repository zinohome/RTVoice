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
      const cfgU = new URL(cfg.base);
      const wsProto = cfgU.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${wsProto}//${cfgU.host}/v1/asr`;
      const protocols = cfg.bearer ? [`bearer.${cfg.bearer}`] : [];
      const ws = new WebSocket(wsUrl, protocols);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        ws.send(merged.buffer);
        ws.send("EOS");
      };

      ws.onmessage = (e) => {
        if (typeof e.data !== "string") return;
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        if (ev.type === "partial") {
          status.textContent = `[partial] ${ev.text}`;
        } else if (ev.type === "final") {
          resultIn.value = ev.text || "";
          log("user", `STT: ${ev.text}`);
          status.textContent = "完成";
          ws.close();
        } else if (ev.type === "error") {
          log("err", `STT: ${ev.message}`);
          status.textContent = "失败";
          ws.close();
        }
      };

      ws.onerror = () => {
        log("err", "STT: WebSocket 连接失败");
        status.textContent = "失败";
      };

      ws.onclose = (e) => {
        if (e.code !== 1000 && status.textContent === "识别中…") {
          log("err", `STT: ws closed ${e.code}`);
          status.textContent = "失败";
        }
      };
    } catch (e) {
      log("err", `STT: ${e.message}`);
      status.textContent = "失败";
    }
  });
}
