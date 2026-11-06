"""웹 화면: 팀 목록·정책 편집·제출·승인·발행·조직 설정·감사. 역할 가드 포함.

Web screens: teams, policy editor, submit, approve, publish, org settings, audit. Role guards included.
"""

from __future__ import annotations

from conftest import ADMIN_EMAIL, LEADER_EMAIL, OTHER_TEAM, TEAM, login_as, policy

from hub.fragment import MODEL_KEYS

FORM = {
    "models": ["claude-sonnet-5-5", "claude-haiku-5-5"],
    "marketplace_name": "example-extensions",
    "marketplace_repo": "example/claude-extensions",
    "marketplace_ref": "demo-2026-11",
    "plugins": "deck-design\ninfographic-design\n",
    "mcp_servers": "deepwiki https://mcp.deepwiki.com/mcp\n",
    "agents": ["dev-guide-qa", "api-catalog"],
    "lock_plugin_sources": "company_only",
    "lock_personal_customization": "block",
    "lock_mcp": "managed_only",
}


def managed(store, team_id=TEAM, name="platform"):
    """팀을 관리 대상으로 넣는다.

    Register a team as managed.
    """
    store.put_team({"team_id": team_id, "name": name, "dept": "engineering", "leader_email": ""})


def test_pages_need_a_login(client):
    """로그인 없이는 /login 으로.

    Without a login, every page redirects to /login.
    """
    for path in ("/", "/teams", f"/teams/{TEAM}", "/pending", "/org", "/audit", "/agents"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["location"] == "/login", path


def test_admin_sees_managed_teams_and_can_add_one_from_the_gateway_tree(client, settings, store, admin_api):
    """관리자: 관리 팀 목록 + 게이트웨이 트리의 나머지 팀을 골라 추가.

    Admin: managed team list, plus the remaining gateway-tree teams to add.
    """
    managed(store)
    login_as(client, settings)

    page = client.get("/teams")
    assert page.status_code == 200
    assert "platform" in page.text and "mobile" in page.text

    resp = client.post("/teams", data={"team_id": OTHER_TEAM})
    assert resp.status_code == 302
    assert store.get_team(OTHER_TEAM)["name"] == "mobile"
    assert store.get_team(OTHER_TEAM)["dept"] == "engineering"


def test_leader_sees_only_own_teams(client, settings, store):
    """팀 리더는 자기 팀만 보고, 남의 팀 페이지는 403.

    A leader sees only own teams; another team's page is 403.
    """
    managed(store)
    managed(store, OTHER_TEAM, "mobile")
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    page = client.get("/teams")
    assert "platform" in page.text and "mobile" not in page.text
    assert client.get(f"/teams/{TEAM}").status_code == 200
    assert client.get(f"/teams/{OTHER_TEAM}").status_code == 403
    assert client.get("/pending").status_code == 403
    assert client.get("/org").status_code == 403


def test_editor_shows_models_from_admin_api_and_agents_from_the_catalog(client, settings, store):
    """편집 화면: admin-api 의 활성 모델만, 카탈로그의 에이전트.

    Editor: only ACTIVE admin-api models, agents from the catalog.
    """
    managed(store)
    store.put_agents([{"name": "api-catalog", "kind": "MCP", "tools": ["list_apis"], "status": "APPROVED"}])
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}")

    assert "claude-opus-5-5" in page.text and "old-model" not in page.text
    assert "api-catalog" in page.text


def test_save_draft_then_submit_then_approve_publishes_and_pushes_models(client, settings, store, admin_api):
    """저장 -> 제출(리더) -> 승인(관리자) = 발행: 버전 1, admin-api 에 허용 모델 PUT, 감사 기록.

    Save -> submit (leader) -> approve (admin) = publish: version 1, allowed-models PUT, audit entries.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    assert client.post(f"/teams/{TEAM}/save", data=FORM).status_code == 302
    draft = store.get_policy(TEAM, "DRAFT")
    assert draft["status"] == "draft" and draft["doc"]["plugins"] == ["deck-design", "infographic-design"]
    assert draft["doc"]["mcp_servers"] == {"deepwiki": {"url": "https://mcp.deepwiki.com/mcp"}}

    assert client.post(f"/teams/{TEAM}/submit").status_code == 302
    assert store.get_policy(TEAM, "DRAFT")["status"] == "pending"
    assert client.post(f"/teams/{TEAM}/approve").status_code == 403  # leaders cannot approve

    login_as(client, settings)
    assert TEAM in client.get("/pending").text
    assert client.post(f"/teams/{TEAM}/approve").status_code == 302

    published = store.get_policy(TEAM, "PUBLISHED")
    assert published["version"] == 1 and published["published_by"] == ADMIN_EMAIL
    put = [c for c in admin_api.calls if c[0] == "PUT"]
    assert put == [
        ("PUT", f"/admin/teams/{TEAM}/allowed-models", {"model_aliases": ["claude-sonnet-5-5", "claude-haiku-5-5"]})
    ]
    actions = [e["action"] for e in store.list_audit()]
    assert actions[:3] == ["publish", "approve", "submit"]


def test_reject_sends_the_draft_back_with_a_reason(client, settings, store):
    """반려: 상태 rejected + 사유, 발행본 없음.

    Reject: status rejected with a reason, nothing published.
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="pending", submitted_by=LEADER_EMAIL)
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/reject", data={"reason": "too broad"}).status_code == 302

    draft = store.get_policy(TEAM, "DRAFT")
    assert draft["status"] == "rejected" and draft["rejected_reason"] == "too broad"
    assert store.get_policy(TEAM, "PUBLISHED") is None
    assert "too broad" in client.get(f"/teams/{TEAM}").text


