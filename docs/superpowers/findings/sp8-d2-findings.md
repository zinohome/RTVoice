# SP8 D2 — OpenAPI Codegen Probe Findings

**Date**: 2026-05-12
**Probe input**: `/tmp/sp8-d2/{token,realtime,stt,tts}.json`
**Tool**: `npx --yes openapi-typescript-codegen` (latest)
**Node**: v25.9.0
**Output**: `/tmp/openapi-codegen/<service>/`

---

## 总结表

| service | codegen 退出码 | 生成文件数 | Bearer 友好 | WS 路径 | 主要缺字段 |
|---|---|---|---|---|---|
| token-server | 0 | 12 | 半友好（runtime 注入 OK，但 schema 又冗余声明 `authorization` header） | N/A（无 WS） | `securitySchemes`、版本号过期、`/metrics` 泄漏到 SDK |
| realtime-server | 0 | 12 | 半友好（同上） | **缺**（`/v1/sessions/{id}/ws` 不在 schema） | `securitySchemes`、`info.version`=0.12.0、WS 端点缺失、`ErrorResponse` 未绑定到 `ApiError.body` |
| stt-server | 0 | 8 | 不适用（无业务 HTTP 端点） | **缺**（核心 `/v1/stt/stream` WS 不在 schema） | 整套 `/v1/` HTTP 业务面缺失；schema 仅 `/metrics` `/health` `/info` |
| tts-server | 0 | 12 | 半友好 | N/A（HTTP 流） | `securitySchemes`、`/v1/voices` GET / POST / `/v1/tts/stream` 返回都是 `Record<string, any>`（无 typed response model）、`info.version`=0.7.0 |

退出码全部 0，stderr/stdout 全部为空——openapi-typescript-codegen 对 4 份 schema 都能"解析通过"，但生成出来的 client 在质量上有大量 finding。

---

## 每 service 一节

### token-server (`/tmp/sp8-d2/token.json`, info.version=`0.6.2`)

- **命令**：`npx --yes openapi-typescript-codegen --input /tmp/sp8-d2/token.json --output /tmp/openapi-codegen/token/`
- **退出码**：0；stderr/stdout 空
- **生成清单**：
  - `index.ts`、`core/{ApiError,ApiRequestOptions,ApiResult,CancelablePromise,OpenAPI,request}.ts`
  - `models/{HTTPValidationError,TokenRequest,TokenResponse,ValidationError}.ts`
  - `services/DefaultService.ts`（含 3 个方法：`metricsMetricsGet`、`healthHealthGet`、`issueTokenV1TokensPost`）
- **观察**：
  - `OpenAPI.TOKEN: string | Resolver<string>` 全局配置项存在；`core/request.ts:159` 在 token 非空时自动注入 `Authorization: Bearer <token>` — runtime 路径 OK
  - 但服务端 schema 把 `authorization` 显式声明为 header parameter，导致每个 method 多出一个 `authorization?: (string | null)` 形参——和上面的 `OpenAPI.TOKEN` 路径**重复且互相干扰**
  - `/metrics` 被生成成 client 公共方法 — 基础设施端点泄漏进 SDK
  - 缺 `info.description` 里写的 "rtvoice_auth" 与 OpenAPI `securitySchemes` 的绑定
- **finding F1**：`info.version` = `0.6.2`，与 CHANGELOG 当前 v0.14.0 严重错位。token-server 的 FastAPI app `version=` 没跟 SP6/SP7 一起 bump。
- **finding F2**：schema 完全缺 `components.securitySchemes`，未声明 Bearer auth scheme。codegen 因此无法自动把 `Authorization` 标成 secured endpoint 的必填头；只能依赖客户端约定。
- **finding F3**：`authorization` 作为 header parameter 显式列出 → 生成的方法签名有 `authorization?: string | null`，**和 `OpenAPI.TOKEN` 全局通道重复**。集成方两条注入路径择一都行但混淆。
- **finding F4**：`/metrics` 进入 `DefaultService.metricsMetricsGet()` — Prometheus 端点不应是 client SDK 公共表面（侧 finding，4 个 service 全部踩坑）。
- **finding F5**：`TokenResponse` 完整 typed，`request_id` / `error_code` 不存在。错误路径没声明 `ErrorResponse` schema（contradicts SP1.5 `CONVENTIONS.md`：错误统一 ErrorResponse），只有 `HTTPValidationError`（FastAPI 默认 422 形态）。

### realtime-server (`/tmp/sp8-d2/realtime.json`, info.version=`0.12.0`)

- **命令**：`npx --yes openapi-typescript-codegen --input /tmp/sp8-d2/realtime.json --output /tmp/openapi-codegen/realtime/`
- **退出码**：0；stderr/stdout 空
- **生成清单**：
  - `index.ts`、`core/*.ts`（6 个）
  - `models/{ErrorResponse,SessionCreateRequest,SessionCreateResponse}.ts`
  - `services/{DefaultService,SessionsService}.ts`（用了 `tags: ["sessions"]`，被正确切到独立 service class）
