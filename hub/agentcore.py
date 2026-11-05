"""AgentCore 쪽 허브 역할(덱 6장 (4b)): Agent Registry 레코드 읽기·승인, 게이트웨이 MCP 대상 보장.

- Registry: 레코드 요약을 나열하고 상세에서 도구 이름(descriptors.mcpServer.additionalData.tools.data 의
  JSON) 과 끝점(descriptors.mcpServer.source.fromUrl.url) 을 뽑는다. 승인·반려 = UpdateRegistryRecordStatus.
- Gateway: 허용된 에이전트마다 mcp.mcpServer 대상(끝점 = Runtime MCP URL, IAM 아웃바운드) 이 READY 인지
  보장한다(deploy/agentcore/20-gateway.py 의 ensure_target 을 라이브러리로 옮긴 것).
boto3 클라이언트는 인자로 받을 수 있어 테스트가 가짜를 끼운다. /v1/resolve 는 이 모듈을 쓰지 않는다.

The hub's AgentCore side (deck slide 6 (4b)): read and approve Agent Registry records, ensure gateway MCP targets.

- Registry: list record summaries, then pull tool names (JSON in descriptors.mcpServer.additionalData.tools.data)
  and the endpoint (descriptors.mcpServer.source.fromUrl.url) from the detail. Approve/reject =
  UpdateRegistryRecordStatus.
- Gateway: for each allowed agent make sure an mcp.mcpServer target (endpoint = Runtime MCP URL, IAM outbound)
  is READY (ensure_target from deploy/agentcore/20-gateway.py as a library).
boto3 clients can be injected, so tests use fakes. /v1/resolve never touches this module.
"""

from __future__ import annotations

import json
import time
from typing import Any

import boto3


class AgentCoreError(Exception):
    """AgentCore 호출 실패(설정 없음·대상이 READY 가 안 됨·레코드 없음).

    An AgentCore call failed (not configured, target never READY, record missing).
    """


