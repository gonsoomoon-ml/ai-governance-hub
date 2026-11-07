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
    receiving = page.split("지금 이 정책을 받는 팀")[1].split("</p>")[0]  # the sidebar lists every managed team
    assert "engineering / mobile" in receiving and "engineering / platform" not in receiving


def test_org_fixed_locks_are_read_only_in_the_default_policy(client, settings, store):
    """조직 고정된 항목은 기본 정책 폼에서 라디오 대신 한 줄('조직 고정(…): 항목 값')로, 값은 hidden 으로 전달.

    An org-fixed lock shows as one read-only line (not radios) in the default policy form; its value travels hidden.
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

    assert "조직 고정(조직 설정에서 바꿈): 개인 스킬·훅 막음" in page
    assert 'name="lock_personal_customization" value="allow"' not in page
    assert 'type="hidden" name="lock_personal_customization" value="block"' in page
    assert 'name="lock_plugin_sources" value="company_only"' in page


def test_editor_fields_are_pick_lists_from_the_org_catalogs(client, settings, store):
    """편집 폼: 저장소는 드롭다운, 플러그인은 카탈로그의 체크박스(자유 입력 없음, MCP 절 없음), 설명은 한 줄 + '자세히',
    tool-gateway 자동 추가 안내, 모델 제한 없음 토글 JS.

    Editor: repository as a dropdown, plugins as checkboxes from the catalog (no free text, no MCP section),
    one-line help + 'more', the tool-gateway note, the unrestricted-models toggle.
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert 'name="marketplace"' in page and "example-extensions — example/claude-extensions @ demo-2026-11" in page
    assert 'name="plugins" value="deck-design"' in page and 'name="mcp_servers"' not in page
    assert 'placeholder="owner/repo"' not in page and "marketplace_repo" not in page
    assert page.count('<details class="more">') == 4 and "자세히" in page
    assert "tool-gateway" in page
    assert "models_unrestricted" in page and "disabled" in page


def test_org_page_folds_the_safety_net_and_summarizes_it(client, settings, store):
    """조직 설정: 안전망 절은 접혀 있고 요약 한 줄(선택 사항 · 비어 있음 · 받는 팀 수), 입력 칸에 placeholder 없음.
    채우면 요약이 '설정됨 — 개수' 로, 검증 오류가 있으면 펼쳐진 채 렌더.

    Org page: the safety net is folded behind one summary line (optional · empty · receiving count) and the inputs
    carry no placeholders. Once filled the summary says 'set — counts'; a validation error renders it open.
    """
    managed(store)
    login_as(client, settings)

    page = client.get("/org").text
    assert '<details class="card fold">' in page
    assert "선택 사항" in page and "비어 있음(잠금만 내려감)" in page
    assert re.search(r"받는 팀 \d+", page)
    assert "placeholder=" not in page

    org_form = {"fixed_plugin_sources": "", "fixed_personal_customization": "block", "fixed_mcp": ""}
    client.post(
        "/org", data={**org_form, **{k: v for k, v in FORM.items() if k != "models"}, "models_unrestricted": "on"}
    )
    page = client.get("/org").text
    assert "설정됨(플러그인 2 · 에이전트 2)" in page

    bad = client.post("/org", data={**org_form, "marketplace": "ghost-market", "models_unrestricted": "on"})
    assert bad.status_code == 400 and '<details class="card fold" open>' in bad.text
