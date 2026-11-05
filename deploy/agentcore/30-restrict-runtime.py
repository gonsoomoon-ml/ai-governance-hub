#!/usr/bin/env python3
"""30-restrict-runtime.py — allow only the AgentCore Gateway role (and the Agent Registry sync role, if
REGISTRY_SYNC_ROLE_ARN is set — see 40-registry.py role) to invoke a Runtime (resource-based policy).

Usage:  uv run deploy/agentcore/30-restrict-runtime.py api_catalog [dev_guide_qa ...]
Docs: devguide runtime-security-best-practices "Front your runtime with an AgentCore Gateway" ->
"Restrict IAM (SigV4) inbound invocation to your gateway" (PutResourcePolicy on the runtime ARN).
Idempotent (PutResourcePolicy replaces the document). Remove with --delete.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env", override=True)
REGION = os.environ.get("AWS_REGION", "us-west-2")
ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)
gateway_role = os.environ["GATEWAY_ROLE_ARN"]
allowed = [gateway_role] + ([os.environ["REGISTRY_SYNC_ROLE_ARN"]] if os.environ.get("REGISTRY_SYNC_ROLE_ARN") else [])
# the gateway assumes its execution role; allow that role (and the account's admin principals are not
# listed on purpose: a resource policy is additive, so account principals already allowed by IAM are
# still allowed unless we add an explicit Deny — see the doc's trust-policy hardening note).
names = [a for a in sys.argv[1:] if not a.startswith("--")]
for name in names:
    arn = os.environ[f"{name.upper()}_RUNTIME_ARN"]
    if "--delete" in sys.argv:
        ctl.delete_resource_policy(resourceArn=arn); print(f"deleted policy on {name}"); continue
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "GatewayAndRegistryOnly", "Effect": "Allow", "Principal": {"AWS": allowed},
         "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"], "Resource": arn},
        {"Sid": "DenyOthers", "Effect": "Deny", "Principal": "*",
         "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"], "Resource": arn,
         "Condition": {"StringNotEquals": {"aws:PrincipalArn": allowed}}},
    ]}
    ctl.put_resource_policy(resourceArn=arn, policy=json.dumps(policy))
    print(f"policy set on {name}: only {allowed}")
    print(json.dumps(ctl.get_resource_policy(resourceArn=arn), default=str)[:300])
