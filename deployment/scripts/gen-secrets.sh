#!/usr/bin/env bash
# gen-secrets.sh — 生成 RTVoice 生产部署所需的所有随机密钥
#
# 用法：
#   bash scripts/gen-secrets.sh
#   bash scripts/gen-secrets.sh >> .env   # 追加到 .env（如果已有 .env，需手动合并）
#
# 说明：每次运行生成新密钥，输出直接可复制到 .env 对应字段

set -euo pipefail

gen() {
  python3 -c "import secrets; print(secrets.token_urlsafe($1))"
}

gen_hex() {
  python3 -c "import secrets; print(secrets.token_hex($1))"
}

echo "# ======================================================"
echo "# RTVoice 生产密钥（$(date '+%Y-%m-%d %H:%M:%S') 生成）"
echo "# 将以下值填入 .env 对应字段"
echo "# ======================================================"
echo ""
echo "LIVEKIT_API_KEY=rtvoice-$(gen 8)"
echo "LIVEKIT_API_SECRET=$(gen 32)"
echo ""
echo "APP_API_KEY=$(gen 32)"
echo ""
echo "RTVOICE_API_KEY=$(gen 32)"
echo ""
echo "RTVOICE_ADMIN_PASSWORD=$(gen 16)"
echo "RTVOICE_SESSION_SECRET=$(gen_hex 32)"
echo ""
echo "TTS_ADMIN_API_KEY=$(gen 32)"
echo ""
echo "GRAFANA_ADMIN_PASSWORD=$(gen 16)"
