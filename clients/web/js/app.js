import cfg from "./config.js";
import { setupSTT } from "./stt.js";
import { setupTTS } from "./tts.js";
import { setupRealtime } from "./realtime.js";
import { setupTokens } from "./tokens.js";

export function log(cls, msg) {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = msg;
  const panel = document.getElementById("log");
  panel.appendChild(d);
  panel.scrollTop = panel.scrollHeight;
}

const baseInput = document.getElementById("cfg-base");
const bearerInput = document.getElementById("cfg-bearer");
const saveBtn = document.getElementById("cfg-save");
const status = document.getElementById("cfg-status");

baseInput.value = cfg.base;
bearerInput.value = cfg.bearer;
saveBtn.addEventListener("click", () => {
  cfg.base = baseInput.value.trim();
  cfg.bearer = bearerInput.value.trim();
  status.textContent = "saved ✓";
  setTimeout(() => (status.textContent = ""), 1500);
  log("evt", `config saved: base=${cfg.base}`);
});

const tabs = document.querySelectorAll("nav .tab");
const contents = document.querySelectorAll(".tab-content");
tabs.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    contents.forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

setupSTT();
setupTTS();
setupRealtime();
setupTokens();

tabs[0].click();
log("evt", "RTVoice Web Demo loaded");