class AgentCore:
    """Registry 하나 + Gateway 하나에 대한 클라이언트.

    A client for one registry and one gateway.
    """

    def __init__(self, settings: Any, registry_client: Any = None, gateway_client: Any = None, sleep=time.sleep):
        """설정의 리전·레지스트리 ID·게이트웨이 ID 로 클라이언트를 준비한다(주입 가능).

        Prepare clients from the settings' region, registry id and gateway id (injectable).
        """
        self.region = settings.aws_region
        self.registry_id = settings.agentcore_registry_id
        self.gateway_id = settings.agentcore_gateway_id
        self._ctl = registry_client or boto3.client("agent-registry-control", region_name=self.region)
        self._gw = gateway_client or boto3.client("bedrock-agentcore-control", region_name=self.region)
        self._sleep = sleep

    # --- registry ---

    def list_records(self) -> list[dict]:
        """레지스트리의 레코드 전부(상세 포함: 도구·끝점·상태), 이름순.

        Every record of the registry with detail (tools, endpoint, status), by name.
        """
        if not self.registry_id:
            raise AgentCoreError("AGENTCORE_REGISTRY_ID is not set")
        out = []
        token = None
        while True:
            kwargs: dict = {"registryId": self.registry_id, "maxResults": 50}
            if token:
                kwargs["nextToken"] = token
            resp = self._ctl.list_registry_records(**kwargs)
            for summary in resp.get("registryRecords", []):
                detail = self._ctl.get_registry_record(registryId=self.registry_id, recordId=summary["recordId"])
                out.append(parse_record(detail))
            token = resp.get("nextToken")
            if not token:
                return sorted(out, key=lambda r: r["name"])

    def approved_catalog(self) -> list[dict]:
        """APPROVED 레코드만, 허브 표의 카탈로그 모양으로.

        APPROVED records only, in the hub table's catalog shape.
        """
        return [
            {
                "name": r["name"],
                "kind": r["kind"],
                "tools": r["tools"],
                "status": r["status"],
                "endpoint": r["endpoint"],
                "description": r["description"],
            }
            for r in self.list_records()
            if r["status"] == "APPROVED"
        ]

    def set_record_status(self, name: str, status: str, reason: str) -> None:
        """레코드 상태 변경(APPROVED / REJECTED) — 문서의 curator 단계.

        Change a record's status (APPROVED / REJECTED) — the docs' curator step.
        """
        record = next((r for r in self.list_records() if r["name"] == name), None)
        if record is None:
            raise AgentCoreError(f"registry record {name} not found")
        self._ctl.update_registry_record_status(
            registryId=self.registry_id, recordId=record["record_id"], status=status, statusReason=reason[:200]
        )

    # --- gateway targets ---

    def ensure_targets_for(self, agents: list[str], catalog: list[dict]) -> list[str]:
        """허용된 에이전트마다 대상을 보장한다. 카탈로그에 끝점이 없는 이름은 건너뛴다. 보장한 이름 목록.

        Ensure a target per allowed agent; names without an endpoint in the catalog are skipped. Returns the names ensured.
        """
        endpoints = {a["name"]: a.get("endpoint") for a in catalog}
        ensured = []
        for name in agents:
            url = endpoints.get(name)
            if url:
                self.ensure_target(name, url)
                ensured.append(name)
        return ensured

    def ensure_target(self, name: str, mcp_url: str, attempts: int = 3) -> None:
        """mcp.mcpServer 대상이 같은 끝점으로 READY 가 되게 한다(없으면 생성, FAILED·끝점 변경이면 지우고 재생성).

        Make the mcp.mcpServer target READY with this endpoint (create if missing; delete and recreate when FAILED
        or the endpoint changed).
        """
        if not self.gateway_id:
            raise AgentCoreError("AGENTCORE_GATEWAY_ID is not set")
        last = ""
        for _ in range(attempts):
            existing = {t["name"]: t for t in self._targets()}
            if name in existing:
                current = self._gw.get_gateway_target(
                    gatewayIdentifier=self.gateway_id, targetId=existing[name]["targetId"]
                )
                same_url = _target_endpoint(current) == mcp_url
                if current["status"] == "READY" and same_url:
                    return
                self._gw.delete_gateway_target(gatewayIdentifier=self.gateway_id, targetId=existing[name]["targetId"])
                for _ in range(20):
                    if existing[name]["targetId"] not in {t["targetId"] for t in self._targets()}:
                        break
                    self._sleep(3)
                self._sleep(5 if not same_url else 45)  # FAILED with the same endpoint = IAM propagation
            created = self._gw.create_gateway_target(
                gatewayIdentifier=self.gateway_id,
                name=name,
                description=f"{name} MCP server on AgentCore Runtime",
                targetConfiguration={"mcp": {"mcpServer": {"endpoint": mcp_url}}},
                credentialProviderConfigurations=[
                    {
                        "credentialProviderType": "GATEWAY_IAM_ROLE",
                        "credentialProvider": {
                            "iamCredentialProvider": {"service": "bedrock-agentcore", "region": self.region}
                        },
                    }
                ],
            )
            for _ in range(24):
                state = self._gw.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=created["targetId"])
                last = f"{state['status']}: {state.get('statusReasons')}"
                if state["status"] == "READY":
                    return
                if state["status"].endswith("FAILED"):
                    break
                self._sleep(5)
        raise AgentCoreError(f"gateway target {name} did not reach READY ({last})")

    def _targets(self) -> list[dict]:
        """게이트웨이의 대상 목록.

        The gateway's target list.
        """
        return self._gw.list_gateway_targets(gatewayIdentifier=self.gateway_id).get("items", [])


def parse_record(detail: dict) -> dict:
    """GetRegistryRecord 응답 -> {name, kind, description, status, tools, endpoint, record_id, version}.

    GetRegistryRecord response -> {name, kind, description, status, tools, endpoint, record_id, version}.
    """
    mcp = (detail.get("descriptors") or {}).get("mcpServer") or {}
    tools: list[str] = []
    raw = ((mcp.get("additionalData") or {}).get("tools") or {}).get("data")
    if raw:
        try:
            tools = [t["name"] for t in json.loads(raw).get("tools", []) if isinstance(t, dict) and t.get("name")]
        except (ValueError, AttributeError):
            tools = []
    return {
        "name": detail.get("name", ""),
        "kind": detail.get("recordType", ""),
        "description": detail.get("description", ""),
        "status": detail.get("status", ""),
        "tools": tools,
        "endpoint": (((mcp.get("source") or {}).get("fromUrl") or {}).get("url")) or "",
        "record_id": detail.get("recordId", ""),
        "version": detail.get("recordVersion", ""),
    }


def _target_endpoint(target: dict) -> str:
    """대상의 MCP 끝점 URL.

    The target's MCP endpoint URL.
    """
    return (((target.get("targetConfiguration") or {}).get("mcp") or {}).get("mcpServer") or {}).get("endpoint") or ""
