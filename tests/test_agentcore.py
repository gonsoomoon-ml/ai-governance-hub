"""AgentCore 쪽: 레코드 파싱·나열·승인, 게이트웨이 대상 보장(가짜 boto3 클라이언트).

The AgentCore side: record parsing, listing, approval and gateway-target ensuring (fake boto3 clients).
"""

from __future__ import annotations

import json

import pytest

from hub.agentcore import AgentCore, AgentCoreError, parse_record

ENDPOINT = "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aexample/invocations?qualifier=DEFAULT"


def record(name, status="PENDING_APPROVAL", kind="MCP", tools=("list_apis", "search_api"), endpoint=ENDPOINT):
    """실측한 GetRegistryRecord 모양의 레코드.

    A record in the GetRegistryRecord shape observed live.
    """
    return {
        "recordId": "rec-" + name,
        "name": name,
        "displayName": name,
        "description": f"{name} description",
        "recordType": kind,
        "recordVersion": "1.30.0",
        "status": status,
        "descriptors": {
            "mcpServer": {
                "data": "{}",
                "dataSchemaVersion": "2025-12-11",
                "additionalData": {
                    "tools": {
                        "data": json.dumps({"tools": [{"name": t, "description": t} for t in tools]}),
                        "dataSchemaVersion": "2025-11-25",
                    }
                },
                "source": {"fromUrl": {"url": endpoint, "credentialProviderConfigurations": []}},
            }
        },
    }


class FakeRegistry:
    """list/get/update 세 호출만 흉내.

    Imitates only list, get and update.
    """

    def __init__(self, records):
        """레코드 목록으로 시작.

        Start with a record list.
        """
        self.records = {r["recordId"]: r for r in records}
        self.updates = []

    def list_registry_records(self, **kw):
        """요약 목록(한 페이지).

        Summary list (one page).
        """
        return {
            "registryRecords": [
                {"recordId": r["recordId"], "name": r["name"], "status": r["status"]} for r in self.records.values()
            ]
        }

    def get_registry_record(self, registryId, recordId):
        """상세.

        Detail.
        """
        return dict(self.records[recordId])

    def update_registry_record_status(self, registryId, recordId, status, statusReason):
        """상태 변경을 기록하고 반영.

        Record and apply the status change.
        """
        self.updates.append((recordId, status, statusReason))
        self.records[recordId]["status"] = status
        return {"status": status}


class FakeGateway:
    """대상 목록·생성·삭제·조회 흉내. 생성하면 바로 READY.

    Imitates target list, create, delete and get. Created targets are READY at once.
    """

    def __init__(self, targets=()):
        """초기 대상: (name, status, endpoint).

        Initial targets: (name, status, endpoint).
        """
        self.targets = {
            f"t-{n}": {
                "targetId": f"t-{n}",
                "name": n,
                "status": s,
                "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": e}}},
            }
            for n, s, e in targets
        }
        self.created, self.deleted = [], []

    def list_gateway_targets(self, gatewayIdentifier):
        """목록.

        List.
        """
        return {
            "items": [
                {"targetId": t["targetId"], "name": t["name"], "status": t["status"]} for t in self.targets.values()
            ]
        }

    def get_gateway_target(self, gatewayIdentifier, targetId):
        """조회.

        Get.
        """
        return dict(self.targets[targetId])

    def delete_gateway_target(self, gatewayIdentifier, targetId):
        """삭제.

        Delete.
        """
        self.deleted.append(self.targets.pop(targetId)["name"])
        return {}

    def create_gateway_target(
        self, gatewayIdentifier, name, description, targetConfiguration, credentialProviderConfigurations
    ):
        """생성(READY).

        Create (READY).
        """
        tid = f"t-{name}-{len(self.created)}"
        self.targets[tid] = {
            "targetId": tid,
            "name": name,
            "status": "READY",
            "targetConfiguration": targetConfiguration,
        }
        self.created.append(
            (
                name,
                targetConfiguration["mcp"]["mcpServer"]["endpoint"],
                credentialProviderConfigurations[0]["credentialProviderType"],
            )
        )
        return {"targetId": tid, "status": "CREATING"}


