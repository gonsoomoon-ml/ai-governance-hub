"""조직 카탈로그(0.1.12): 관리자가 회사 저장소(GitHub 에서 읽음)·MCP 서버를 등록하고, 팀은 그 목록에서 고른다.

Org catalogs (0.1.12): an administrator registers company repositories (read from GitHub) and MCP servers;
teams pick from those lists. Documents carry their own copy of repo/ref/url.
"""

from conftest import LEADER_EMAIL, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"
OTHER = {
    "name": "other-tools",
    "plugins": [{"name": "lint-rules", "description": "House lint rules"}],
}


def test_admin_reads_a_repository_from_github_into_the_catalog(client, settings, store):
    """저장소 등록: owner/repo + 태그 → manifest 를 읽어 이름(비우면 manifest 이름)·플러그인 목록을 저장, 감사 기록.

    Register: owner/repo + tag -> the manifest is read, name (manifest's when blank) and plugins stored, audited.
    """
    login_as(client, settings)
    client.app.state.github.manifests[("example/other-tools", "v1")] = OTHER

    resp = client.post("/org/marketplaces", data={"repo": "example/other-tools", "ref": "v1", "name": ""})

    assert resp.status_code == 302 and resp.headers["location"] == "/org?msg=msg.market_added"
    names = {m["name"]: m for m in store.list_marketplaces()}
    assert names["other-tools"]["repo"] == "example/other-tools" and names["other-tools"]["ref"] == "v1"
    assert [p["name"] for p in names["other-tools"]["plugins"]] == ["lint-rules"]
    assert client.app.state.github.calls == ["/example/other-tools/v1/.claude-plugin/marketplace.json"]
    assert store.list_audit()[0]["action"] == "market_added"

    page = client.get("/org").text
    assert "other-tools" in page and "lint-rules" in page and "example/other-tools @ v1" in page


def test_unreadable_repository_is_a_400_with_the_reason(client, settings, store):
    login_as(client, settings)

    missing = client.post("/org/marketplaces", data={"repo": "example/claude-extensions", "ref": "nope"})
    assert missing.status_code == 400 and "HTTP 404" in missing.text
    bad = client.post("/org/marketplaces", data={"repo": "not a repo", "ref": ""})
    assert bad.status_code == 400 and "owner/repo" in bad.text
    assert len(store.list_marketplaces()) == 1


def test_refresh_rereads_the_stored_ref_and_delete_removes_it(client, settings, store):
    login_as(client, settings)
    github = client.app.state.github
    github.manifests[("example/claude-extensions", "demo-2026-11")]["plugins"].append({"name": "new-one"})

    assert client.post("/org/marketplaces/example-extensions/refresh").status_code == 302
    plugins = [p["name"] for p in store.list_marketplaces()[0]["plugins"]]
    assert (
        "new-one" in plugins
        and github.calls[-1] == "/example/claude-extensions/demo-2026-11/.claude-plugin/marketplace.json"
    )

    assert client.post("/org/marketplaces/example-extensions/delete").status_code == 302
    assert store.list_marketplaces() == []
    assert client.post("/org/marketplaces/ghost/refresh").status_code == 404


def test_admin_registers_and_removes_mcp_servers(client, settings, store):
    login_as(client, settings)

    ok = client.post(
        "/org/mcp-servers", data={"name": "docs", "url": "https://docs.example.com/mcp", "description": "Docs"}
    )
    assert ok.status_code == 302
    assert [s["name"] for s in store.list_mcp_servers()] == ["deepwiki", "docs"]
    assert "docs.example.com" in client.get("/org").text

    bad = client.post("/org/mcp-servers", data={"name": "plain", "url": "http://plain.example/mcp"})
    assert bad.status_code == 400 and "https://" in bad.text
    reserved = client.post("/org/mcp-servers", data={"name": "tool-gateway", "url": "https://x.example/mcp"})
    assert reserved.status_code == 400

    assert client.post("/org/mcp-servers/docs/delete").status_code == 302
    assert [s["name"] for s in store.list_mcp_servers()] == ["deepwiki"]


def test_catalog_routes_need_an_admin(client, settings, store):
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=(TEAM,))
    for path in (
        "/org/marketplaces",
        "/org/mcp-servers",
        "/org/marketplaces/example-extensions/delete",
        "/org/mcp-servers/deepwiki/delete",
    ):
        assert client.post(path, data={"repo": "a/b", "name": "n", "url": "https://x/mcp"}).status_code == 403, path


def test_saved_document_copies_repo_ref_and_url_from_the_catalog(client, settings, store):
    """팀 저장: 폼은 이름만 보내고, 문서에는 카탈로그의 repo·ref·url 이 복사돼 발행본이 완결된다.

    Team save: the form sends names only; the document carries the catalog's repo/ref/url, so the published copy
    stays self-contained (deleting the catalog entry later does not change it).
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
    assert doc["mcp_servers"] == {"deepwiki": {"url": "https://mcp.deepwiki.com/mcp"}}

    client.post("/org/marketplaces/example-extensions/delete")
    assert store.get_policy(TEAM, "PUBLISHED")["doc"]["marketplace"]["repo"] == "example/claude-extensions"
    page = client.get(f"/teams/{TEAM}").text
    assert "(카탈로그에 없음)" in page  # the editor flags the orphaned pick instead of hiding it


def test_names_outside_the_catalogs_are_refused(client, settings, store):
    managed(store)
    login_as(client, settings)

    market = client.post(SAVE, data={**FORM, "marketplace": "unknown-market"})
    assert market.status_code == 400 and "unknown-market is not registered" in market.text
    plugin = client.post(SAVE, data={**FORM, "plugins": ["deck-design", "not-there"]})
    assert plugin.status_code == 400 and "not in marketplace example-extensions: not-there" in plugin.text
    server = client.post(SAVE, data={**FORM, "mcp_servers": ["deepwiki", "rogue"]})
    assert server.status_code == 400 and "rogue is not registered" in server.text
    assert store.get_policy(TEAM, "DRAFT") is None


def test_team_page_explains_empty_catalogs_and_links_admins_to_org_settings(client, settings, store):
    managed(store)
    store.delete_marketplace("example-extensions")
    store.delete_mcp_server("deepwiki")

    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=(TEAM,))
    page = client.get(f"/teams/{TEAM}").text
    assert "회사 저장소를 등록하면 여기서 고릅니다" in page and "MCP 서버를 등록하면 여기서 고릅니다" in page
    assert 'href="/org"' not in page.split("</h1>")[1].split("<aside")[0]  # leaders get no org link in the form

    client.cookies.clear()
    login_as(client, settings)
    page = client.get(f"/teams/{TEAM}").text
    assert page.split("</h1>")[1].split("<aside")[0].count('href="/org"') == 2
