#!/usr/bin/env bash
# 30-service-token.sh — issue the hub's admin-api service token with a cached gateway-cli
# admin login (run where admin-api is reachable, e.g. over VPN) and copy it to the deploy
# host. The token is written to ~/hub-admin-api-token (mode 0600) and never printed.
# Needs: gateway-cli login done (~/.gateway-cli/oidc-tokens.json, id_token valid ~1h), curl, jq, python3.
# Env: ADMIN_API_URL (https://admin-api.<domain>), DEPLOY_HOST (ssh target, e.g. ubuntu@<deploy-ec2>).
set -euo pipefail
: "${ADMIN_API_URL:?}"; : "${DEPLOY_HOST:?e.g. ubuntu@<deploy-ec2>}"
TOK=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.gateway-cli/oidc-tokens.json")))["id_token"])')
OUT=$(curl -fsS -X POST "$ADMIN_API_URL/admin/service-tokens" -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d '{"name":"governance-hub","expiry_days":365}')
umask 077; jq -r .token <<<"$OUT" > ~/hub-admin-api-token
echo "issued: $(jq -r '[.name,.token_prefix,.expires_at]|@tsv' <<<"$OUT")"
scp -q ~/hub-admin-api-token "$DEPLOY_HOST:hub-snapshots/admin-api-token" && echo "copied to $DEPLOY_HOST:~/hub-snapshots/admin-api-token"
