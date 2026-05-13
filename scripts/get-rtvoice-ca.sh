#!/usr/bin/env bash
# get-rtvoice-ca.sh — 一键拉 RTVoice Caddy 自签 root CA 出来，并给出信任步骤。
#
# 用法（任选其一）：
#   ./scripts/get-rtvoice-ca.sh                       # 默认 ssh root@192.168.66.163
#   RTVOICE_HOST=user@host ./scripts/get-rtvoice-ca.sh
#   ./scripts/get-rtvoice-ca.sh path/to/output.crt    # 指定输出位置
#
# 输出：本机 caddy-root.crt（或参数指定路径），以及 OS / 浏览器 trust 指引。
#
# 背景：Caddy `tls internal` 自签 CA 是首次集成的最高阻塞——客户端不信任 CA
# 就 cert chain 验证失败。这个脚本把 root CA 文件取出来，配合 OS / browser
# 系统信任链导入即可。

set -euo pipefail

HOST="${RTVOICE_HOST:-root@192.168.66.163}"
OUT="${1:-caddy-root.crt}"

echo "▶ Fetching Caddy root CA from ${HOST} …"
ssh "${HOST}" 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > "${OUT}"

if [[ ! -s "${OUT}" ]]; then
  echo "❌ Empty file. Caddy container 可能没跑，或 docker-compose.tls.yml 没启 caddy。" >&2
  rm -f "${OUT}"
  exit 1
fi

# 校验是 PEM 证书
if ! grep -q "BEGIN CERTIFICATE" "${OUT}"; then
  echo "❌ ${OUT} 不是合法 PEM 证书；内容：" >&2
  cat "${OUT}" >&2
  rm -f "${OUT}"
  exit 1
fi

CN=$(openssl x509 -in "${OUT}" -noout -subject 2>/dev/null | sed 's/^subject=//')
EXP=$(openssl x509 -in "${OUT}" -noout -enddate 2>/dev/null | sed 's/^notAfter=//')

cat <<EOF
✅ Saved: ${OUT}
   Subject: ${CN}
   Expires: ${EXP}

下一步——根据你的环境选一个：

【1】Linux 系统级（Ubuntu / Debian）信任：
    sudo cp ${OUT} /usr/local/share/ca-certificates/rtvoice-caddy.crt
    sudo update-ca-certificates

【2】macOS 系统级信任：
    sudo security add-trusted-cert -d -r trustRoot \\
         -k /Library/Keychains/System.keychain ${OUT}

【3】Chrome / Edge / Chromium（Linux）：
    Settings → Privacy → Security → Manage certificates →
    Authorities tab → Import → 选 ${OUT} → 勾 "Trust for identifying websites"

【4】Firefox：
    Settings → Certificates → View Certificates → Authorities → Import
    勾 "Trust this CA to identify websites"

【5】curl 临时：
    curl --cacert ${OUT} https://192.168.66.163/info
    或 curl -k 跳过验证（仅自测）

【6】Python httpx / requests：
    httpx.Client(verify="${OUT}")
    requests.get(url, verify="${OUT}")

校验信任生效：
    curl --cacert ${OUT} https://192.168.66.163/info   # 应 200
EOF
