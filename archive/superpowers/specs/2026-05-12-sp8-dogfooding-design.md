# SP8 — Dogfooding Sprint 设计

**Why**：05-12 复盘发现"真用户路径 2/10"评分被偷偷降级——实际上 8/10 维度可自验。这个 sprint 不写新 feature，纯粹**当消费者用自己的平台**，把"需要真用户才能发现"的范畴严格收窄到主观体验。

**Non-goal**：不做任何 platform feature（不开 G3 / G4 / per-key metrics 实施）。SP8 只输出**发现清单**，驱动 SP9+。

## 范围 — D1 + D2 并行（本次）

### D1 — 跟 COZYVOICE_INTEGRATION.md 当 fresh 消费者

- 假装从未碰过 RTVoice，照文档从头跟
- 用 Python 写 50 行的 fake CozyVoice client：调 prod 的 stt + tts + tokens 三 service
- 每处文档不清楚 / 步骤跑不通 / 错误信息没用 → 记到 `docs/superpowers/findings/sp8-d1-findings.md`
- 完成标准：50 行 client 能跑通完整 STT 录音 → 文字 + 文字 → TTS 音频路径

### D2 — OpenAPI codegen 自验（驱动 G4）

- prod 4 服务暴露 `/openapi.json`（已确认）
- 对每个 service 跑 `openapi-typescript-codegen`（站 web 集成方视角）
- 列每个失败 / 警告 / 缺字段 / 错误 schema 不齐到 `docs/superpowers/findings/sp8-d2-findings.md`
- 完成标准：4 service 全跑完 codegen，**无论成功失败都有 finding**

## 范围 — D3+ 不在本次

D3 合成负载 / D4 浏览器 demo / D5 整理 SP9 spec 留后续 dogfood 轮。

## 输出

- `docs/superpowers/findings/sp8-d1-findings.md` — D1 发现
- `docs/superpowers/findings/sp8-d2-findings.md` — D2 发现
- `clients/dogfood/fake-cozyvoice-client.py` — D1 的 50 行 demo（可作 example）

## 不做

- 不修任何 finding 在 SP8 内部（修在 SP9）
- 不评分文档质量主观感受，只列**具体不能往下走**的卡点
- 不补全 SP8 发现的 bug（除非阻塞 dogfood 本身往下走）

## 当 finding 阻塞

若 D1 走到一半因 prod bug 卡死，**当场用最小补丁解锁**（commit "fix(dogfood-blocker)" 单列），继续 dogfood。所有这种 fix 进 finding 记录。
