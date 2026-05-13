# Browser Quickstart

单文件 HTML demo——浏览器端验证 RTVoice 接通：/info、/v1/tokens、/v1/tts、/v1/realtime WS。

## 跑法

最简方式：直接用浏览器打开 `examples/browser-quickstart/index.html` 文件 (file://)。

**但** 用 file:// 时浏览器对 https://192.168.66.163 有 CORS / 自签证书双重门槛。**推荐**：

1. 先在浏览器访问 `https://192.168.66.163/static/` 一次（任何 RTVoice prod 静态页都行），通过浏览器的 "Advanced → Proceed" 临时信任自签 CA。或先用 `../../scripts/get-rtvoice-ca.sh` 把 root CA 导入系统/浏览器信任链。

2. 然后任选其一：
   - **本地起 HTTP server**：`cd examples/browser-quickstart && python3 -m http.server 8080`，访问 `http://localhost:8080/`
   - **直接 file://**：可能浏览器 mixed-content 报错，按浏览器提示放行

3. 在页面 Config fieldset 里填 `RTVoice base URL`（默认 `https://192.168.66.163`）+ `Bearer`（rtvoice-admin create 出的 secret），点 Save。

4. 4 个 fieldset 依次点测：
   - **/info**：应 200 显示 service+version
   - **/v1/tokens**：应 200 + JWT length
   - **/v1/tts/stream**：应 200 + 显示字节数 + <audio> 可播
   - **/v1/realtime**：应 onopen + protocol=bearer.xxx echo

## CORS 注意

如果 `Bearer` 在不同 origin 触发 CORS preflight 失败：
1. ssh prod，在 `.env` 加 `RTVOICE_CORS_ORIGINS=http://localhost:8080,...`（多个用逗号）
2. `docker compose restart token-server tts-server realtime-server`（watcher 不重载 CORS，需要 restart）

## 这个 demo 没涵盖的

- **STT 录音**：浏览器 mic capture → PCM 16k mono → WS /v1/asr。比较复杂的 AudioWorklet 逻辑，建议看 `clients/web/js/stt.js` 实现
- **Realtime 全链路对话**：建立 WS 后还要送 audio 帧 + 收 transcript / response.audio 事件。本示例只验证 WS 握手 + protocol echo
