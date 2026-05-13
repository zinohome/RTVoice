#!/usr/bin/env bash
# e2e-smoke.sh — 模拟"刚下载 RTVoice 的第三方用户"端到端烟测。
#
# 7 个 step 全过 → RTVoice 通用接入 ready。
# 失败任一 → 该 step 是真消费者会卡的位置。
#
# 用法：
#   RTVOICE_HOST=root@192.168.66.163 \
#   RTVOICE_BASE_URL=https://192.168.66.163 \
#   ./scripts/e2e-smoke.sh
#
# 退出码：0 全过；非零 = 卡在哪一步。

set -uo pipefail

HOST="${RTVOICE_HOST:-root@192.168.66.163}"
BASE="${RTVOICE_BASE_URL:-https://192.168.66.163}"
TMPDIR="$(mktemp -d -t rtvoice-smoke-XXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

CA="${TMPDIR}/caddy-root.crt"
KEY_FILE="${TMPDIR}/smoke.key"
PASS=0
FAIL=0

step() { echo; echo "▶ $*"; }
ok()   { PASS=$((PASS+1)); echo "  ✅ $*"; }
fail() { FAIL=$((FAIL+1)); echo "  ❌ $*" >&2; }

# Step 1 — fetch Caddy root CA
step "Step 1: get Caddy root CA"
if ssh "${HOST}" 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > "${CA}" 2>/dev/null && grep -q "BEGIN CERTIFICATE" "${CA}"; then
  ok "got root CA ($(stat -c%s "${CA}") bytes)"
else
  fail "拉 root CA 失败；是否启了 caddy？"; exit 1
fi

# Step 2 — admin CLI create smoke key
step "Step 2: rtvoice-admin create smoke key"
KEY_JSON=$(ssh "${HOST}" 'docker exec rtvoice-realtime rtvoice-admin create --name e2e-smoke --scopes stt,tts,tokens,realtime --sessions-concurrent 3 --notes "e2e-smoke" 2>&1' || true)
SECRET=$(echo "$KEY_JSON" | grep -oE '"secret":\s*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
KID=$(echo "$KEY_JSON" | grep -oE '"id":\s*"key_[A-Za-z0-9_-]+"' | head -1 | sed -E 's/.*"(key_[^"]+)".*/\1/')
if [[ -n "$SECRET" && -n "$KID" ]]; then
  echo "$SECRET" > "$KEY_FILE"
  ok "created key $KID"
else
  fail "admin CLI 没返 key；输出：$KEY_JSON"; exit 1
fi

# 结束时 revoke
cleanup() {
  rm -rf "$TMPDIR"
  ssh "${HOST}" "docker exec rtvoice-realtime rtvoice-admin revoke $KID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Step 3 — HTTPS /info (TLS trust 验证)
step "Step 3: HTTPS /info（verify with root CA）"
INFO=$(curl --cacert "$CA" -sS -w '\n%{http_code}' "${BASE}/info" 2>&1)
HTTP="${INFO##*$'\n'}"; BODY="${INFO%$'\n'*}"
if [[ "$HTTP" == "200" ]] && echo "$BODY" | grep -q '"service"'; then
  ok "/info HTTP 200, service=$(echo "$BODY" | grep -oE '"service":"[^"]+"' | head -1)"
else
  fail "/info HTTP $HTTP — TLS trust 或 Caddy 路由问题"; exit 1
fi

# Step 4 — /v1/tokens with Bearer
step "Step 4: POST /v1/tokens (scope=tokens)"
HTTP=$(curl --cacert "$CA" -sS -o "${TMPDIR}/tokens.json" -w "%{http_code}" \
  -X POST "${BASE}/v1/tokens" \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"room":"e2e-smoke","identity":"u"}')
if [[ "$HTTP" == "200" ]] && grep -q '"token"' "${TMPDIR}/tokens.json"; then
  ok "tokens HTTP 200, JWT len=$(grep -oE '"token":"[^"]+"' "${TMPDIR}/tokens.json" | head -c 200 | wc -c)"
else
  fail "tokens HTTP $HTTP"; cat "${TMPDIR}/tokens.json" >&2
fi

# Step 5 — /v1/tts/stream binary
step "Step 5: POST /v1/tts/stream (binary audio)"
HTTP=$(curl --cacert "$CA" -sS -o "${TMPDIR}/tts.pcm" -w "%{http_code}" \
  -X POST "${BASE}/v1/tts/stream" \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"text":"端到端测试","voice":"default_zh_female","speed":1.0}' --max-time 60)
SIZE=$(stat -c%s "${TMPDIR}/tts.pcm" 2>/dev/null || echo 0)
if [[ "$HTTP" == "200" ]] && [[ "$SIZE" -gt 1000 ]]; then
  ok "tts HTTP 200, PCM ${SIZE} bytes ($((SIZE / 48000))s @ 24kHz)"
else
  fail "tts HTTP $HTTP size=$SIZE"
fi

# Step 6 — /v1/sessions create + DELETE
step "Step 6: POST /v1/sessions + DELETE"
HTTP=$(curl --cacert "$CA" -sS -o "${TMPDIR}/sess.json" -w "%{http_code}" \
  -X POST "${BASE}/v1/sessions" \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{}')
SID=$(grep -oE '"session_id":\s*"[^"]+"' "${TMPDIR}/sess.json" | sed -E 's/.*"([^"]+)".*/\1/')
if [[ "$HTTP" == "201" ]] && [[ -n "$SID" ]]; then
  ok "session created: $SID"
  HTTP2=$(curl --cacert "$CA" -sS -o /dev/null -w "%{http_code}" \
    -X DELETE "${BASE}/v1/sessions/$SID" \
    -H "Authorization: Bearer $SECRET")
  if [[ "$HTTP2" == "204" ]]; then ok "DELETE 204"; else fail "DELETE HTTP $HTTP2"; fi
else
  fail "sessions HTTP $HTTP"; cat "${TMPDIR}/sess.json" >&2
fi

# Step 7 — Prometheus per-key metric 验真接通了
step "Step 7: verify Prometheus per-key metric tracked this key"
sleep 3
METRIC=$(ssh "${HOST}" "curl -sS 'http://127.0.0.1:9091/api/v1/query?query=rtvoice_requests_total%7Bkey_id%3D%22$KID%22%7D' 2>&1" || echo "{}")
# 数 result 中的 series 个数（每 series 一个 "metric": 标签集 + 一个 "value": 时戳）
COUNT=$(echo "$METRIC" | grep -c '"endpoint":')
if [[ "$COUNT" -gt 0 ]]; then
  ok "rtvoice_requests_total{key_id=$KID} has $COUNT series (per-key tracking confirmed)"
else
  fail "Prometheus 没抓到本次流量 metrics"
fi

echo
echo "════════════════════════════════════════════"
echo "  PASSED: $PASS    FAILED: $FAIL"
if [[ "$FAIL" -eq 0 ]]; then
  echo "  ✅ 端到端通用接入 ready"
else
  echo "  ❌ $FAIL 项卡了；上面 ❌ 是真消费者会撞的点"
fi
echo "════════════════════════════════════════════"
exit $FAIL
