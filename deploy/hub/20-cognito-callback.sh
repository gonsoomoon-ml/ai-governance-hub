#!/usr/bin/env bash
# 20-cognito-callback.sh — register the hub's OIDC callback on the gateway's Cognito app client.
# The client's callback list is terraform-managed (var.cognito_callback_urls in the gateway's
# terraform.tfvars on the deploy host): a CLI update-user-pool-client would be undone by the next
# apply. So this rewrites that one list in terraform.tfvars = current list (read back through
# `terraform console`, the authoritative reader) + the hub callback, verifies terraform reads the
# new list back (restores the backup otherwise), then prints a targeted plan. `terraform apply`
# is yours. Same guard as the gateway's update-scripts/19-admin-login.sh callback.
# The hub's /auth/logout only clears its cookie, so cognito_logout_urls stays untouched.
# Run on the deploy host (terraform, jq, python3, the gateway clone). Idempotent.
# Env (deploy/hub/.env or exported): HUB_HOST, GATEWAY_TF_DIR (default: the gateway clone's
# deployment/terraform/environments/llm-gateway-${DEPLOY_ENV:-dev}).
set -euo pipefail
cd "$(dirname "$0")"
if [ -f ./.env ]; then set -a; source ./.env; set +a; fi
: "${HUB_HOST:?HUB_HOST (the hub https host) is required}"
case "$HUB_HOST" in *CHANGE_ME*) echo "HUB_HOST still has CHANGE_ME — set it in deploy/hub/.env" >&2; exit 1;; esac
TF_DIR="${GATEWAY_TF_DIR:-$HOME/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-${DEPLOY_ENV:-dev}}"
TFVARS="$TF_DIR/terraform.tfvars"
TARGET="${COGNITO_CLIENT_TF_ADDR:-module.cognito.aws_cognito_user_pool_client.cli}"
VAR=cognito_callback_urls
CALLBACK="https://${HUB_HOST}/auth/callback"
[ -f "$TFVARS" ] || { echo "not found: $TFVARS (set GATEWAY_TF_DIR)" >&2; exit 1; }
for c in terraform jq python3; do command -v "$c" >/dev/null || { echo "$c not found — run on the deploy host" >&2; exit 1; }; done

read_list() {  # the list as terraform sees it, one URL per line ([] = empty, not a failure)
  local out
  out=$(echo "jsonencode(var.$VAR)" | terraform -chdir="$TF_DIR" console 2>/dev/null | tail -1)
  jq -r 'fromjson | .[]' <<<"$out"
}
CUR=$(read_list) || { echo "terraform console could not read var.$VAR in $TF_DIR" >&2; exit 1; }

if [[ $'\n'"$CUR"$'\n' == *$'\n'"$CALLBACK"$'\n'* ]]; then
  echo "already in tfvars: $CALLBACK — only terraform plan / apply is left"
else
  WANT=$(printf '%s\n%s\n' "$CUR" "$CALLBACK" | sed '/^$/d')
  BLOCK="$VAR = ["$'\n'
  while IFS= read -r u; do BLOCK+="  \"$u\","$'\n'; done <<<"$WANT"
  BLOCK+="]"
  SNAP="$HOME/hub-snapshots"; mkdir -p "$SNAP"
  BAK="$SNAP/$(date +%Y%m%d-%H%M%S)-terraform.tfvars.bak"
  cp "$TFVARS" "$BAK"
  echo "--- planned $VAR block in $(basename "$TFVARS") (backup: $BAK) ---"; echo "$BLOCK"
  # Replace the one existing `cognito_callback_urls = [ ... ]` block, or append when absent.
  python3 - "$TFVARS" "$VAR" "$BLOCK" <<'PY'
import re, sys
path, var, block = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
rx = re.compile(r"^[ \t]*" + re.escape(var) + r"[ \t]*=[ \t]*\[.*?^[ \t]*\][ \t]*$", re.S | re.M)
hits = rx.findall(text)
if len(hits) > 1:
    sys.exit(f"{var} appears {len(hits)} times in {path} — fix by hand")
if hits:
    text = rx.sub(lambda _m: block, text, count=1)
else:
    if not text.endswith("\n"):
        text += "\n"
    text += "\n" + block + "\n"
open(path, "w", encoding="utf-8").write(text)
PY
  GOT=$(read_list) || GOT=""
  if [ "$GOT" != "$WANT" ]; then
    cp "$BAK" "$TFVARS"
    echo "terraform does not read the new list back — tfvars restored from $BAK" >&2; exit 1
  fi
  echo "tfvars updated: $VAR now has $(wc -l <<<"$GOT") URLs (existing entries kept)"
fi

echo "--- terraform plan (targeted; nothing is applied) ---"
terraform -chdir="$TF_DIR" plan -no-color -target="$TARGET" 2>/dev/null \
  | grep -E '^[[:space:]]*# .* (will be|must be)|^Plan:' || true
cat <<EOT
expect exactly:
  # $TARGET will be updated in-place
  Plan: 0 to add, 1 to change, 0 to destroy.
anything else: stop. Otherwise, by hand:
  terraform -chdir=$TF_DIR apply -target=$TARGET
EOT
