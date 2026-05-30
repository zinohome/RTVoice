# Manual Validation Queue

需要**用户参与 / 浏览器 / 主观判断**的 prod 验收项汇总。

按 SP 来源分组，等未来某个 UI / 验收节点批量扫一遍。

## 来源

> 见 `memory/feedback_sp_prod_gate.md` 的 A/B 拆分规则。本队列 = 类 A。
> 类 B（可脚本化）必须当 SP 闭环，不进队列。

## 待验项

### 来自 v0.7（CosyVoice 3.0 升级）

- [ ] 浏览器端到端对话 first_audio_ms 实测（目标 150ms TTS 首音延迟）
- [ ] barge-in UX：说话打断时丝滑度 / 半句中止是否自然
- [ ] 主观音质对比 v0.6（CosyVoice 2 → 3.0）
- [ ] 长稳跑 1 周观察：crash / mem leak / 音质波动

### 来自 v0.13 (SP6 Multi-tenant Auth)

- [ ] 浏览器端真三方 client 用独立 key 调通 STT/TTS/Realtime 三 service
- [ ] quota 触发后用户侧错误提示是否清晰

### 来自 v0.12 (SP5 Adoption Bridge / web demo)

- [ ] 4 tab（STT / TTS / Realtime / Tokens）浏览器端全跑通
- [ ] CORS 在真三方 origin 下工作正常
- [ ] Grafana 仪表盘观察实际流量曲线

### 来自下游集成

- [ ] CozyVoice 应用项目接入 RTVoice 作 STT/TTS 后端 — 端到端实测
- [ ] COZYVOICE_INTEGRATION.md 文档对照实操是否可用

## 批量验收时机

候选触发条件（任一满足可考虑批量扫）：
- web demo (SP5) 打磨到可用 → 浏览器项立刻能扫一批
- CozyVoice 接入需求来 → 三方集成项跟着扫
- 上线前 / 公开演示前 → 全队列扫一遍

## 节奏

- 每次新 SP 完工，把当 SP 的类 A 项追加到本文件对应 section
- 当 SP changelog 末尾用"详见 MANUAL_VALIDATION_QUEUE.md" 引一行，不再每个 SP 单独列 ⏳
- 批量验收完一项就 `[x]`，全勾再删 section
