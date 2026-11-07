"""회사 플러그인 저장소(0.1.12~0.1.18): 설정의 저장소 하나를 관리자가 태그로 읽고 플러그인마다 심사하며, 팀은
승인된 플러그인만 고른다. 문서는 repo·ref 사본을 품는다. PC 의 MCP 연결은 도구 게이트웨이 하나뿐(덱 4장).

Company plugin repository (0.1.12–0.1.18): an administrator reads the one configured repository at a tag and
reviews each plugin; teams pick approved plugins only. Documents carry their own copy of repo/ref. PCs connect
to the tool gateway only (deck slide 4).
"""

from conftest import LEADER_EMAIL, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_admin_reads_the_company_repository_at_a_tag(client, settings, store):
    """읽기: 설정의 repo + 폼의 태그 → manifest 를 읽어 플러그인을 승인 대기로 저장, 감사 기록. 화면은 읽기 전·후가 다르다.

    Read: configured repo + the form's tag -> plugins stored as PENDING, audited. The page differs before and after.
    """
    store.delete_marketplace("example-extensions")  # start unread
    login_as(client, settings)
    before = client.get("/plugins").text
    assert "아직 읽지 않았습니다" in before and 'value="demo-2026-11"' in before and "플러그인 심사 <span" not in before
    github = client.app.state.github
    github.manifests[("example/claude-extensions", "v2")] = {
        "name": "example-extensions",
        "plugins": [{"name": "lint-rules", "description": "House lint rules"}],
    }

    resp = client.post("/plugins/read", data={"ref": "v2"})

    assert resp.status_code == 302 and resp.headers["location"] == "/plugins?msg=msg.market_read"
    market = store.list_marketplaces()[0]
    assert market["repo"] == "example/claude-extensions" and market["ref"] == "v2"
    assert [(p["name"], p["status"]) for p in market["plugins"]] == [("lint-rules", "PENDING")]
    assert github.calls[-1] == "/example/claude-extensions/v2/.claude-plugin/marketplace.json"
    assert store.list_audit()[0]["action"] == "market_read"
    after = client.get("/plugins").text
    assert "플러그인 심사 <span" in after and "lint-rules" in after and "1개 · 승인 0" in after and "다시 읽기" in after


def test_unreadable_tag_is_a_400_with_the_reason_and_keeps_the_catalog(client, settings, store):
    login_as(client, settings)
    missing = client.post("/plugins/read", data={"ref": "nope"})
    assert missing.status_code == 400 and "HTTP 404" in missing.text
    assert store.list_marketplaces()[0]["ref"] == "demo-2026-11"


def test_rereading_keeps_decisions_and_adds_new_plugins_as_pending(client, settings, store):
    login_as(client, settings)
    github = client.app.state.github
    github.manifests[("example/claude-extensions", "demo-2026-11")]["plugins"].append({"name": "new-one"})

    assert client.post("/plugins/read", data={"ref": "demo-2026-11"}).status_code == 302
    statuses = {p["name"]: p["status"] for p in store.list_marketplaces()[0]["plugins"]}
    assert statuses["deck-design"] == "APPROVED" and statuses["new-one"] == "PENDING"


def test_plugins_are_reviewed_one_by_one_and_only_approved_ones_are_selectable(client, settings, store):
    """플러그인 심사: 반려하면 팀 화면에서 사라지고 저장도 400, 다시 승인하면 돌아온다. 감사 기록 2건.

    Plugin review: a rejected plugin disappears from the team page and a save with it is 400; approving brings
    it back. Two audit entries.
    """
    managed(store)
    login_as(client, settings)

    assert "3개 · 승인 3" in client.get("/plugins").text
    assert client.post("/plugins/marketplaces/example-extensions/plugins/concise-response/reject").status_code == 302
    page = client.get("/plugins").text
    assert "반려됨" in page and "3개 · 승인 2" in page

    team = client.get(f"/teams/{TEAM}").text
    assert 'value="concise-response"' not in team and 'value="deck-design"' in team
    refused = client.post(SAVE, data={**FORM, "plugins": ["concise-response"]})
    assert refused.status_code == 400 and "not approved" in refused.text

    assert client.post("/plugins/marketplaces/example-extensions/plugins/concise-response/approve").status_code == 302
    assert 'value="concise-response"' in client.get(f"/teams/{TEAM}").text
    assert [e["action"] for e in store.list_audit()][:2] == ["plugin_approved", "plugin_rejected"]
    assert client.post("/plugins/marketplaces/example-extensions/plugins/ghost/approve").status_code == 404