- **观察**：
  - `SessionsService.createSessionV1SessionsPost` 返回 typed `SessionCreateResponse`
  - 但响应里的 `ws_url` 是 string，**WebSocket 端点本身（`/v1/sessions/{id}/ws`）不在 OpenAPI paths 内**——SDK 用户拿到 url 后只能裸 `new WebSocket(ws_url)`，schema 不描述 inbound/outbound 消息类型
  - `errors: { 401: 'Auth failed', 422: 'Invalid input', 503: 'Capacity full' }` ——错误状态有描述，但 `ApiError.body: any`（`core/ApiError.ts:12`），**没有 bind 到 `ErrorResponse` model**
- **finding F6**：`info.version` = `0.12.0`，应为 0.14.0
- **finding F7**：唯一一个真正把 `ErrorResponse` 列进 schema 的 service（401/422/503 都 `$ref: ErrorResponse`），是 4 个 service 里 schema 质量最好的一份；其他 3 个应当 align 到这个范式。
- **finding F8**：`ApiError.body: any` —— codegen 工具的限制，但因为 schema 声明了 `ErrorResponse`，**集成方可以手工 `error.body as ErrorResponse`**。如要让 codegen 直接生成 typed error class，需切换到别的工具（`@hey-api/openapi-ts` 或 `openapi-typescript` + zod）。
- **finding F9**：WebSocket 端点 `/v1/sessions/{id}/ws` 在 OpenAPI 3.1 范畴外不可枚举。SP2 的 client-server WS 消息协议在 SDK 里**完全不存在**。建议另存为 AsyncAPI 2.x 文档或 `docs/api/websocket-schema.md` 明文契约（侧 finding：spec SP1.5 也未涵盖）。

### stt-server (`/tmp/sp8-d2/stt.json`, info.version=`0.5.0`)

- **命令**：`npx --yes openapi-typescript-codegen --input /tmp/sp8-d2/stt.json --output /tmp/openapi-codegen/stt/`
- **退出码**：0；stderr/stdout 空
- **生成清单**：
  - `index.ts`、`core/*.ts`（6 个）
  - **`models/` 为空**（无任何业务 model）
  - `services/DefaultService.ts`：只有 `metricsMetricsGet`、`healthHealthGet`、`infoInfoGet`
- **观察**：schema 总字节数 835，是 4 个里最短的；**没有任何 `/v1/` 端点**。从 SDK 集成方角度看 stt-server 完全没有 HTTP 业务面可消费。
- **finding F10**：stt-server 核心 `/v1/stt/stream` WebSocket 端点在 OpenAPI 中完全不可见。G4 done 定义第 2 条"所有 `/v1/` 端点必须在 schema"对 STT 不成立（因为只有 WS）。需要决策：(a) 把 WS handshake URL 至少作为 GET endpoint stub 暴露用作 capability discovery，或 (b) 明文降级承认 STT 不走 OpenAPI 路径，改 AsyncAPI / `/info` 字段描述。
- **finding F11**：`info.version` = `0.5.0`，应为 0.14.0
- **finding F12**：schema 没有 `components.schemas`、没有 `securitySchemes`。生成的 `models/` 目录为空，`index.ts` 也只 re-export `OpenAPI` / `ApiError` / `CancelablePromise` —— 这是一个**几乎无用的 client**。

### tts-server (`/tmp/sp8-d2/tts.json`, info.version=`0.7.0`)

- **命令**：`npx --yes openapi-typescript-codegen --input /tmp/sp8-d2/tts.json --output /tmp/openapi-codegen/tts/`
- **退出码**：0；stderr/stdout 空
- **生成清单**：
  - `index.ts`、`core/*.ts`（6 个）
  - `models/{Body_add_voice_v1_voices_post,HTTPValidationError,TTSRequest,ValidationError}.ts`
  - `services/DefaultService.ts`（7 个方法：metrics/health/info + voices GET/POST + tts/stream POST + voices/{spk_id} DELETE）
- **观察**：
  - `voicesV1VoicesGet`、`addVoiceV1VoicesPost`、`deleteVoiceV1VoicesSpkIdDelete`、`ttsStreamV1TtsStreamPost` **返回类型全部是 `CancelablePromise<Record<string, any>>` / `<any>`** —— 4 个业务端点全部"无 typed response"
  - 请求端：`TTSRequest`（带 `maxLength`/`minLength`/`min/max`）和 `Body_add_voice_v1_voices_post` typed OK；multipart/form-data 正确转 `formData` + `mediaType`
  - `/v1/tts/stream` 返回 audio bytes（streaming），FastAPI 应已用 `StreamingResponse`，schema 写成 `application/json schema: {}` 失实——**返回类型应为 `audio/wav` binary**
