#!/usr/bin/env bash
# 40-values.sh — write the hub's Helm values copy on the deploy host (never committed).
# Everything that differs per account is read from where it already lives: the admin-ui Ingress
# (allow-list CIDRs, certificate, via kubectl + jq), the gateway's terraform outputs (Cognito pool,
# app client, Hosted UI domain), STS (ECR registry), plus one small env file for the hub's own ids.
# Secrets: HUB_TOKEN and SESSION_SECRET are generated once and kept in ~/hub-snapshots (HUB_TOKEN is
# reused by the gateway-proxy step as POLICY_HUB_TOKEN); ADMIN_API_TOKEN comes from 30-service-token.sh.
# The result is rendered with `helm template` as proof, and nothing is installed here.
# Env file (~/hub-snapshots/hub-deploy.env): HUB_HOST, HUB_ROLE_ARN, AGENTCORE_REGISTRY_ID,
#   AGENTCORE_GATEWAY_ID, AGENTCORE_GATEWAY_URL, MCP_OAUTH_CLIENT_ID; optional NAMESPACE, GATEWAY_TF_DIR,
#   CHART_DIR, ADMIN_UI_INGRESS, HUB_IMAGE_TAG, COMPANY_MARKETPLACE_REPO/NAME/REF (company plugin repository).
set -euo pipefail
SNAP="$HOME/hub-snapshots"; mkdir -p "$SNAP"; umask 077
ENV_FILE="${1:-$SNAP/hub-deploy.env}"
[ -f "$ENV_FILE" ] || { echo "env file not found: $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
for v in HUB_HOST HUB_ROLE_ARN AGENTCORE_REGISTRY_ID AGENTCORE_GATEWAY_ID AGENTCORE_GATEWAY_URL MCP_OAUTH_CLIENT_ID; do
  [ -n "${!v:-}" ] || { echo "$v is empty in $ENV_FILE" >&2; exit 1; }
done
NS="${NAMESPACE:-llm-gateway}"
TF_DIR="${GATEWAY_TF_DIR:-$HOME/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-${DEPLOY_ENV:-dev}}"
CHART_DIR="${CHART_DIR:-$HOME/ai-governance-hub/deploy/helm/governance-hub}"
ING="${ADMIN_UI_INGRESS:-llm-gateway-admin-ui}"
TAG="${HUB_IMAGE_TAG:-0.1.0}"
OUT="$HOME/governance-hub-values.yaml"
for c in kubectl jq terraform helm aws openssl; do command -v "$c" >/dev/null || { echo "$c not found" >&2; exit 1; }; done
[ -d "$CHART_DIR" ] || { echo "chart not found: $CHART_DIR (clone the hub repo first)" >&2; exit 1; }
[ -s "$SNAP/admin-api-token" ] || { echo "missing $SNAP/admin-api-token — run 30-service-token.sh first" >&2; exit 1; }

# Per-account facts, each from the tool that owns the format.
ING_JSON=$(kubectl get ingress "$ING" -n "$NS" -o json)
CIDRS=$(jq -er '.metadata.annotations["alb.ingress.kubernetes.io/inbound-cidrs"]' <<<"$ING_JSON")
CERT=$(jq -er '.metadata.annotations["alb.ingress.kubernetes.io/certificate-arn"]' <<<"$ING_JSON")
TFO=$(terraform -chdir="$TF_DIR" output -json)
POOL=$(jq -er '.cognito_user_pool_id.value' <<<"$TFO")
CLIENT=$(jq -er '.cognito_client_id.value' <<<"$TFO")
HOSTED=$(jq -er '.cognito_hosted_ui_domain.value' <<<"$TFO")
REGION=$(jq -er '.cognito_issuer_url.value' <<<"$TFO" | sed -E 's#https://cognito-idp\.([a-z0-9-]+)\.amazonaws\.com/.*#\1#')
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
aws ecr describe-images --repository-name llm-gateway/governance-hub --image-ids imageTag="$TAG" >/dev/null \
  || { echo "image tag $TAG not in ECR llm-gateway/governance-hub" >&2; exit 1; }

# Secrets: generate once, reuse afterwards (the gateway-proxy step reads hub-token).
[ -s "$SNAP/hub-token" ] || openssl rand -hex 32 > "$SNAP/hub-token"
[ -s "$SNAP/session-secret" ] || openssl rand -hex 32 > "$SNAP/session-secret"
HUB_TOKEN=$(<"$SNAP/hub-token"); SESSION_SECRET=$(<"$SNAP/session-secret"); ADMIN_API_TOKEN=$(<"$SNAP/admin-api-token")

[ -f "$OUT" ] && cp "$OUT" "$SNAP/$(date +%Y%m%d-%H%M%S)-governance-hub-values.yaml.bak"
cat > "$OUT" <<EOT
# governance-hub values — written by 40-values.sh on $(date -u +%FT%TZ); holds secrets, never commit.
replicaCount: 1
image:
  repository: $REGISTRY/llm-gateway/governance-hub
  tag: "$TAG"
  pullPolicy: IfNotPresent
serviceAccount:
  create: true
  name: governance-hub
  annotations:
    eks.amazonaws.com/role-arn: $HUB_ROLE_ARN
service:
  port: 8080
env:
  HUB_LANG: "ko"
  HUB_TZ: "${HUB_TZ:-Asia/Seoul}"
  AWS_REGION: "$REGION"
  DDB_TABLE: "ai-governance-hub"
  ADMIN_API_URL: "http://llm-gateway-admin-api.$NS.svc:8080"
  OIDC_ISSUER: "https://cognito-idp.$REGION.amazonaws.com/$POOL"
  OIDC_CLIENT_ID: "$CLIENT"
  OIDC_AUTHORIZE_URL: "https://$HOSTED/oauth2/authorize"
  OIDC_TOKEN_URL: "https://$HOSTED/oauth2/token"
  OIDC_REDIRECT_URI: "https://$HUB_HOST/auth/callback"
  OIDC_SCOPES: "openid email profile"
  ADMIN_GROUP: "ClaudeAdmin"
  AGENTCORE_REGISTRY_ID: "$AGENTCORE_REGISTRY_ID"
  AGENTCORE_GATEWAY_ID: "$AGENTCORE_GATEWAY_ID"
  AGENTCORE_GATEWAY_URL: "$AGENTCORE_GATEWAY_URL"
  MCP_OAUTH_CLIENT_ID: "$MCP_OAUTH_CLIENT_ID"
  MCP_OAUTH_CALLBACK_PORT: "8094"
  MCP_OAUTH_METADATA_URL: "https://cognito-idp.$REGION.amazonaws.com/$POOL/.well-known/openid-configuration"
  MCP_OAUTH_SCOPES: "openid email"
  COMPANY_MARKETPLACE_REPO: "${COMPANY_MARKETPLACE_REPO:-}"
  COMPANY_MARKETPLACE_NAME: "${COMPANY_MARKETPLACE_NAME:-}"
  COMPANY_MARKETPLACE_REF: "${COMPANY_MARKETPLACE_REF:-}"
secrets:
  HUB_TOKEN: "$HUB_TOKEN"
  ADMIN_API_TOKEN: "$ADMIN_API_TOKEN"
  SESSION_SECRET: "$SESSION_SECRET"
  OIDC_CLIENT_SECRET: ""
ingress:
  enabled: true
  className: alb
  host: $HUB_HOST
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS": 443}]'
    alb.ingress.kubernetes.io/certificate-arn: $CERT
    alb.ingress.kubernetes.io/ssl-policy: ELBSecurityPolicy-TLS13-1-2-2021-06
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
    alb.ingress.kubernetes.io/success-codes: "200"
    alb.ingress.kubernetes.io/inbound-cidrs: $CIDRS
EOT

# Proof: the chart renders with this file, and the summary below carries no secret.
helm template governance-hub "$CHART_DIR" -n "$NS" -f "$OUT" >/dev/null
echo "values written: $OUT ($(wc -l <"$OUT") lines, mode $(stat -c %a "$OUT"))"
echo "host $HUB_HOST | image $TAG | cidrs $(tr ',' '\n' <<<"$CIDRS" | wc -l) entries | cert ${CERT##*/} | pool $POOL"
echo "next: helm upgrade --install governance-hub $CHART_DIR -n $NS -f $OUT"
