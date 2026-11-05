#!/usr/bin/env python3
"""10-table-and-role.py — the hub's DynamoDB table and IRSA role (one account, one region).

Usage:  uv run deploy/hub/10-table-and-role.py ensure      # table (pk/sk, on-demand) + role + inline policy -> .env HUB_ROLE_ARN
        uv run deploy/hub/10-table-and-role.py teardown --yes   # delete role and table (data is lost)

IRSA: the role trusts the EKS cluster's OIDC provider for system:serviceaccount:<NAMESPACE>:<SERVICE_ACCOUNT>
(devguide: IAM roles for service accounts). Policy = this table only + (optional) the demo Agent Registry
(list/get/update records) and AgentCore Gateway (list/get/create/delete targets, PassRole of the gateway role).
Registry actions use the `agent-registry:` IAM prefix (new namespace, 2026-10); if the hub logs AccessDenied
on ListRegistryRecords, check the prefix in the service authorization reference and adjust POLICY below.
Prints the values lines for deploy/helm/governance-hub (serviceAccount.annotations role-arn, env.DDB_TABLE).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
load_dotenv(ENV_FILE, override=True)
REGION = os.environ.get("AWS_REGION", "us-west-2")
CLUSTER = os.environ.get("EKS_CLUSTER", "llm-gateway-dev")
NAMESPACE = os.environ.get("NAMESPACE", "llm-gateway")
SERVICE_ACCOUNT = os.environ.get("SERVICE_ACCOUNT", "governance-hub")
TABLE = os.environ.get("TABLE", "ai-governance-hub")
ROLE_NAME = os.environ.get("ROLE_NAME", "ai-governance-hub-role")
REGISTRY_ARN = os.environ.get("REGISTRY_ARN", "")
GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
GATEWAY_ROLE_ARN = os.environ.get("GATEWAY_ROLE_ARN", "")

ddb = boto3.client("dynamodb", region_name=REGION)
iam = boto3.client("iam")
eks = boto3.client("eks", region_name=REGION)
account = boto3.client("sts").get_caller_identity()["Account"]


def ensure_table() -> str:
    """표가 없으면 만들고(pk/sk, 온디맨드, 삭제 보호) ARN 을 돌려준다.

    Create the table if missing (pk/sk, on-demand, deletion protection) and return its ARN.
    """
    try:
        desc = ddb.describe_table(TableName=TABLE)["Table"]
        print(f"table exists: {TABLE} {desc['TableStatus']}")
        return desc["TableArn"]
    except ddb.exceptions.ResourceNotFoundException:
        pass
    ddb.create_table(
        TableName=TABLE, BillingMode="PAY_PER_REQUEST", DeletionProtectionEnabled=True,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        Tags=[{"Key": "project", "Value": "ai-governance-hub"}],
    )
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    arn = ddb.describe_table(TableName=TABLE)["Table"]["TableArn"]
    print(f"table created: {arn}")
    return arn


def oidc_provider_arn() -> tuple[str, str]:
    """클러스터의 OIDC 발급자 -> (provider ARN, 발급자 호스트/경로). 프로바이더가 IAM 에 없으면 멈춘다.

    The cluster's OIDC issuer -> (provider ARN, issuer host/path). Stops when the provider is not in IAM.
    """
    issuer = eks.describe_cluster(name=CLUSTER)["cluster"]["identity"]["oidc"]["issuer"]
    host_path = issuer.replace("https://", "")
    arn = f"arn:aws:iam::{account}:oidc-provider/{host_path}"
    known = {p["Arn"] for p in iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]}
    if arn not in known:
        sys.exit(f"OIDC provider {arn} is not registered in IAM (terraform irsa module should have created it)")
    return arn, host_path


def policy_document(table_arn: str) -> dict:
    """표 + (선택) 레지스트리·게이트웨이 대상 권한.

    Table + (optional) registry and gateway-target permissions.
    """
    statements = [{
        "Sid": "HubTable", "Effect": "Allow",
        "Action": ["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
                   "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem"],
        "Resource": [table_arn, f"{table_arn}/index/*"],
    }]
    if REGISTRY_ARN:
        statements.append({"Sid": "RegistryCurator", "Effect": "Allow",
                           "Action": ["agent-registry:ListRegistryRecords", "agent-registry:GetRegistryRecord",
                                      "agent-registry:UpdateRegistryRecordStatus", "agent-registry:GetRegistry"],
                           "Resource": [REGISTRY_ARN, f"{REGISTRY_ARN}/*"]})
    if GATEWAY_ID:
        gw_arn = f"arn:aws:bedrock-agentcore:{REGION}:{account}:gateway/{GATEWAY_ID}"
        statements.append({"Sid": "GatewayTargets", "Effect": "Allow",
                           "Action": ["bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGatewayTargets",
                                      "bedrock-agentcore:GetGatewayTarget", "bedrock-agentcore:CreateGatewayTarget",
                                      "bedrock-agentcore:DeleteGatewayTarget", "bedrock-agentcore:UpdateGatewayTarget"],
                           "Resource": [gw_arn, f"{gw_arn}/*"]})
        if GATEWAY_ROLE_ARN:
            statements.append({"Sid": "PassGatewayRole", "Effect": "Allow", "Action": "iam:PassRole",
                               "Resource": GATEWAY_ROLE_ARN,
                               "Condition": {"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}})
    return {"Version": "2012-10-17", "Statement": statements}


def ensure_role(table_arn: str) -> str:
    """IRSA 역할을 만들거나 신뢰 정책·인라인 정책을 갱신하고 ARN 을 돌려준다.

    Create the IRSA role or refresh its trust and inline policies; return the ARN.
    """
    provider_arn, host_path = oidc_provider_arn()
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Federated": provider_arn}, "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {f"{host_path}:sub": f"system:serviceaccount:{NAMESPACE}:{SERVICE_ACCOUNT}",
                                       f"{host_path}:aud": "sts.amazonaws.com"}}}]}
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust))
        print(f"role exists: {arn} (trust refreshed)")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=f"AI Governance Hub (IRSA {NAMESPACE}/{SERVICE_ACCOUNT}): hub table + registry + gateway targets",
                              Tags=[{"Key": "project", "Value": "ai-governance-hub"}])["Role"]["Arn"]
        print(f"role created: {arn}")
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="governance-hub", PolicyDocument=json.dumps(policy_document(table_arn)))
    return arn


def teardown() -> None:
    """역할(인라인 정책 포함)과 표를 지운다. --yes 가 없으면 멈춘다.

    Delete the role (with its inline policy) and the table. Stops without --yes.
    """
    if "--yes" not in sys.argv:
        sys.exit("teardown deletes the hub table and its data: re-run with --yes")
    try:
        iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName="governance-hub")
        iam.delete_role(RoleName=ROLE_NAME)
        print(f"deleted role {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        print(f"role {ROLE_NAME} already gone")
    try:
        ddb.update_table(TableName=TABLE, DeletionProtectionEnabled=False)
        ddb.delete_table(TableName=TABLE)
        print(f"deleted table {TABLE}")
    except ddb.exceptions.ResourceNotFoundException:
        print(f"table {TABLE} already gone")
    except ClientError as exc:
        sys.exit(f"table delete failed: {exc}")


def save_env(**kv: str) -> None:
    """키를 .env 에 쓰거나 바꾼다.

    Write or replace keys in .env.
    """
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not any(line.startswith(k + "=") for k in kv)]
    ENV_FILE.write_text("\n".join(lines + [f"{k}={v}" for k, v in kv.items()]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ensure"
    if cmd == "teardown":
        teardown()
        sys.exit()
    if cmd != "ensure":
        sys.exit(__doc__)
    table_arn = ensure_table()
    role_arn = ensure_role(table_arn)
    save_env(HUB_ROLE_ARN=role_arn, HUB_TABLE_ARN=table_arn)
    print("\nhelm values (deploy host copy of deploy/helm/governance-hub/values.yaml):")
    print(f"  serviceAccount.annotations.eks.amazonaws.com/role-arn: {role_arn}")
    print(f"  env.DDB_TABLE: {TABLE}    env.AWS_REGION: {REGION}")
