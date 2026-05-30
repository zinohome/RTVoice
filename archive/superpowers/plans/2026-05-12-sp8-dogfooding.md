# SP8 Dogfooding 实施计划

## D1 — Fake CozyVoice client (foreground)

- T1.1 读 `COZYVOICE_INTEGRATION.md` 第一遍，列每节预期能做的事
- T1.2 准备 prod RTVOICE_API_KEY（用 dev admin CLI create 一个 dogfood-d1 scope=stt,tts,tokens）
- T1.3 写 `clients/dogfood/fake_cozyvoice_client.py` ~ 50 行：
  - 拿 token（POST /v1/tokens）
  - WS /v1/asr 上传 1s 测试音频（合成 sine 不真识别也行）
  - POST /v1/tts/synthesize（或 stream）合成短句
  - 边写边记每个"文档没说"的细节
- T1.4 跑通 + 记 finding 到 `docs/superpowers/findings/sp8-d1-findings.md`

## D2 — OpenAPI codegen probe (background)

- T2.1 装 `openapi-typescript-codegen` (`npm i -g`) 或用 `npx`
- T2.2 4 service 各 dump `/openapi.json` 到 `/tmp/openapi-dump/{service}.json`
- T2.3 对每个 schema 跑 codegen 到 `/tmp/openapi-codegen/{service}/`
- T2.4 记 finding 到 `docs/superpowers/findings/sp8-d2-findings.md`
  - 每个 service 一节，含：codegen 退出码、stderr、生成文件数、缺失项

## 完工标准

D1 + D2 各自产出 finding 文件 + commit。
