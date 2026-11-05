#!/usr/bin/env python3
"""20-gateway.py — AgentCore Gateway (MCP, Cognito JWT inbound, MCP sessions) + MCP-server targets on Runtime.

Usage:  uv run deploy/agentcore/20-gateway.py            # create/ensure gateway + targets from .env
        uv run deploy/agentcore/20-gateway.py --target api_catalog   # (re)create one target only

Copied and reduced from aiops-multi-agent-workshop infra/cognito-gateway/setup_gateway.py (boto3,
idempotent by name, wait READY before adding targets). Differences:
  - inbound: CUSTOM_JWT with allowedClients = the Claude Code public client (user access tokens)
  - protocolConfiguration.mcp.sessionConfiguration: gateway MCP sessions (devguide gateway-sessions)
  - targets: mcp.mcpServer endpoint = Runtime MCP URL, credential GATEWAY_IAM_ROLE
    iamCredentialProvider{service: bedrock-agentcore} (devguide gateway-add-target-api-target-config)
  - gateway execution role created here (trust bedrock-agentcore, bedrock-agentcore:InvokeAgentRuntime*)
Writes GATEWAY_ID / GATEWAY_URL / GATEWAY_ROLE_ARN into deploy/agentcore/.env.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
load_dotenv(ENV_FILE, override=True)
REGION = os.environ.get("AWS_REGION", "us-west-2")
PREFIX = os.environ.get("DEMO_PREFIX", "ai-governance-demo")
GATEWAY_NAME = f"{PREFIX}-gateway"
ROLE_NAME = f"{PREFIX}-gateway-role"
POOL = os.environ["COGNITO_USER_POOL_ID"]
CLIENT_ID = os.environ["CLAUDE_CODE_MCP_CLIENT_ID"]
DISCOVERY = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}/.well-known/openid-configuration"
# targets: name -> env key holding the Runtime MCP URL (deploy_runtime.py writes <AGENT>_RUNTIME_MCP_URL)
TARGETS = {"api-catalog": "API_CATALOG_RUNTIME_MCP_URL", "dev-guide-qa": "DEV_GUIDE_QA_RUNTIME_MCP_URL"}

iam = boto3.client("iam")
gw = boto3.client("bedrock-agentcore-control", region_name=REGION)
account = boto3.client("sts").get_caller_identity()["Account"]


def ensure_role() -> str:
    """게이트웨이 실행 역할을 만들거나 갱신한다(신뢰 = bedrock-agentcore.amazonaws.com + 이 계정, 권한 = 이 계정 Runtime 전체
    InvokeAgentRuntime*). ARN 을 돌려준다.

    Create or update the gateway execution role (trust = bedrock-agentcore.amazonaws.com + this account, permission =
    InvokeAgentRuntime* on every Runtime in this account). Returns the ARN.
    """
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
             "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": account}}}]}
    policy = {"Version": "2012-10-17", "Statement": [{"Sid": "InvokeRuntimes", "Effect": "Allow",
              "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"],
              "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{account}:runtime/*"]}]}
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]; print(f"role exists: {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="AgentCore Gateway execution role (ai-governance-hub demo)")["Role"]["Arn"]
        print(f"role created: {arn}"); time.sleep(10)  # IAM propagation
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="invoke-runtimes", PolicyDocument=json.dumps(policy))
    return arn


def find_gateway() -> dict | None:
    """이름이 같은 게이트웨이를 찾아 상세를 돌려준다. 없으면 None.

    Find the gateway with this name and return its details. None if absent.
    """
    for item in gw.list_gateways().get("items", []):
        if item["name"] == GATEWAY_NAME:
            return gw.get_gateway(gatewayIdentifier=item["gatewayId"])
    return None


def ensure_gateway(role_arn: str) -> dict:
    """게이트웨이가 없으면 만든다: MCP 프로토콜, MCP 세션(1시간), SEMANTIC 도구 검색, 인바운드 = Cognito JWT(allowedClients =
    Claude Code 클라이언트).

    Create the gateway if it does not exist: MCP protocol, MCP sessions (1 hour), SEMANTIC tool search, inbound = Cognito JWT
    (allowedClients = the Claude Code client).
    """
    g = find_gateway()
    if g:
        print(f"gateway exists: {g['gatewayId']} {g['status']}"); return g
    g = gw.create_gateway(
        name=GATEWAY_NAME, description="ai-governance-hub demo: in-house tools for Claude Code",
        roleArn=role_arn, protocolType="MCP",
        protocolConfiguration={"mcp": {"sessionConfiguration": {"sessionTimeoutInSeconds": 3600}, "searchType": "SEMANTIC"}},
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={"customJWTAuthorizer": {"discoveryUrl": DISCOVERY, "allowedClients": [CLIENT_ID]}},
    )
    print(f"gateway created: {g['gatewayId']}")
    return g


def wait_ready(gateway_id: str) -> dict:
    """게이트웨이가 READY 가 될 때까지 최대 2분 기다린다. FAILED 나 시간 초과면 종료.

    Wait up to 2 minutes for the gateway to become READY. Exit on FAILED or timeout.
    """
    for _ in range(40):
        g = gw.get_gateway(gatewayIdentifier=gateway_id)
        if g["status"] == "READY":
            return g
        if g["status"].endswith("FAILED"):
            sys.exit(f"gateway {g['status']}: {g.get('statusReasons')}")
        time.sleep(3)
    sys.exit("gateway not READY in time")


def ensure_target(gateway_id: str, name: str, mcp_url: str) -> None:
    """mcp.mcpServer 대상을 만들고 READY 를 기다린다. 게이트웨이 역할을 막 만든 직후의 첫 시도는
    "Authorization error when sending message" 로 실패할 수 있다(IAM 전파, 2026-11-03 실측);
    FAILED 대상은 지우고 잠시 쉰 뒤 다시 만들며, 최대 3회 시도한다.

    Create the mcp.mcpServer target and wait for READY. The first attempt right after the gateway role
    is created can fail with "Authorization error when sending message" (IAM propagation, seen 2026-11-03);
    a FAILED target is deleted and recreated after a pause, up to 3 attempts.
    """
    for attempt in range(1, 4):
        existing = {t["name"]: t for t in gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])}
        if name in existing:
            cur = gw.get_gateway_target(gatewayIdentifier=gateway_id, targetId=existing[name]["targetId"])
            same_url = cur.get("targetConfiguration", {}).get("mcp", {}).get("mcpServer", {}).get("endpoint") == mcp_url
            if cur["status"] == "READY" and same_url:
                print(f"target exists: {name} READY"); return
            print(f"target {name} is {cur['status']}, endpoint {'same' if same_url else 'changed'} -> delete and recreate")
            gw.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=existing[name]["targetId"])
            for _ in range(20):
                ids = [t["targetId"] for t in gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])]
                if existing[name]["targetId"] not in ids:
                    break
                time.sleep(3)
            if not same_url:
                time.sleep(5)
            else:
                time.sleep(45)  # FAILED with same endpoint = IAM propagation; give it time
        t = gw.create_gateway_target(
            gatewayIdentifier=gateway_id, name=name, description=f"{name} MCP server on AgentCore Runtime",
            targetConfiguration={"mcp": {"mcpServer": {"endpoint": mcp_url}}},
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE",
                "credentialProvider": {"iamCredentialProvider": {"service": "bedrock-agentcore", "region": REGION}}}],
        )
        print(f"target created: {name} {t['status']} (attempt {attempt})")
        status, reasons = None, None
        for _ in range(40):
            s = gw.get_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            status, reasons = s["status"], s.get("statusReasons")
            if status == "READY":
                print(f"target READY: {name}"); return
            if status.endswith("FAILED"):
                break
            time.sleep(5)
        print(f"target {name} {status}: {reasons}")
    sys.exit(f"target {name} did not reach READY after 3 attempts")


def save_env(**kv) -> None:
    """주어진 키=값 을 deploy/agentcore/.env 에 쓴다(같은 키는 교체).

    Write the given key=value pairs into deploy/agentcore/.env (same keys replaced).
    """
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not any(l.startswith(k + "=") for k in kv)]
    lines += [f"{k}={v}" for k, v in kv.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--target") + 1] if "--target" in sys.argv else None
    role_arn = ensure_role()
    g = wait_ready(ensure_gateway(role_arn)["gatewayId"])
    save_env(GATEWAY_ID=g["gatewayId"], GATEWAY_URL=g["gatewayUrl"], GATEWAY_ROLE_ARN=role_arn)
    for name, key in TARGETS.items():
        if only and name.replace("-", "_") != only.replace("-", "_"):
            continue
        url = os.environ.get(key)
        if not url:
            print(f"skip {name}: {key} not in .env"); continue
        ensure_target(g["gatewayId"], name, url)
    print(f"GATEWAY_URL={g['gatewayUrl']}")
