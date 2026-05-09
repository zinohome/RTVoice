import cfg from "./config.js";
import { log } from "./app.js";
import { PCMPlayer, recordMic16kPCM } from "./audio.js";

export function setupRealtime() {
  let session = null;
  let ws = null;
  let stopRec = null;
  let player = null;

  const $ = (id) => document.getElementById(id);
  const tx = $("rt-transcript");
  const rx = $("rt-response");

  $("rt-create").addEventListener("click", async () => {
    const promptVal = $("rt-prompt").value.trim() || undefined;
    const voiceVal = $("rt-voice").value.trim() || undefined;
    const speedVal = parseFloat($("rt-speed").value);
    const auditVal = $("rt-audit").checked;
    try {
      const r = await fetch(`${cfg.base}/v1/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...cfg.authHeaders() },
        body: JSON.stringify({ prompt: promptVal, voice: voiceVal, speed: speedVal, audit_persist: auditVal }),
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `create_session: HTTP ${r.status} ${body.slice(0, 200)}`);
        return;
      }
      session = await r.json();
      $("rt-session-info").textContent = `session=${session.session_id} | audit=${session.audit_persist}`;
      log("evt", `session created: ${session.session_id}`);
      $("rt-connect").disabled = false;
    } catch (e) {
      log("err", `create_session: ${e.message}`);
    }
  });

  $("rt-connect").addEventListener("click", () => {
    let wsUrl = session.ws_url;
    try {
      const cfgU = new URL(cfg.base);
      const wsU = new URL(wsUrl);
      wsU.protocol = cfgU.protocol === "https:" ? "wss:" : "ws:";
      wsU.hostname = cfgU.hostname;
      wsU.port = cfgU.port || (cfgU.protocol === "https:" ? "443" : "80");
      wsUrl = wsU.toString();
    } catch (e) {
      log("err", `ws_url 解析失败: ${e.message}`);
      return;
    }
    const protocols = cfg.bearer ? [`bearer.${cfg.bearer}`] : [];
    ws = new WebSocket(wsUrl, protocols);
    ws.binaryType = "arraybuffer";

    if (player) player.close();
    player = new PCMPlayer(24000);

    ws.onopen = () => {
      log("evt", "ws open");
      tx.textContent = ""; rx.textContent = "";
      ["rt-record", "rt-eos", "rt-disconnect", "rt-update-prompt", "rt-update-voice", "rt-clear-memory"].forEach(
        (id) => ($(id).disabled = false)
      );
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        let ev;
        try { ev = JSON.parse(e.data); } catch { log("err", `non-json: ${e.data.slice(0, 80)}`); return; }
        const t = ev.type;
        if (t === "transcript.partial") tx.textContent = `[partial] ${ev.text}`;
        else if (t === "transcript.final") { tx.textContent = `[final] ${ev.text}`; log("user", `你: ${ev.text}`); }
        else if (t === "response.text") rx.textContent += ev.text;
        else if (t === "response.done") { log("agent", `agent: ${(ev.text || "").slice(0, 80)}`); rx.textContent = `[done] ${ev.text || ""}`; }
        else if (t === "error") log("err", `error: ${ev.code} - ${ev.message}`);
        else log("evt", `evt: ${JSON.stringify(ev).slice(0, 100)}`);
      } else {
        player.enqueue(e.data);
      }
    };
    ws.onclose = (e) => {
      log("evt", `ws close ${e.code} ${e.reason || ""}`);
      ["rt-record", "rt-eos", "rt-disconnect", "rt-update-prompt", "rt-update-voice", "rt-clear-memory"].forEach(
        (id) => ($(id).disabled = true)
      );
    };
    ws.onerror = () => log("err", "ws err");
  });

  $("rt-record").addEventListener("click", async () => {
    try {
      stopRec = await recordMic16kPCM((buf) => {
        if (ws && ws.readyState === 1) ws.send(buf);
      });
      $("rt-record").disabled = true;
      log("evt", "录音中…");
    } catch (e) {
      log("err", `mic: ${e.message}`);
    }
  });

  $("rt-eos").addEventListener("click", async () => {
    if (stopRec) { await stopRec(); stopRec = null; }
    if (ws && ws.readyState === 1) {
      ws.send("audio.eos");
      log("evt", "EOS sent");
    }
    $("rt-record").disabled = false;
  });

  $("rt-disconnect").addEventListener("click", async () => {
    if (stopRec) { await stopRec(); stopRec = null; }
    if (ws) ws.close(1000);
    if (player) await player.close();
  });

  $("rt-update-prompt").addEventListener("click", () => {
    const p = window.prompt("新 prompt:", session?.prompt || "");
    if (!p || !ws) return;
    ws.send(JSON.stringify({ type: "session.update", prompt: p }));
    log("evt", `session.update prompt → "${p.slice(0, 30)}..."`);
  });

  $("rt-update-voice").addEventListener("click", () => {
    const v = window.prompt("新 voice:", session?.voice || "default_zh_female");
    if (!v || !ws) return;
    ws.send(JSON.stringify({ type: "session.update", voice: v }));
    log("evt", `session.update voice → ${v}`);
  });

  $("rt-clear-memory").addEventListener("click", () => {
    if (!ws) return;
    ws.send(JSON.stringify({ type: "memory.clear" }));
    log("evt", "memory.clear sent");
  });
}