def test_a_draft_holding_a_later_rejected_plugin_is_flagged_in_the_editor(client, settings, store):
    managed(store)
    login_as(client, settings)
    client.post(SAVE, data={**FORM, "action": "save"})
    client.post("/plugins/marketplaces/example-extensions/plugins/deck-design/reject")

    page = client.get(f"/teams/{TEAM}").text
    assert "승인되지 않음" in page and 'value="deck-design" checked' in page


def test_catalog_routes_need_an_admin(client, settings, store):
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=(TEAM,))
    for path in ("/plugins/read", "/plugins/marketplaces/example-extensions/plugins/deck-design/approve"):
        assert client.post(path, data={"ref": "v1"}).status_code == 403, path
    assert client.get("/plugins").status_code == 403


def test_saved_document_copies_repo_and_ref_from_the_catalog(client, settings, store):
    """팀 저장: 폼은 이름만 보내고(저장소 하나면 hidden), 문서에는 카탈로그의 repo·ref 가 복사돼 발행본이 완결된다.

    Team save: the form sends the name only (hidden when there is one repository); the document carries the
    catalog's repo/ref so the published copy stays self-contained.
    """
    managed(store)
    login_as(client, settings)

    assert client.post(SAVE, data={**FORM, "action": "publish"}).status_code == 302
    doc = store.get_policy(TEAM, "PUBLISHED")["doc"]
    assert doc["marketplace"] == {
        "name": "example-extensions",
        "repo": "example/claude-extensions",
        "ref": "demo-2026-11",
    }
    assert doc["plugins"] == ["deck-design", "infographic-design"]
    assert doc["mcp_servers"] == {}  # PCs connect to the tool gateway only (deck slide 4)

    store.delete_marketplace("example-extensions")
    assert store.get_policy(TEAM, "PUBLISHED")["doc"]["marketplace"]["repo"] == "example/claude-extensions"
    page = client.get(f"/teams/{TEAM}").text
    assert "(카탈로그에 없음)" in page  # the editor flags the orphaned pick instead of hiding it


def test_names_outside_the_catalog_are_refused(client, settings, store):
    managed(store)
    login_as(client, settings)

    market = client.post(SAVE, data={**FORM, "marketplace": "unknown-market"})
    assert market.status_code == 400 and "unknown-market is not registered" in market.text
    plugin = client.post(SAVE, data={**FORM, "plugins": ["deck-design", "not-there"]})
    assert plugin.status_code == 400 and "not approved in marketplace example-extensions: not-there" in plugin.text
    assert store.get_policy(TEAM, "DRAFT") is None


def test_team_page_explains_an_unread_repository_and_links_admins_to_the_plugins_page(client, settings, store):
    managed(store)
    store.delete_marketplace("example-extensions")

    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=(TEAM,))
    page = client.get(f"/teams/{TEAM}").text
    assert "회사 저장소를 읽고 플러그인을 승인하면 여기서 고릅니다" in page and "관리형 MCP 서버" not in page
    assert 'href="/plugins"' not in page.split("</h1>")[1].split("<aside")[0]  # leaders get no admin link in the form

    client.cookies.clear()
    login_as(client, settings)
    page = client.get(f"/teams/{TEAM}").text
    assert page.split("</h1>")[1].split("<aside")[0].count('href="/plugins"') == 1
