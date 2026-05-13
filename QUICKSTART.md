# RTVoice 接入快速开始（30 分钟跑通）

要把 RTVoice 接进你的项目，按这 5 步走。完整工程参考 `examples/python-quickstart/` 和 `examples/browser-quickstart/`。

**前提**：RTVoice prod 已跑（默认 `192.168.66.163`，按 [DEPLOY.md](./DEPLOY.md) 部署）。

---

## Step 1 — 信任 Caddy 自签 root CA（30 秒）

```bash
git clone https://github.com/zinohome/RTVoice
cd RTVoice
./scripts/get-rtvoice-ca.sh                  # 默认 ssh root@192.168.66.163
# 或：RTVOICE_HOST=user@host ./scripts/get-rtvoice-ca.sh
```

脚本输出 OS/浏览器 trust 指引。或自测时 `curl --cacert caddy-root.crt ...` / 直接 `curl -k`。

确认信任生效：
```bash
curl --cacert caddy-root.crt https://192.168.66.163/info
# {"service":"realtime-server","version":"0.17.1",...}
```

---

## Step 2 — 拿一把 API key（1 分钟）

```bash
ssh root@192.168.66.163 'docker exec rtvoice-realtime rtvoice-admin create \
  --name <your-app-name> \
  --scopes stt,tts,tokens,realtime \
  --sessions-concurrent 10 \
  --sessions-per-hour 1000 \
  --notes "<your app description>"'
```

输出含 `id`（key_xxx，可分享）和 `secret`（**只显示一次**，必须立刻存进你应用 `.env`）。

**热加载**：写完 ~100ms 内 4 service 全部 pickup，**不需 rebuild / restart**。

---

## Step 3 — 装 SDK（2 分钟）

```bash
# 当前只能 git URL 装（PyPI 发布留待 SDK GA）
pip install "git+https://github.com/zinohome/RTVoice.git#subdirectory=clients/python"
```

```python
from rtvoice_client import Client
c = Client(api_key="<your-secret>", base_url="https://192.168.66.163",
           verify="caddy-root.crt")  # 或 verify=False
```

---

## Step 4 — 跑通最小示例（5 分钟）

```bash
cp examples/python-quickstart/.env.example examples/python-quickstart/.env
# 填 RTVOICE_BASE_URL + RTVOICE_API_KEY + RTVOICE_CA_FILE
cd examples/python-quickstart
pip install -e ../../clients/python
pip install httpx
python main.py
```

期望输出：
```
✅ token-server /v1/tokens → JWT  (issued for room=demo)
✅ TTS POST /v1/tts/stream → 96000 bytes PCM (4.0s @ 24kHz)
✅ Realtime POST /v1/sessions → sess_xxx; DELETE → 204
```

---

## Step 5 — 看 metrics 确认真接通（1 分钟）

```bash
ssh root@192.168.66.163 'curl -s http://127.0.0.1:9091/api/v1/query?query=rtvoice_requests_total | jq'
```

应能看到 `key_id="key_xxx"` 含**你**这把 key 的请求计数 > 0。

或开 Grafana：`http://192.168.66.163:3000`（admin/admin）→ "RTVoice — Per-Key Tenant View"。

---

## 下一步深入

- **接入文档完整版**：[COZYVOICE_INTEGRATION.md](./COZYVOICE_INTEGRATION.md)（虽叫 CozyVoice，但通用模式）
- **网络拓扑速查**：[docs/QUICKSTART-TOPOLOGY.md](./docs/QUICKSTART-TOPOLOGY.md)
- **API 契约**：[docs/api/](./docs/api/)（含 WS protocol 单独文档）
- **架构总览**：[ARCHITECTURE.md](./ARCHITECTURE.md)

## 阻塞 / 常见故障

| 现象 | 排查 |
|---|---|
| `curl ... 000` HTTPS 全失败 | Caddy 没起 / root CA 未信任。先 `./scripts/get-rtvoice-ca.sh`，再 `curl --cacert` |
| `401 auth.missing_token` | Bearer 没传或拼写错 |
| `403 auth.scope_denied` | key 创建时 scopes 没含目标 service；rotate key 加 scope |
| `429 quota.session_concurrent` | session 并发触顶；admin CLI 调高 `--sessions-concurrent` |
| 浏览器 WS close 1006 | 服务器没 echo subprotocol（RTVoice 应 v0.15.0+；老版本有这个 bug） |
| Python SDK `verify` 错 | 用 `verify=False`（仅自测）或 `verify="path/to/caddy-root.crt"` |

完整故障排查见 [OPERATIONS.md](./OPERATIONS.md)。