def test_unrestricted_models_publish_as_an_empty_list(client, settings, store, admin_api):
    """ "모델 제한 없음" 은 admin-api 에 [] 로(= 행 전부 삭제 = 전체 허용).

    "No model restriction" goes to admin-api as [] (= delete every row = everything allowed).
    """
    managed(store)
    login_as(client, settings)
    form = {**FORM, "models": [], "models_unrestricted": "on"}

    client.post(f"/teams/{TEAM}/save", data=form)
    assert store.get_policy(TEAM, "DRAFT")["doc"]["models"] is None
    client.post(f"/teams/{TEAM}/publish")

    assert [c for c in admin_api.calls if c[0] == "PUT"][0][2] == {"model_aliases": []}


def test_validation_errors_are_shown_and_nothing_is_saved(client, settings, store):
    """잘못된 값(https 아님)은 화면에 오류로 보이고 초안은 저장되지 않는다.

    A bad value (not https) is shown as an error and no draft is saved.
    """
    managed(store)
    login_as(client, settings)

    resp = client.post(f"/teams/{TEAM}/save", data={**FORM, "mcp_servers": "bad http://plain"})

    assert resp.status_code == 400 and "https" in resp.text
    assert store.get_policy(TEAM, "DRAFT") is None


def test_publish_is_refused_when_admin_api_is_down(client, settings, store, admin_api):
    """admin-api 가 죽어 있으면 발행하지 않는다(모델 강제와 PC 목록이 어긋나지 않도록).

    When admin-api is down nothing is published (server enforcement and PC list must not diverge).
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="pending", submitted_by=LEADER_EMAIL)
    login_as(client, settings)
    admin_api.fail = True

    resp = client.post(f"/teams/{TEAM}/approve")

    assert resp.status_code == 502
    assert store.get_policy(TEAM, "PUBLISHED") is None


def test_preview_json_is_the_fragment_of_the_draft(client, settings, store):
    """미리보기 JSON = 초안의 조각(+버전), 모델 키 없음.

    Preview JSON = the draft's fragment (+ version), no model keys.
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="draft")
    login_as(client, settings)

    body = client.get(f"/teams/{TEAM}/preview.json").json()

    assert body["managedSettings"]["enabledPlugins"]["deck-design@example-extensions"] is True
    assert not (set(body["managedSettings"]) & MODEL_KEYS)
    assert len(body["version"]) == 16


def test_org_fixed_lock_blocks_a_differing_team_save(client, settings, store):
    """조직 설정에서 항목을 고정하면 팀 저장이 다른 값을 쓸 수 없다.

    An org-fixed lock makes a team save with a different value fail.
    """
    managed(store)
    login_as(client, settings)
    assert (
        client.post(
            "/org",
            data={
                "fixed_plugin_sources": "company_only",
                "fixed_personal_customization": "",
                "fixed_mcp": "",
                **{k: v for k, v in FORM.items() if k != "models"},
                "models_unrestricted": "on",
            },
        ).status_code
        == 302
    )
    assert store.org_settings()["locks_fixed"]["plugin_sources"] == "company_only"
    assert store.org_settings()["default_policy"]["plugins"] == ["deck-design", "infographic-design"]

    resp = client.post(f"/teams/{TEAM}/save", data={**FORM, "lock_plugin_sources": "unrestricted"})

    assert resp.status_code == 400 and "fixed" in resp.text


def test_audit_page_lists_entries(client, settings, store):
    """감사 기록 화면.

    The audit page.
    """
    store.audit("publish", actor=ADMIN_EMAIL, team_id=TEAM, detail={"version": 1})
    login_as(client, settings)

    assert "발행" in client.get("/audit").text


def test_remove_team_unmanages_it(client, settings, store):
    """관리 해제.

    Unmanage a team.
    """
    managed(store)
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/remove").status_code == 302
    assert store.get_team(TEAM) is None


def test_publish_ensures_gateway_targets_for_the_allowed_agents(client, settings, store, agentcore):
    """발행은 허용된 에이전트의 게이트웨이 대상을 보장한다(에이전트가 없으면 부르지 않음).

    Publishing ensures gateway targets for the allowed agents (not called when there are none).
    """
    managed(store)
    login_as(client, settings)
    store.put_draft(TEAM, policy(agents=["api-catalog"]), status="draft")
    assert client.post(f"/teams/{TEAM}/publish").status_code == 302
    assert agentcore.ensured == [["api-catalog"]]

    store.put_draft(TEAM, policy(agents=[]), status="draft")
    client.post(f"/teams/{TEAM}/publish")
    assert agentcore.ensured == [["api-catalog"]]


def test_agents_page_lists_registry_records_and_approval_refreshes_the_catalog(client, settings, store, agentcore):
    """사내 에이전트 화면: 레코드 목록, 승인하면 카탈로그에 들어간다.

    In-house agents page: record list; approval puts the record into the catalog.
    """
    agentcore.records = [
        {
            "name": "api-catalog",
            "kind": "MCP",
            "tools": ["list_apis"],
            "status": "PENDING_APPROVAL",
            "endpoint": "https://example/mcp",
            "description": "catalog",
        }
    ]
    login_as(client, settings)

    page = client.get("/agents")
    assert page.status_code == 200 and "승인 대기" in page.text

    assert client.post("/agents/api-catalog/approve").status_code == 302
    assert agentcore.status_changes == [("api-catalog", "APPROVED")]
    assert [a["name"] for a in store.list_agents()] == ["api-catalog"]
    assert store.list_audit()[0]["action"] == "agent_approved"
