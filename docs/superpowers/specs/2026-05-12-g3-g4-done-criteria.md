# G3 / G4 Done Definition（platform 化清单残项）

**作者**：复盘 2026-05-12
**目的**：把先前被悄悄记成"部分 ✅"的 G3 / G4 写清楚 done 的硬标准，避免目标偏移。

---

## G3 — Per-API-Key Metrics Tracking

### 当前状态

- SP4 加了 3 个 Prometheus metrics（realtime 相关）
- **但只按 service 维度打 label**，没有 `key_id` 维度
- 后果：用户 A vs 用户 B 用量无法区分；quota 告警没法定位到具体 key

### Done 定义（硬标准）

1. **核心 metrics 全部含 `key_id` label**：
   - `rtvoice_requests_total{service,endpoint,key_id,status}` — 请求计数
   - `rtvoice_request_duration_seconds{service,endpoint,key_id}` — 延迟
   - `rtvoice_stt_audio_seconds_total{key_id}` — STT 实际处理音频时长
   - `rtvoice_tts_chars_total{key_id}` — TTS 合成字符数
   - `rtvoice_realtime_session_duration_seconds{key_id}` — Realtime session 时长
2. **`key_id` 取值**：用 `Key.id`（不是原始 token，避免泄漏）；匿名/未鉴权 → `key_id="anonymous"`
3. **基数控制**：label values 限 `Key.id` + `anonymous` + `internal`；不允许 free-text
4. **Grafana 面板**：至少 1 个 panel 按 key 维度 top-N 用量（验证 label 真生效）
5. **测试**：单测覆盖 3 service 各至少 1 metric 含正确 key_id label

### 不算 done

- 只有 `service` label 不算（现状）
- 只在日志里打 key_id，但 Prometheus 没 label，不算
- Grafana 没按 key 维度切片的 panel，不算

### 预估工作量

1 个独立 SP（建议 SP8 候选）。约 8-10 个 T。

---

## G4 — OpenAPI Schema / Capability Discovery 系统化

### 当前状态

- SP1.5 写了 `docs/api/CONVENTIONS.md`（人读规范）
- 统一了 `ErrorResponse` schema
- 各 service 有 `/info` 文本端点
- **但没真发布 `/openapi.json`**，FastAPI 自动生成的 schema 没被 audit / 没契约保证

### Done 定义（硬标准）

1. **每个 service 暴露 `/openapi.json`**（FastAPI 默认即可，但需要 audit）：
   - stt-server、tts-server、token-server、realtime-server 全部
   - 路径稳定（不会随版本飘）
2. **schema 内容必须包含**：
   - 所有 `/v1/` 端点
   - 请求 / 响应 body 完整 Pydantic schema
   - `ErrorResponse` 作为所有错误响应统一 schema
   - Bearer auth security scheme 声明
3. **capability discovery `/info` 升级**：
   - 返回 JSON（不只是文本）
   - 含 `service` / `version` / `capabilities` / `models` 字段
   - `capabilities` 例：`["streaming", "voice_clone", "barge_in"]`
4. **客户端可消费**：能用 `openapi-python-client` / `openapi-typescript-codegen` 生成 client 代码不报错（至少能解析）
5. **CI 守护**（轻量）：snapshot 测试，确保 openapi.json 不会无声变化

### 不算 done

- 只有 CONVENTIONS.md 不算（现状）
- `/info` 仍是纯文本不算
- FastAPI 默认生成但内容不完整 / 含调试端点 / 缺 ErrorResponse 不算

### 预估工作量

约半个 SP（4-6 个 T）。可以和 G3 合并为 "SP8 — Observability + API Discovery"，但建议不要捆绑，吃过 SP4/SP7 捆绑教训。

---

## 推进建议

**SP8 候选**：G3（per-key metrics）单独做 — 价值大、独立性强、有审计/计费意义。
**SP9 候选**：G4（OpenAPI）单独做 — 价值适中、对外集成必备。

不混 SP。每个 SP 完工 changelog 必须按 A/B 分类标 prod 验收。
