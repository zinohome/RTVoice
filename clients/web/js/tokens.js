import cfg from "./config.js";
import { log } from "./app.js";

export function setupTokens() {
  document.getElementById("tk-go").addEventListener("click", async () => {
    const identity = document.getElementById("tk-identity").value.trim();
    const room = document.getElementById("tk-room").value.trim();
    const ttl = parseInt(document.getElementById("tk-ttl").value);
    const status = document.getElementById("tk-status");

    if (!identity || !room) {
      log("err", "tokens: identity/room required");
      return;
    }
    status.textContent = "请求中…";
    try {
      const r = await fetch(`${cfg.base.replace(/\/$/, "")}/v1/tokens`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...cfg.authHeaders() },
        body: JSON.stringify({ identity, room, ttl_minutes: ttl }),
      });
      if (!r.ok) {
        const body = await r.text();
        log("err", `tokens: HTTP ${r.status} ${body.slice(0, 100)}`);
        status.textContent = `失败 (${r.status})`;
        return;
      }
      const j = await r.json();
      document.getElementById("tk-token").value = j.token;
      document.getElementById("tk-url").value = j.url;
      log("evt", `token issued: identity=${j.identity} room=${j.room}`);
      status.textContent = "完成";
    } catch (e) {
      log("err", `tokens: ${e.message}`);
      status.textContent = "失败";
    }
  });
}
