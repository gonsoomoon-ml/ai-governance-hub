#!/usr/bin/env bash
# 10-cognito-demo-users.sh — two demo teams in the gateway's Cognito pool: one group per team
# (Claude_<dept>_<team>, the shape admin-api's group sync maps to department / team) and one user
# each, invited by e-mail (Cognito sends the temporary password; nothing secret is printed here).
# Idempotent: existing groups/users are kept, membership is re-applied.
# Env (deploy/demo/.env or exported): AWS_PROFILE, AWS_REGION, COGNITO_USER_POOL_ID,
#   DEMO_MEMBERS="Claude_engineering_platform=alice@example.com Claude_engineering_mobile=bob@example.com"
set -euo pipefail
cd "$(dirname "$0")"
if [ -f ./.env ]; then set -a; source ./.env; set +a; fi
: "${COGNITO_USER_POOL_ID:?}"; : "${DEMO_MEMBERS:?group=email pairs, space separated}"
for pair in $DEMO_MEMBERS; do
  group="${pair%%=*}"; email="${pair#*=}"
  if aws cognito-idp get-group --user-pool-id "$COGNITO_USER_POOL_ID" --group-name "$group" >/dev/null 2>&1; then
    echo "group exists: $group"
  else
    aws cognito-idp create-group --user-pool-id "$COGNITO_USER_POOL_ID" --group-name "$group" \
      --description "AI Governance Hub demo team" >/dev/null
    echo "group created: $group"
  fi
  if aws cognito-idp admin-get-user --user-pool-id "$COGNITO_USER_POOL_ID" --username "$email" >/dev/null 2>&1; then
    echo "user exists: $email"
  else
    aws cognito-idp admin-create-user --user-pool-id "$COGNITO_USER_POOL_ID" --username "$email" \
      --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
      --desired-delivery-mediums EMAIL >/dev/null
    echo "user created (invite e-mailed): $email"
  fi
  aws cognito-idp admin-add-user-to-group --user-pool-id "$COGNITO_USER_POOL_ID" --username "$email" --group-name "$group"
  echo "member: $email -> $group"
done
echo "status:"; for pair in $DEMO_MEMBERS; do email="${pair#*=}"
  aws cognito-idp admin-get-user --user-pool-id "$COGNITO_USER_POOL_ID" --username "$email" --query '[Username,UserStatus]' --output text | sed -E 's/^[0-9a-f-]{36}/<sub>/'
done
