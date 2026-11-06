"""목록 화면(0.1.2): 팀 목록 표기, 승인 대기, 사내 에이전트 상태 문구, 감사 기록 라벨, 조직 설정의 안전망 절.

List pages (0.1.2): teams list wording, pending list, agent status labels, audit labels, org safety-net section.
"""

from __future__ import annotations

import re

from conftest import ADMIN_EMAIL, LEADER_EMAIL, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_teams_list_shows_dept_and_team_together_and_unpublished_markers(client, settings, store):
    """팀 목록: '부서 / 팀' 한 칸, 발행본 없음은 '미발행', 추가 목록의 인원은 '(n명)', 관리 해제는 확인창.

    Teams list: one 'dept / team' cell, '미발행' for no published copy, '(n명)' member counts, confirm on remove.
    """
    managed(store)
    login_as(client, settings)

    page = client.get("/teams").text

    assert "engineering / platform" in page
    assert "미발행" in page
    assert "(2명)" in page
    assert 'onsubmit="return confirm(' in page


def test_pending_list_hides_team_ids(client, settings, store):
    """승인 대기 목록은 팀 이름(부서 / 팀)만 보이고 UUID 는 본문에 없다.

    The pending list shows 'dept / team' and no raw UUID in the body text.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data={**FORM, "action": "submit"})
    login_as(client, settings)

    page = client.get("/pending").text

    assert "engineering / platform" in page
    assert f'<span class="muted">{TEAM}</span>' not in page


def test_agents_page_translates_statuses_and_marks_catalog_members(client, settings, store, agentcore):
    """사내 에이전트: 상태는 한국어 문구, 한 표에 '팀 정책에서 선택 가능' 열(카탈로그에 있으면 예).

    Agents page: Korean status labels; one table with a 'selectable in team policy' column (yes when in the catalog).
    """
    agentcore.records = [
        {
            "name": "api-catalog",
            "kind": "MCP",
            "tools": ["list_apis"],
            "status": "PENDING_APPROVAL",
            "endpoint": "https://example/mcp",
            "description": "catalog",
        },
        {
            "name": "dev-guide-qa",
            "kind": "AGENT",
            "tools": ["ask"],
            "status": "APPROVED",
            "endpoint": "https://example/qa",
            "description": "guide",
        },
    ]
    store.put_agents([{"name": "dev-guide-qa", "kind": "AGENT", "tools": ["ask"], "endpoint": "https://example/qa"}])
    login_as(client, settings)

    page = client.get("/agents").text

    assert "승인 대기" in page and "승인됨" in page
    assert "PENDING_APPROVAL" not in page
    assert "팀 정책에서 선택 가능" in page
    assert page.count("<td>예</td>") == 1 and page.count("<td>아니오</td>") == 1


def test_audit_page_shows_labels_team_names_and_local_time(client, settings, store):
    """감사 기록: 행위는 한국어, 팀은 '부서 / 팀', 시각은 화면 시간대 초 단위, 내용은 펼침 상자.

    Audit page: Korean action labels, 'dept / team', local time with seconds, details in a disclosure box.
    """
    managed(store)
    store.audit("publish", actor=ADMIN_EMAIL, team_id=TEAM, detail={"version": 1})
    login_as(client, settings)

    page = client.get("/audit").text

    assert "발행" in page and "engineering / platform" in page
    assert re.search(r"\d\d/\d\d \d\d:\d\d:\d\d", page)
    assert "<details>" in page and "&#34;version&#34;: 1" in page  # autoescaped JSON inside <pre>


def test_org_page_names_the_safety_net_and_lists_the_teams_receiving_it(client, settings, store):
    """조직 설정: '기본 정책 (안전망)' 과 '비워 두면' 설명, 지금 그 정책을 받는 팀 목록(발행되면 빠짐).

    Org page: the safety-net name and 'empty means' hint, and the teams currently receiving it (gone once published).
    """
    managed(store)
    login_as(client, settings)

    page = client.get("/org").text
    assert "기본 정책 (안전망)" in page and "비워 두면" in page
    assert "지금 이 정책을 받는 팀" in page
    assert "engineering / platform" in page and "engineering / mobile" in page

    client.post(SAVE, data={**FORM, "action": "publish"})
    page = client.get("/org").text
    assert "engineering / mobile" in page and "engineering / platform" not in page


def test_org_fixed_locks_are_read_only_in_the_default_policy(client, settings, store):
    """조직 고정된 항목은 기본 정책 폼에서 라디오 대신 읽기 전용 표시.

    An org-fixed lock shows as a read-only marker, not radios, in the default policy form.
    """
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

    page = client.get("/org").text

    assert 'class="pill" title=' in page and "조직 고정: 막음</span>" in page
    assert 'name="lock_personal_customization" value="allow"' not in page
    assert 'name="lock_plugin_sources" value="company_only"' in page


def test_editor_fields_carry_placeholders_and_plain_hints(client, settings, store):
    """편집 폼: 저장소 칸 placeholder, MCP 범례에 백틱 없음, 에이전트 허용 시 tool-gateway 자동 추가 안내, 모델 제한 없음 토글 JS.

    Editor: repo placeholders, MCP legend without backticks, the tool-gateway note, the unrestricted-models toggle script.
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert 'placeholder="owner/repo"' in page
    assert "태그 또는 브랜치" in page
    assert "`" not in page.split("관리형 MCP 서버")[1].split("</legend>")[0]
    assert "tool-gateway" in page
    assert "models_unrestricted" in page and "disabled" in page
