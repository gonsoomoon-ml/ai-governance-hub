#!/usr/bin/env bash
# teardown.sh — delete demo AgentCore resources in reverse order (targets -> gateway -> runtimes -> roles/logs
# -> code bucket -> registry -> gateway role -> Cognito client). Mirrors aiops-multi-agent-workshop teardown.sh patterns.
set -uo pipefail
cd "$(dirname "$0")"; set -a; source ./.env; set +a
R="${AWS_REGION:-us-west-2}"; P="${DEMO_PREFIX:-ai-governance-demo}"
echo "== gateway targets + gateway"
GW=$(aws bedrock-agentcore-control list-gateways --region "$R" --query "items[?name=='${P}-gateway'].gatewayId" --output text)
if [[ -n "$GW" && "$GW" != "None" ]]; then
  for T in $(aws bedrock-agentcore-control list-gateway-targets --region "$R" --gateway-identifier "$GW" --query 'items[].targetId' --output text); do
    aws bedrock-agentcore-control delete-gateway-target --region "$R" --gateway-identifier "$GW" --target-id "$T" >/dev/null && echo "  target $T deleted"; done
  sleep 5; aws bedrock-agentcore-control delete-gateway --region "$R" --gateway-identifier "$GW" >/dev/null && echo "  gateway $GW deleted"
fi
echo "== runtimes (execution roles, log groups, ECR repos follow)"
for NAME in api_catalog dev_guide_qa; do
  ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$R" --query "agentRuntimes[?agentRuntimeName=='${NAME}'].agentRuntimeId" --output text)
  if [[ -n "$ID" && "$ID" != "None" ]]; then
    ROLE=$(aws bedrock-agentcore-control get-agent-runtime --region "$R" --agent-runtime-id "$ID" --query roleArn --output text)
    aws bedrock-agentcore-control delete-agent-runtime --region "$R" --agent-runtime-id "$ID" >/dev/null && echo "  runtime $NAME ($ID) deleted"
    aws ecr delete-repository --region "$R" --repository-name "bedrock-agentcore-${NAME}" --force >/dev/null 2>&1 && echo "  ecr bedrock-agentcore-${NAME} deleted"
    RN="${ROLE##*/}"; for PN in $(aws iam list-role-policies --role-name "$RN" --query PolicyNames --output text 2>/dev/null); do aws iam delete-role-policy --role-name "$RN" --policy-name "$PN"; done
    aws iam delete-role --role-name "$RN" 2>/dev/null && echo "  role $RN deleted"
    for LG in $(aws logs describe-log-groups --region "$R" --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/${NAME}" --query 'logGroups[].logGroupName' --output text); do aws logs delete-log-group --region "$R" --log-group-name "$LG"; done
  fi
done
echo "== code bucket (direct code deployment packages)"
ACCT=$(aws sts get-caller-identity --query Account --output text); B="${P}-code-${ACCT}-${R}"
if aws s3api head-bucket --bucket "$B" 2>/dev/null; then aws s3 rm "s3://$B" --recursive --quiet; aws s3api delete-bucket --bucket "$B" && echo "  bucket $B deleted"; fi
echo "== agent registry (records, registry, sync role)"
uv run ./40-registry.py teardown 2>&1 | sed 's/^/  /'
echo "== gateway role"
aws iam delete-role-policy --role-name "${P}-gateway-role" --policy-name invoke-runtimes 2>/dev/null; aws iam delete-role --role-name "${P}-gateway-role" 2>/dev/null && echo "  role deleted"
echo "== cognito client"
CID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$COGNITO_USER_POOL_ID" --max-results 60 --query "UserPoolClients[?ClientName=='${P}-claude-code-mcp'].ClientId" --output text)
[[ -n "$CID" && "$CID" != "None" ]] && aws cognito-idp delete-user-pool-client --user-pool-id "$COGNITO_USER_POOL_ID" --client-id "$CID" && echo "  client deleted"
echo "== done"