@pytest.fixture
def ac(settings):
    """가짜 클라이언트를 끼운 AgentCore.

    AgentCore with fake clients.
    """
    settings.agentcore_registry_id = "reg-1"
    settings.agentcore_gateway_id = "gw-1"
    registry = FakeRegistry(
        [record("api-catalog"), record("dev-guide-qa", status="APPROVED", kind="AGENT", tools=("ask",))]
    )
    gateway = FakeGateway([("dev-guide-qa", "READY", ENDPOINT)])
    core = AgentCore(settings, registry_client=registry, gateway_client=gateway, sleep=lambda s: None)
    return core, registry, gateway


def test_parse_record_extracts_tools_and_endpoint():
    """도구 이름은 additionalData.tools.data 의 JSON 에서, 끝점은 source.fromUrl.url 에서.

    Tool names come from the JSON in additionalData.tools.data, the endpoint from source.fromUrl.url.
    """
    parsed = parse_record(record("api-catalog"))

    assert parsed["tools"] == ["list_apis", "search_api"]
    assert parsed["endpoint"] == ENDPOINT
    assert (
        parsed["kind"] == "MCP" and parsed["status"] == "PENDING_APPROVAL" and parsed["record_id"] == "rec-api-catalog"
    )


def test_parse_record_tolerates_a_record_without_tools():
    """도구 데이터가 없는 레코드는 빈 목록.

    A record without tool data gives an empty list.
    """
    detail = record("x")
    detail["descriptors"]["mcpServer"].pop("additionalData")

    assert parse_record(detail)["tools"] == []


def test_list_records_and_approved_catalog(ac):
    """전체 나열은 이름순, 카탈로그는 APPROVED 만.

    Listing is by name; the catalog holds APPROVED only.
    """
    core, _, _ = ac

    assert [r["name"] for r in core.list_records()] == ["api-catalog", "dev-guide-qa"]
    catalog = core.approved_catalog()
    assert [c["name"] for c in catalog] == ["dev-guide-qa"]
    assert catalog[0]["tools"] == ["ask"] and catalog[0]["endpoint"] == ENDPOINT


def test_set_record_status_uses_the_record_id(ac):
    """승인은 이름으로 찾은 recordId 로 UpdateRegistryRecordStatus.

    Approval calls UpdateRegistryRecordStatus with the recordId found by name.
    """
    core, registry, _ = ac

    core.set_record_status("api-catalog", "APPROVED", "ok")

    assert registry.updates == [("rec-api-catalog", "APPROVED", "ok")]
    assert [c["name"] for c in core.approved_catalog()] == ["api-catalog", "dev-guide-qa"]
    with pytest.raises(AgentCoreError):
        core.set_record_status("nope", "APPROVED", "ok")


def test_ensure_target_leaves_a_ready_target_with_the_same_endpoint_alone(ac):
    """READY + 같은 끝점이면 아무것도 하지 않는다.

    READY with the same endpoint: nothing happens.
    """
    core, _, gateway = ac

    core.ensure_target("dev-guide-qa", ENDPOINT)

    assert gateway.created == [] and gateway.deleted == []


def test_ensure_target_creates_a_missing_target_with_iam_outbound(ac):
    """없는 대상은 mcp.mcpServer + GATEWAY_IAM_ROLE 로 만든다.

    A missing target is created as mcp.mcpServer with GATEWAY_IAM_ROLE.
    """
    core, _, gateway = ac

    core.ensure_target("api-catalog", ENDPOINT)

    assert gateway.created == [("api-catalog", ENDPOINT, "GATEWAY_IAM_ROLE")]


def test_ensure_target_recreates_when_the_endpoint_changed(ac):
    """끝점이 바뀌면 지우고 다시 만든다.

    A changed endpoint means delete and recreate.
    """
    core, _, gateway = ac

    core.ensure_target("dev-guide-qa", ENDPOINT + "&v=2")

    assert gateway.deleted == ["dev-guide-qa"] and gateway.created[0][1] == ENDPOINT + "&v=2"


def test_ensure_targets_for_skips_agents_without_an_endpoint(ac):
    """카탈로그에 끝점이 없는 이름은 건너뛴다.

    Names without an endpoint in the catalog are skipped.
    """
    core, _, gateway = ac
    catalog = [{"name": "api-catalog", "endpoint": ENDPOINT}, {"name": "ghost", "endpoint": ""}]

    assert core.ensure_targets_for(["api-catalog", "ghost", "unknown"], catalog) == ["api-catalog"]
    assert [c[0] for c in gateway.created] == ["api-catalog"]
