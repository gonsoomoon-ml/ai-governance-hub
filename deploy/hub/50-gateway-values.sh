#!/usr/bin/env bash
# 50-gateway-values.sh — point the LLM gateway's gateway-proxy at the hub: in the deploy host's
# gateway values copy set gatewayProxy.image.tag (the image that carries the hub call) and
# gatewayProxy.env POLICY_HUB_URL / POLICY_HUB_TOKEN (the token 40-values.sh generated).
# Line-level edit with uniqueness guards (same approach as the gateway's 19-admin-login.sh), shown
# as a diff first; --apply writes it after a backup and proves the result by rendering the chart.
# Deploying is a separate step: install-eks.sh <env> (never `helm upgrade -f values` by hand — the
# gateway values keep placeholders that install-eks.sh fills from terraform output).
# Env: GATEWAY_IMAGE_TAG (e.g. 1.0.89-hub); optional GATEWAY_ROOT, DEPLOY_ENV, HUB_SERVICE_URL, NAMESPACE.
set -euo pipefail
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
: "${GATEWAY_IMAGE_TAG:?e.g. 1.0.89-hub}"
ROOT="${GATEWAY_ROOT:-$HOME/awsome-ai-gateway}"
ENV_NAME="${DEPLOY_ENV:-dev}"
NS="${NAMESPACE:-llm-gateway}"
V="$ROOT/deployment/charts/llm-gateway/values-eks-fargate-$ENV_NAME.yaml"
CHART="$ROOT/deployment/charts/llm-gateway"
SNAP="$HOME/hub-snapshots"; mkdir -p "$SNAP"
HUB_URL="${HUB_SERVICE_URL:-http://governance-hub.$NS.svc:8080}"
[ -f "$V" ] || { echo "values not found: $V" >&2; exit 1; }
[ -s "$SNAP/hub-token" ] || { echo "missing $SNAP/hub-token — run 40-values.sh first" >&2; exit 1; }
HUB_TOKEN=$(<"$SNAP/hub-token")
for c in helm python3 aws; do command -v "$c" >/dev/null || { echo "$c not found" >&2; exit 1; }; done
python3 -c "import yaml" 2>/dev/null || { echo "python3 yaml module not found (apt install python3-yaml)" >&2; exit 1; }
aws ecr describe-images --repository-name llm-gateway/gateway-proxy --image-ids imageTag="$GATEWAY_IMAGE_TAG" >/dev/null \
  || { echo "image tag $GATEWAY_IMAGE_TAG not in ECR llm-gateway/gateway-proxy" >&2; exit 1; }

TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
python3 - "$V" "$TMP" "$GATEWAY_IMAGE_TAG" "$HUB_URL" "$HUB_TOKEN" <<'PY'
import json, re, sys
src, dst, tag, url, token = sys.argv[1:]
L = open(src, encoding="utf-8").read().split("\n")
SEC = "gatewayProxy"

def ind(l): return len(l) - len(l.lstrip(" "))
def code(l): return l.strip() != "" and not l.lstrip().startswith("#")

def section():
    hits = [i for i, l in enumerate(L) if re.match(rf"^{SEC}:\s*(#.*)?$", l)]
    if len(hits) != 1:
        sys.exit(f"values: top-level '{SEC}:' found {len(hits)} times (need exactly 1)")
    s = hits[0]
    e = next((i for i in range(s + 1, len(L)) if code(L[i]) and ind(L[i]) == 0), len(L))
    return s, e

def block(name, s, e):  # the one `  name:` block map inside the section -> (start, end)
    hits = [i for i in range(s + 1, e) if code(L[i]) and ind(L[i]) == 2 and re.match(rf"^\s*{name}:\s*(#.*)?$", L[i])]
    if len(hits) != 1:
        sys.exit(f"values: '{SEC}.{name}:' block found {len(hits)} times (need exactly 1)")
    i = hits[0]; j = i + 1
    while j < e and (not code(L[j]) or ind(L[j]) > 2):
        j += 1
    return i, j

# 1. image.tag
s, e = section(); i, j = block("image", s, e)
tags = [k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) == 4 and re.match(r"^\s*tag:", L[k])]
if len(tags) != 1:
    sys.exit(f"values: {SEC}.image.tag found {len(tags)} times (need exactly 1)")
L[tags[0]] = f'    tag: "{tag}"'

# 2. env.POLICY_HUB_URL / POLICY_HUB_TOKEN
note_done = False
for key, val in (("POLICY_HUB_URL", url), ("POLICY_HUB_TOKEN", token)):
    s, e = section(); i, j = block("env", s, e)
    line = f"    {key}: {json.dumps(val)}"
    hits = [k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) == 4 and re.match(rf"^\s*{key}:", L[k])]
    if len(hits) > 1:
        sys.exit(f"values: {SEC}.env.{key} appears {len(hits)} times — fix the file first")
    if hits:
        L[hits[0]] = line; continue
    last = max([k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) >= 4], default=i)
    new = [line]
    if not note_done:
        new.insert(0, "    # AI Governance Hub (50-gateway-values.sh): /v1/policy asks the hub per request; unset = models fragment only")
        note_done = True
    L[last + 1:last + 1] = new
open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
if cmp -s "$V" "$TMP"; then echo "values already carry these — nothing to write"; exit 0; fi

# Proof on the candidate file: the rendered gateway-proxy Deployment has the tag and both env values.
R=$(helm template llm-gateway "$CHART" -n "$NS" -f "$TMP" 2>/dev/null \
  | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("gateway-proxy")]
c = docs[0]["spec"]["template"]["spec"]["containers"][0]
env = {e["name"]: e.get("value") for e in c.get("env", [])}
print(c["image"].rsplit(":", 1)[-1], env.get("POLICY_HUB_URL"), "token-set" if env.get("POLICY_HUB_TOKEN") else "token-missing")')
[ "$R" = "$GATEWAY_IMAGE_TAG $HUB_URL token-set" ] || { echo "rendered gateway-proxy does not carry the change: '$R' — $V untouched" >&2; exit 1; }

echo "--- planned change to $(basename "$V") (token redacted) ---"
diff -u "$V" "$TMP" | sed -n '3,$p' | sed -E 's/(POLICY_HUB_TOKEN: ")[^"]+/\1<redacted>/'
if [ "$APPLY" = 0 ]; then echo; echo "Nothing written. Apply:  GATEWAY_IMAGE_TAG=$GATEWAY_IMAGE_TAG bash $0 --apply"; exit 0; fi
BAK="$SNAP/$(date +%Y%m%d-%H%M%S)-values-$ENV_NAME-50-gateway.bak"
cp "$V" "$BAK" && cp "$TMP" "$V"
echo "values updated: $V (backup: $BAK — undo: cp $BAK $V, then install-eks.sh $ENV_NAME)"
echo "next: cd $ROOT && ./deployment/scripts/install-eks.sh $ENV_NAME"
