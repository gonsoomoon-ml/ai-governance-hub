"""덱 정렬(0.1.4): 덱의 틀(두 기둥 색·네 곳에서 막음·막는 곳은 유형마다 하나·공유 절차·용어 한 장)을 화면에 그대로.
역할별 화면: 관리자는 전체, 팀 리더는 자기 팀 중심.

Deck alignment (0.1.4): the deck's frame on screen (two pillar colors, four enforcement points, one enforcement
point per type, the sharing procedure, the glossary). Role views: admins see everything, leaders their team.
"""

from __future__ import annotations

from conftest import LEADER_EMAIL, OTHER_TEAM, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_header_carries_the_deck_identity_and_pillar_chip(client, settings, store):
    """검정 헤더에 제품명, 페이지마다 기둥 칩(정책 화면 = AI 거버넌스 코랄, 에이전트 = 공유 파랑).

    Dark header with the product name; each page shows its pillar chip (policy pages coral, agents blue).
    """
    managed(store)
    login_as(client, settings)

    teams = client.get("/teams").text
    assert "AI Governance Hub" in teams and 'class="chip p1"' in teams and "AI 거버넌스" in teams

    agents = client.get("/agents").text
    assert 'class="chip p2"' in agents and "사내 개발 AI 도구 공유" in agents


def test_login_page_is_the_deck_cover(client):
    """로그인 화면 = 덱 표지: 질문 세 줄과 답 한 줄.

    The login page mirrors the deck cover: the three-line question and the one-line answer.
    """
    page = client.get("/login").text

    assert "거버넌스는 어떻게" in page and "체계적으로 쓸까요" in page
    assert "정책은 한 곳에서 정하고" in page and "도구는 한 곳에 모읍니다" in page


def test_teams_landing_states_one_hub_four_enforcement_points(client, settings, store):
    """팀 목록 첫 줄: 정하는 곳은 하나, 네 곳에서 막음 — 보안 프록시는 설계 단계(점선).

    Teams landing: one hub, four enforcement points; the security proxy marked as design stage.
    """
    managed(store)
    login_as(client, settings)

    page = client.get("/teams").text

    for phrase in ("네 곳에서 막", "AI 게이트웨이", "도구 게이트웨이", "직원 PC", "보안 프록시", "설계 단계"):
        assert phrase in page, phrase


def test_team_page_shows_what_the_team_receives_after_publish(client, settings, store):
    """팀 화면 오른쪽 카드: 발행 전엔 '지금 할 일' 만, 발행 뒤 '이 팀이 받는 것' 한 줄(개수). 설명 표는 없다.

    Team page card: before publish only 'what to do now'; after publish one 'what this team receives' line
    with counts. No explanatory table.
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text
    assert "지금 할 일" in page and "이 팀이 받는 것" not in page
    assert "어디에서 막히나" not in page and "서버에서 막는 곳" not in page

    client.post(SAVE, data={**FORM, "action": "publish"})
    page = client.get(f"/teams/{TEAM}").text
    assert "이 팀이 받는 것" in page
    assert "모델 2 · 플러그인 2 · 사내 MCP 서버 1 · 사내 에이전트 1" in page
    assert "발행됐습니다" in page


def test_lock_rows_name_the_real_setting_keys(client, settings, store):
    """잠금 세 항목에 실제 관리형 설정 키 이름 툴팁(덱 FAQ 1 의 다리).

    The three locks carry the real managed-settings key names as tooltips (the deck's FAQ 1 bridge).
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    for key in (
        "strictKnownMarketplaces",
        "disableSideloadFlags",
        "strictPluginOnlyCustomization",
        "allowManagedMcpServersOnly",
    ):
        assert key in page, key


def test_agents_page_shows_the_sharing_procedure_with_approval_as_the_current_step(client, settings, agentcore):
    """사내 에이전트 화면: (0) 만들기 → (1) 검사 → (2) 등록 → (3) 승인(여기) → (4) 배포 두 경로.

    Agents page: the sharing procedure strip with approval as the current step and the two rollout paths.
    """
    agentcore.records = []
    login_as(client, settings)

    page = client.get("/agents").text

    assert 'class="step current">(3)' in page
    assert (
        "(0) 만들기" in page
        and "(4a)" in page
        and "플러그인 저장소" in page
        and "(4b)" in page
        and "도구 게이트웨이" in page
    )


def test_leader_with_one_team_lands_on_that_team(client, settings, store):
    """팀 리더가 팀 하나면 목록을 건너뛰고 바로 그 팀 화면으로.

    A leader of exactly one team skips the list and lands on the team page.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    resp = client.get("/teams")

    assert resp.status_code == 302 and resp.headers["location"] == f"/teams/{TEAM}"


def test_leader_with_two_teams_sees_a_list_titled_my_teams(client, settings, store):
    managed(store)
    managed(store, team_id=OTHER_TEAM, name="mobile")
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM, OTHER_TEAM])

    page = client.get("/teams")

    assert page.status_code == 200 and "내 팀" in page.text


def test_leader_team_page_speaks_to_the_leader(client, settings, store):
    """리더의 팀 화면: 리더용 소개 줄, 조직 고정 항목엔 '관리자가 정함' 툴팁.

    Leader's team page: a leader-oriented intro; org-fixed locks explain that an administrator set them.
    """
    managed(store)
    login_as(client, settings)
    client.post(
        "/org",
        data={
            "fixed_plugin_sources": "",
            "fixed_personal_customization": "block",
            "fixed_mcp": "",
            **{k: v for k, v in FORM.items() if k != "models"},
            "models_unrestricted": "on",
        },
    )
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    page = client.get(f"/teams/{TEAM}").text

    assert "내 팀의" in page
    assert "관리자가 조직 설정에서 정한 값" in page


def test_help_page_uses_the_deck_glossary_and_links_the_deck(client, settings):
    """도움말 = 덱의 용어 한 장(개념도 이름 / 시스템 이름 / 하는 일) + 도입 순서 + 웹 덱 링크.

    Help page: the deck's glossary table, the rollout steps, and a link to the web deck.
    """
    login_as(client, settings)

    page = client.get("/help").text

    for phrase in (
        "개념도 이름",
        "시스템 구조도 이름",
        "AI Governance Hub",
        "AgentCore Gateway",
        "AWS Agent Registry",
        "도입 순서",
        "2단계",
        'rel="noopener"',
    ):
        assert phrase in page, phrase


def test_agents_page_reviews_mcp_servers_and_agents_in_two_tables(client, settings, agentcore):
    """심사 화면: 덱의 서비스형 두 항목대로 '사내 MCP 서버' 표(api-catalog) 다음에 '사내 에이전트' 표(dev-guide-qa).

    Review page: the deck's two service-type items as two tables, in-house MCP servers (api-catalog) before
    in-house agents (dev-guide-qa).
    """
    agentcore.records = [
        {"name": "dev-guide-qa", "kind": "AGENT", "tools": ["ask"], "status": "PENDING_APPROVAL", "description": "Q&A"},
        {"name": "api-catalog", "kind": "MCP", "tools": ["list_apis"], "status": "APPROVED", "description": "API specs"},
    ]
    login_as(client, settings)

    page = client.get("/agents").text

    assert (
        page.index("사내 MCP 서버</h2>")
        < page.index("api-catalog")
        < page.index("사내 에이전트</h2>")
        < page.index("dev-guide-qa")
    )
    assert "플러그인 저장소 화면에서 심사" in page and "사내 MCP 서버&#39;·&#39;사내 에이전트&#39; 칸" in page