- **finding F13**：`info.version` = `0.7.0`，应为 0.14.0
- **finding F14**：4 个业务端点 response schema 全空。`@app.get("/v1/voices")` 等 handler 没用 `response_model=`，FastAPI 推断不出来 → 生成成 `Record<string, any>`。集成方接到响应需要手工 cast，TypeScript 编译失去类型保护。
- **finding F15**：`/v1/tts/stream` 响应声明成 `application/json schema: {}`，但实际是流式 wav/pcm。应改用 `response_class=StreamingResponse` + OpenAPI `content: {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}` override。
- **finding F16**：缺 `ErrorResponse` schema —— TTS 的错误响应只有 FastAPI 默认 422 `HTTPValidationError`，未对齐 SP1.5 CONVENTIONS。

---

## G4 done 标准对照

参考 `docs/superpowers/specs/2026-05-12-g3-g4-done-criteria.md` G4 节。

| # | 硬标准 | 评级 | 证据 |
|---|---|---|---|
| 1 | 4 service 暴露 `/openapi.json`，路径稳定 | ✅ | 4 份 schema 都成功 dump 到 `/tmp/sp8-d2/*.json`，FastAPI 默认 `/openapi.json` 路径 |
| 2 | schema 含所有 `/v1/` 端点 + 完整 req/resp body + 统一 `ErrorResponse` + Bearer security scheme | ❌ | (a) stt 无任何 `/v1/`（只 WS）；(b) tts 4 个业务端点 response body 全空（→ `Record<string, any>`）；(c) `ErrorResponse` 只 realtime 用上，token/tts 仍是 `HTTPValidationError`；(d) **4 份 schema 全部缺 `components.securitySchemes`**，无 Bearer 声明（findings F2/F12/F16） |
| 3 | `/info` 升级为 JSON + service/version/capabilities/models 字段 | ⚠️ | 3 个 service (realtime/stt/tts) 有 `/info` 返回 `{ type: object, additionalProperties: true }`；**但 schema 不给字段约定**，client 拿到 `Record<string, any>`；token-server **完全无 `/info` 端点**（schema 仅 `/metrics` + `/health` + `/v1/tokens`） |
| 4 | 客户端可消费：`openapi-typescript-codegen` 不报错能生成 | ⚠️ | 4 个都"不报错"且退出码 0，stt 生成的 client **空壳无业务方法**；其他 3 个生成可用但 typed response 残缺 |
| 5 | CI 守护 snapshot 测试，防 schema 无声漂移 | ❌ | 仓库无 `tests/test_openapi_snapshot.py` 或同类（D1 已确认） |

---

## 建议（按优先级）

**1. 修业务端点 `response_model=` —— 立即收益最大**

- `services/tts-server/app/main.py`：`/v1/voices` GET、`/v1/voices` POST、`/v1/voices/{spk_id}` DELETE 必须声明 `response_model=ListVoicesResponse` / `AddVoiceResponse` / `DeleteVoiceResponse` 等 Pydantic model
- `/v1/tts/stream` 用 `responses={200: {"content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}}}` 覆盖 OpenAPI（保持 `StreamingResponse` 实现不变）
- 修完后 4 个业务端点的 `CancelablePromise<Record<string, any>>` → typed `CancelablePromise<ListVoicesResponse>`，集成方拿到编译期保护

**2. 统一 4 service 的 OpenAPI `securitySchemes` + `info.version`**

- 在 4 个 `app.openapi_schema` hook 里注入：
  ```python
  components.securitySchemes = {
      "rtvoice_auth": {"type": "http", "scheme": "bearer"}
  }
  ```
  并在受保护端点上加 `security=[{"rtvoice_auth": []}]`
- 同步在 `app = FastAPI(version=...)` 处统一读 `__version__ = "0.14.0"` —— 4 个 service 当前散在 0.5.0 / 0.6.2 / 0.7.0 / 0.12.0
- 修完后：(a) codegen 知道哪些 endpoint 必须带 `Authorization`；(b) 消除"每个方法多冗余 `authorization?: string` 参数"的双通道问题（finding F3）；(c) 版本同步可写 CI snapshot

**3. 显式承认 WebSocket / Streaming 不走 OpenAPI，单独契约化**

- 写 `docs/api/websocket-protocol.md`：列出 STT `/v1/stt/stream`、Realtime `/v1/sessions/{id}/ws`、TTS 流式响应的消息 schema（inbound/outbound JSON shape + binary frame 编码）
- 在 `/info` 响应里加 `capabilities: ["http", "websocket", "streaming"]` 字段，并把 WS URL 模板放在 `/info.endpoints.websocket` 下，作为 capability discovery 唯一权威源
- G4 done 标准 #2 "所有 `/v1/` 端点必须在 schema" 对 STT 无解（核心就是 WS）—— 必须**显式降级**这一条，否则 G4 永远不能 ✅

---

## 附：原始 schema info 字段速查

```
token       title="RTVoice Token Server"               version=0.6.2
realtime    title="RTVoice Realtime Voice Server"     version=0.12.0
stt         title="RTVoice STT Server"                version=0.5.0
tts         title="RTVoice TTS Server (Fun-CosyVoice 3)" version=0.7.0
```

均 OpenAPI 3.1.0；**0 个 service 含 `components.securitySchemes`**。
