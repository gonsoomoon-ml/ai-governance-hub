"""POST /v1/resolve — gateway-proxy 가 VK 의 user_id·team_id 로 비(非)모델 조각을 받는 길.

POST /v1/resolve — how gateway-proxy fetches the non-model fragment for a VK's user_id and team_id.
"""

from __future__ import annotations

from conftest import HUB_TOKEN, OTHER_TEAM, TEAM, policy

from hub.fragment import MODEL_KEYS

AUTH = {"Authorization": f"Bearer {HUB_TOKEN}"}


def resolve(client, team_id=TEAM, headers=AUTH, **extra):
    """짧은 호출용 포장.

    Short wrapper for the resolve call.
    """
    body = {"user_id": "00000000-0000-4000-a000-000000000001", "team_id": team_id, "client": "claude-code"}
    body.update(extra)
    return client.post("/v1/resolve", json=body, headers=headers)


def test_requires_the_hub_bearer_token(client):
    """토큰이 없거나 틀리면 401.

    Missing or wrong token -> 401.
    """
    assert resolve(client, headers={}).status_code == 401
    assert resolve(client, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_published_team_policy_becomes_the_fragment(client, store):
    """발행된 팀 정책이 조각이 된다(버전 = 조각 해시).

    The published team policy becomes the fragment (version = fragment hash).
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    store.publish(TEAM, policy(), by="admin@example.com", version_hash="x")

    resp = resolve(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "team"
    assert body["managedSettings"]["enabledPlugins"] == {
        "deck-design@example-extensions": True,
        "infographic-design@example-extensions": True,
    }
    assert len(body["version"]) == 16
    assert not (set(body["managedSettings"]) & MODEL_KEYS)


def test_unmanaged_team_and_empty_team_get_the_org_default(client, store):
    """허브가 관리하지 않는 팀·팀 없는 사용자는 조직 기본 정책 조각(200).

    A team the hub does not manage, or a user without a team, gets the org default fragment (200).
    """
    store.put_org_settings(
        {
            "locks_fixed": {"plugin_sources": None, "personal_customization": None, "mcp": None},
            "default_policy": policy(agents=[], plugins=["deck-design"]),
        }
    )

    for team_id in (OTHER_TEAM, ""):
        resp = resolve(client, team_id=team_id)
        assert resp.status_code == 200, team_id
        assert resp.json()["source"] == "org-default"
        assert resp.json()["managedSettings"]["enabledPlugins"] == {"deck-design@example-extensions": True}


def test_managed_team_without_a_published_policy_gets_the_org_default(client, store):
    """관리 팀이라도 발행본이 없으면 조직 기본.

    A managed team without a published policy gets the org default.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    store.put_draft(TEAM, policy(), status="pending", submitted_by="lead@example.com")

    assert resolve(client).json()["source"] == "org-default"


def test_extra_and_missing_body_keys_are_tolerated(client):
    """본문의 추가 키는 무시하고, team_id 가 빠져도 조직 기본으로 답한다.

    Extra body keys are ignored; a missing team_id still answers with the org default.
    """
    resp = client.post("/v1/resolve", json={"user_id": "u", "claude_code_version": "2.1.317"}, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["source"] == "org-default"


def test_version_is_stable_across_calls_and_changes_on_publish(client, store):
    """같은 정책이면 버전이 같고, 재발행하면 달라진다.

    The version is the same for the same policy and changes on a new publish.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    store.publish(TEAM, policy(), by="admin@example.com", version_hash="x")
    first = resolve(client).json()["version"]

    assert resolve(client).json()["version"] == first
    store.publish(TEAM, policy(plugins=["deck-design"]), by="admin@example.com", version_hash="y")
    assert resolve(client).json()["version"] != first


def test_resolve_uses_the_stored_agent_catalog_for_the_deny_list(client, store):
    """deny 목록은 저장된 카탈로그에서 만든다(resolve 는 AWS 를 부르지 않는다).

    The deny list comes from the stored catalog (resolve never calls AWS).
    """
    store.put_agents(
        [
            {"name": "dev-guide-qa", "kind": "AGENT", "tools": ["ask"], "status": "APPROVED"},
            {"name": "api-catalog", "kind": "MCP", "tools": ["list_apis"], "status": "APPROVED"},
        ]
    )
    store.put_team({"team_id": TEAM, "name": "mobile", "dept": "engineering", "leader_email": ""})
    store.publish(TEAM, policy(agents=["api-catalog"]), by="admin@example.com", version_hash="x")

    settings = resolve(client).json()["managedSettings"]

    assert settings["permissions"] == {"deny": ["mcp__tool-gateway__dev-guide-qa___ask"]}
    assert "tool-gateway" in settings["managedMcpServers"]


def test_healthz(client):
    """/healthz 는 200.

    /healthz answers 200.
    """
    assert client.get("/healthz").status_code == 200
