#!/usr/bin/env python3
"""40-registry.py — AWS Agent Registry for the demo: registry + records synced from the Runtime MCP endpoints.

Usage (order: `role` -> 30-restrict-runtime.py (allows gateway + sync roles) -> `ensure`):
  uv run deploy/agentcore/40-registry.py role                # IAM role the registry assumes for endpoint sync
  uv run deploy/agentcore/40-registry.py ensure              # role + registry + records (sync from endpoint, IAM) + submit
  uv run deploy/agentcore/40-registry.py list                # records with status (what the Hub reads)
  uv run deploy/agentcore/40-registry.py approve <record-name> [reason]   # curator step (Hub does the same call)
  uv run deploy/agentcore/40-registry.py teardown            # delete records + registry

Reduced from aiops-multi-agent-workshop docs/research/poc/agent_registry/main.py (create -> publish ->
submit -> approve -> search -> cleanup) and moved to the GA `agent-registry-control` namespace.
Docs: devguide registry-sync-records ("Synchronize from an IAM-protected MCP server": iamCredentialProvider
{roleArn, service} — role must allow bedrock-agentcore:InvokeAgentRuntime; caller needs iam:PassRole with
iam:PassedToService=bedrock-agentcore.amazonaws.com), registry-supported-record-types (AGENT may use the
mcpServer descriptor), registry-record-lifecycle (DRAFT -> PENDING_APPROVAL -> APPROVED).
Records: api-catalog (MCP) and dev-guide-qa (AGENT) — both mcpServer descriptor, source.fromUrl = Runtime MCP URL.
Sync role (2026-11-03 finding): the gateway role (trust = bedrock-agentcore.amazonaws.com) is NOT assumable by the
registry — CREATE_FAILED "Unable to assume the provided IAM role ... trust policy allows the Registry service principal
... aws:SourceAccount and aws:SourceArn". The devguide does not spell the principal out; the service-linked-role page
names agent-registry.amazonaws.com, and the PassRole doc says AgentCore Identity (bedrock-agentcore.amazonaws.com)
does the hand-off, so the dedicated sync role trusts both, limited to this account. Verified 2026-11-03: both
records reach DRAFT with tools synced (recordVersion is overwritten by the server's reported version, e.g. 1.30.0).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
load_dotenv(ENV_FILE, override=True)
REGION = os.environ.get("AWS_REGION", "us-west-2")
PREFIX = os.environ.get("DEMO_PREFIX", "ai-governance-demo")
REGISTRY_NAME = f"{PREFIX}-registry"
SYNC_ROLE = os.environ.get("REGISTRY_SYNC_ROLE_ARN")
SYNC_ROLE_NAME = f"{PREFIX}-registry-sync-role"
# SigV4 signing service for the Runtime MCP endpoint. The devguide (registry-sync-records, API section) says
# "agent-registry" for Runtime/Gateway, but that value failed with CREATE_FAILED "MCP server returned HTTP 403"
# (2026-11-03); "bedrock-agentcore" (the Runtime's own signing name, also the console example) synced fine.
SYNC_SERVICE = os.environ.get("REGISTRY_SYNC_SERVICE", "bedrock-agentcore")
RECORDS = {  # name -> (recordType, displayName, description, env key of Runtime MCP URL, version)
    "api-catalog": ("MCP", "API Catalog", "In-house API catalog: list/search internal APIs, get OpenAPI specs and request examples.",
                    "API_CATALOG_RUNTIME_MCP_URL", "1.0.0"),
    "dev-guide-qa": ("AGENT", "Dev Guide Q&A Agent", "In-house agent: answers questions about the developer guide and checks code/diffs against it.",
                     "DEV_GUIDE_QA_RUNTIME_MCP_URL", "1.0.0"),
}
ctl = boto3.client("agent-registry-control", region_name=REGION)
iam = boto3.client("iam")


def ensure_sync_role() -> str:
    """레지스트리가 (AgentCore Identity 를 거쳐) 맡아 Runtime MCP 끝점에 tools/list 를 SigV4 서명으로 보내는 역할을 만들거나 갱신한다.

    Create or update the role the registry assumes (via AgentCore Identity) to SigV4-sign tools/list against the Runtime MCP endpoints.
    """
    account = boto3.client("sts").get_caller_identity()["Account"]
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
        "Principal": {"Service": ["agent-registry.amazonaws.com", "bedrock-agentcore.amazonaws.com"]},
        "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": account}}}]}
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
        "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"],
        "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{account}:runtime/*",
                     f"arn:aws:bedrock-agentcore:{REGION}:{account}:runtime/*/runtime-endpoint/*"]}]}
    try:
        arn = iam.get_role(RoleName=SYNC_ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=SYNC_ROLE_NAME, PolicyDocument=json.dumps(trust))
        print(f"sync role exists: {arn}")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=SYNC_ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="ai-governance-hub demo: Agent Registry endpoint sync (SigV4 to Runtime MCP)")["Role"]["Arn"]
        print(f"sync role created: {arn} (waiting 10s for IAM propagation)"); time.sleep(10)
    iam.put_role_policy(RoleName=SYNC_ROLE_NAME, PolicyName="invoke-runtimes", PolicyDocument=json.dumps(policy))
    save_env(REGISTRY_SYNC_ROLE_ARN=arn)
    return arn


def find_registry() -> dict | None:
    """이름이 같은 레지스트리를 찾아 상세를 돌려준다. 없으면 None.

    Find the registry with this name and return its details. None if absent.
    """
    for r in ctl.list_registries().get("registries", []):
        if r["name"] == REGISTRY_NAME:
            return ctl.get_registry(registryId=r["registryId"])
    return None


def ensure_registry() -> dict:
    """레지스트리가 없으면 만들고 READY 를 기다린다(최대 2분).

    Create the registry if it does not exist and wait for READY (up to 2 minutes).
    """
    r = find_registry()
    if r:
        print(f"registry exists: {r['registryId']} {r['status']}"); return r
    r = ctl.create_registry(name=REGISTRY_NAME, description="ai-governance-hub demo: in-house MCP servers and agents (manual approval)")
    rid = r["registryArn"].rsplit("/", 1)[-1]
    for _ in range(40):
        g = ctl.get_registry(registryId=rid)
        if g["status"] == "READY":
            print(f"registry created: {rid}"); return g
        time.sleep(3)
    sys.exit("registry not READY")


def records(rid: str) -> dict:
    """레지스트리의 레코드 전체를 {이름: 요약} 으로 돌려준다(페이지 넘김 포함).

    Return every record of the registry as {name: summary} (paginated).
    """
    out = {}
    token = None
    while True:
        kw = {"registryId": rid, "maxResults": 50}
        if token: kw["nextToken"] = token
        resp = ctl.list_registry_records(**kw)
        for rec in resp.get("registryRecords", []):
            out[rec["name"]] = rec
        token = resp.get("nextToken")
        if not token: return out


def ensure_record(rid: str, name: str) -> None:
    """레코드를 Runtime MCP 끝점에서 동기화(IAM)해 만들고 DRAFT 가 되면 승인 제출한다. 이름이 같은 레코드가 FAILED 이거나
    동기화 원본 URL 이 .env 와 다르면 지우고 다시 만든다; 그 외엔 그대로 둔다.

    Create the record by syncing from the Runtime MCP endpoint (IAM) and submit it for approval once it is DRAFT. A record
    with the same name is deleted and recreated when it is FAILED or its sync source URL differs from .env; otherwise it is
    left as is.
    """
    rtype, display, desc, key, version = RECORDS[name]
    url = os.environ.get(key)
    if not url:
        print(f"skip {name}: {key} not in .env"); return
    existing = records(rid).get(name)
    if existing:
        cur = ctl.get_registry_record(registryId=rid, recordId=existing["recordId"])
        cur_url = cur.get("descriptors", {}).get("mcpServer", {}).get("source", {}).get("fromUrl", {}).get("url")
        if cur["status"].endswith("FAILED") or cur_url != url:
            print(f"record {name} is {cur['status']}, endpoint {'same' if cur_url == url else 'changed'} -> delete and recreate")
            ctl.delete_registry_record(registryId=rid, recordId=existing["recordId"]); time.sleep(5)
        else:
            print(f"record exists: {name} {cur['status']}"); return
    resp = ctl.create_registry_record(
        registryId=rid, name=name, displayName=display, description=desc, recordType=rtype, recordVersion=version,
        descriptors={"mcpServer": {"source": {"fromUrl": {"url": url, "credentialProviderConfigurations": [
            {"credentialProviderType": "IAM", "credentialProvider": {"iamCredentialProvider": {"roleArn": SYNC_ROLE, "service": SYNC_SERVICE, "region": REGION}}}]}}}},
    )
    recid = resp["recordArn"].rsplit("/", 1)[-1]
    print(f"record creating: {name} {resp.get('status')}")
    for _ in range(60):
        g = ctl.get_registry_record(registryId=rid, recordId=recid)
        st = g["status"]
        if st == "DRAFT":
            tools = (g.get("descriptors", {}).get("mcpServer", {}).get("additionalData") or {})
            print(f"record DRAFT: {name} (synced from endpoint; additionalData keys={list(tools)})"); break
        if st.endswith("FAILED"):
            sys.exit(f"record {name} {st}: {g.get('statusReason')}")
        time.sleep(5)
    else:
        sys.exit(f"record {name} did not reach DRAFT within 5 minutes (last status {st})")
    ctl.submit_registry_record_for_approval(registryId=rid, recordId=recid)
    print(f"record submitted: {name} -> PENDING_APPROVAL")


def approve(rid: str, name: str, reason: str) -> None:
    """레코드를 APPROVED 로 바꾼다(큐레이터 단계; 허브가 같은 호출을 한다).

    Set the record to APPROVED (curator step; the Hub makes the same call).
    """
    rec = records(rid)[name]
    resp = ctl.update_registry_record_status(registryId=rid, recordId=rec["recordId"], status="APPROVED", statusReason=reason)
    print(f"approved: {name} -> {resp['status']}")


def teardown(rid: str | None) -> None:
    """레코드 전부와 레지스트리를 지우고, 동기화 역할(인라인 정책 포함)도 지운다. rid 가 None 이면 역할만.

    Delete every record and the registry, then the sync role (with its inline policy). With rid None, only the role.
    """
    if rid:
        for name, rec in records(rid).items():
            ctl.delete_registry_record(registryId=rid, recordId=rec["recordId"]); print(f"deleted record {name}")
        time.sleep(5); ctl.delete_registry(registryId=rid); print(f"deleted registry {rid}")
    try:
        iam.delete_role_policy(RoleName=SYNC_ROLE_NAME, PolicyName="invoke-runtimes")
        iam.delete_role(RoleName=SYNC_ROLE_NAME); print(f"deleted role {SYNC_ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        pass


def save_env(**kv) -> None:
    """주어진 키=값 을 deploy/agentcore/.env 에 쓴다(같은 키는 교체).

    Write the given key=value pairs into deploy/agentcore/.env (same keys replaced).
    """
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not any(l.startswith(k + "=") for k in kv)]
    ENV_FILE.write_text("\n".join(lines + [f"{k}={v}" for k, v in kv.items()]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "teardown":
        r = find_registry(); teardown(r["registryId"] if r else None); sys.exit()
    if cmd in ("role", "ensure"):
        SYNC_ROLE = ensure_sync_role()
        if cmd == "role":
            sys.exit()
    if cmd == "ensure" and not SYNC_ROLE:
        sys.exit("no REGISTRY_SYNC_ROLE_ARN; run `role` first")
    r = ensure_registry() if cmd == "ensure" else find_registry()
    if not r: sys.exit("no registry; run ensure")
    rid = r["registryId"]; save_env(REGISTRY_ID=rid, REGISTRY_ARN=r["registryArn"])
    if cmd == "ensure":
        for name in RECORDS: ensure_record(rid, name)
    elif cmd == "approve":
        approve(rid, sys.argv[2], " ".join(sys.argv[3:]) or "Approved in Governance Hub demo")
    for name, rec in records(rid).items():
        print(f"  {name:14} {rec.get('recordType', ''):6} v{rec.get('recordVersion', '')} {rec.get('status')}")
