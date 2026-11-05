#!/usr/bin/env bash
# 10-cognito-client.sh — public (no secret) Cognito app client for Claude Code's MCP OAuth.
# Claude Code managed MCP entry: oauth {clientId, callbackPort, authServerMetadataUrl, scopes} — public
# client, authorization-code + PKCE, fixed redirect http://localhost:<port>/callback.
# Refs: code.claude.com/docs/en/mcp (OAuth), AWS devguide gateway-inbound-auth (CUSTOM_JWT allowedClients).
# Idempotent: reuses a client with the same name. Prints CLAUDE_CODE_MCP_CLIENT_ID=... for .env.
set -euo pipefail
cd "$(dirname "$0")"; set -a; source ./.env; set +a
NAME="${DEMO_PREFIX}-claude-code-mcp"
CB="http://localhost:${CLAUDE_CODE_MCP_CALLBACK_PORT}/callback"
EXISTING=$(aws cognito-idp list-user-pool-clients --user-pool-id "$COGNITO_USER_POOL_ID" --max-results 60 \
  --query "UserPoolClients[?ClientName=='${NAME}'].ClientId" --output text)
if [[ -n "$EXISTING" && "$EXISTING" != "None" ]]; then
  echo "exists: $NAME"; echo "CLAUDE_CODE_MCP_CLIENT_ID=$EXISTING"; exit 0
fi
ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$COGNITO_USER_POOL_ID" --client-name "$NAME" \
  --no-generate-secret \
  --allowed-o-auth-flows code --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client \
  --callback-urls "$CB" --logout-urls "$CB" \
  --supported-identity-providers COGNITO \
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH \
  --prevent-user-existence-errors ENABLED \
  --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 7 \
  --token-validity-units AccessToken=minutes,IdToken=minutes,RefreshToken=days \
  --query 'UserPoolClient.ClientId' --output text)
echo "created: $NAME"; echo "CLAUDE_CODE_MCP_CLIENT_ID=$ID"
