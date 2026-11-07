"""왼쪽 세로 메뉴(0.1.6): 기둥별 묶음, 한 줄 설명, 승인 대기 건수, 팀 바로가기, 현재 위치, 접기, 역할별 항목.

Left sidebar (0.1.6): pillar groups, one-line descriptions, pending count, team shortcuts, current page, collapse, per-role items.
"""

from __future__ import annotations

from conftest import LEADER_EMAIL, OTHER_TEAM, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_admin_sidebar_groups_items_by_pillar_with_descriptions_and_icons(client, settings, store):
    managed(store)
    login_as(client, settings)

    page = client.get("/teams").text

    assert '<aside class="nav"' in page or 'id="sidenav"' in page
    for group in ("정하기", "도구 공유", "기록·도움"):
        assert group in page, group
    assert "팀별 규칙을 쓰고 발행" in page  # description under 팀 정책
    assert page.count("<svg") >= 6
    assert 'aria-current="page" class="active" href="/teams"' in page
    assert 'aria-expanded="true"' in page and "메뉴 접기" in page


def test_sidebar_lists_managed_teams_with_status_and_counts_pending_requests(client, settings, store):
    managed(store)
    managed(store, team_id=OTHER_TEAM, name="mobile")
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data={**FORM, "action": "submit"})
    login_as(client, settings)

    page = client.get("/org").text

    assert "engineering / platform" in page and "engineering / mobile" in page
    assert 'class="badge">1<' in page
    assert 'class="navstatus pending"' in page and 'class="navstatus none"' in page


def test_leader_sidebar_has_only_own_team_and_help(client, settings, store):
    managed(store)
    managed(store, team_id=OTHER_TEAM, name="mobile")
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    page = client.get(f"/teams/{TEAM}").text
    nav = page.split('class="nav"')[1].split("</aside>")[0]

    assert "engineering / platform" in nav and "engineering / mobile" not in nav
    assert 'href="/help"' in nav
    for admin_only in (
        'href="/pending"',
        'href="/org"',
        'href="/plugins"',
        'href="/mcp-servers"',
        'href="/agents"',
        'href="/audit"',
    ):
        assert admin_only not in nav, admin_only


def test_sidebar_collapses_by_default_only_on_the_team_page(client, settings, store):
    managed(store)
    login_as(client, settings)

    assert 'data-nav-default="collapsed"' in client.get(f"/teams/{TEAM}").text
    assert 'data-nav-default="expanded"' in client.get("/teams").text
    assert "localStorage" in client.get("/teams").text
