"""DynamoDB 단일 표 저장소(moto).

The single-table DynamoDB store, exercised against moto.
"""

from __future__ import annotations

from conftest import OTHER_TEAM, TEAM, policy


def test_org_settings_default_when_absent(store):
    """조직 설정이 없으면 고정 항목 없음 + 빈 기본 정책을 돌려준다.

    Without org settings: no fixed locks and an empty default policy.
    """
    org = store.org_settings()

    assert org["locks_fixed"] == {"plugin_sources": None, "personal_customization": None, "mcp": None}
    assert org["default_policy"]["agents"] == []


def test_org_settings_round_trip(store):
    """조직 설정 저장·조회.

    Org settings round-trip.
    """
    store.put_org_settings(
        {
            "locks_fixed": {"plugin_sources": "company_only", "personal_customization": None, "mcp": None},
            "default_policy": policy(agents=[]),
        }
    )

    assert store.org_settings()["locks_fixed"]["plugin_sources"] == "company_only"
    assert store.org_settings()["default_policy"]["plugins"] == ["deck-design", "infographic-design"]


def test_teams_round_trip_and_removal(store):
    """관리할 팀 등록·목록·해제.

    Managed teams: register, list, remove.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    store.put_team({"team_id": OTHER_TEAM, "name": "mobile", "dept": "engineering", "leader_email": ""})

    assert [t["name"] for t in store.list_teams()] == ["mobile", "platform"]
    store.remove_team(OTHER_TEAM)
    assert [t["team_id"] for t in store.list_teams()] == [TEAM]
    assert store.get_team(OTHER_TEAM) is None


def test_draft_and_publish_keep_versions(store):
    """초안 저장 -> 발행 -> 발행본·버전 이력·초안 상태.

    Draft -> publish -> published copy, version history, draft status.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    assert store.get_policy(TEAM, "PUBLISHED") is None

    store.put_draft(TEAM, policy(), status="pending", submitted_by="lead@example.com")
    draft = store.get_policy(TEAM, "DRAFT")
    assert draft["status"] == "pending" and draft["submitted_by"] == "lead@example.com"

    v1 = store.publish(TEAM, draft["doc"], by="admin@example.com", version_hash="abc")
    v2 = store.publish(TEAM, policy(agents=[]), by="admin@example.com", version_hash="def")

    assert (v1, v2) == (1, 2)
    published = store.get_policy(TEAM, "PUBLISHED")
    assert published["version"] == 2 and published["doc"]["agents"] == []
    assert [v["version"] for v in store.list_versions(TEAM)] == [2, 1]
    assert store.get_policy(TEAM, "DRAFT")["status"] == "published"


def test_agent_catalog_round_trip(store):
    """승인된 에이전트 카탈로그(Registry 에서 복사) 저장·조회.

    The approved agent catalog (copied from the Registry): save and read.
    """
    store.put_agents([{"name": "api-catalog", "kind": "MCP", "tools": ["list_apis"], "status": "APPROVED"}])

    [agent] = store.list_agents()
    assert agent["updated_at"]
    assert {k: v for k, v in agent.items() if k != "updated_at"} == {
        "name": "api-catalog",
        "kind": "MCP",
        "tools": ["list_apis"],
        "status": "APPROVED",
    }


def test_audit_is_listed_newest_first(store):
    """감사 기록은 최신 순.

    Audit entries list newest first.
    """
    store.audit("publish", actor="a@example.com", team_id=TEAM, detail={"version": 1})
    store.audit("approve", actor="a@example.com", team_id=TEAM)

    entries = store.list_audit(limit=10)
    assert [e["action"] for e in entries] == ["approve", "publish"]
    assert entries[1]["detail"] == {"version": 1}
