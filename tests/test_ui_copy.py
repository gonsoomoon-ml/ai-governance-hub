"""초보자용 문구·시각 요소(0.1.3): 화면 소개 줄, 칸 설명, 진행 단계 띠, 효과 요약, 도움말, 활성 메뉴, 빈 값 표기, 단추 역할.

Beginner copy and visual elements (0.1.3): page intros, field help, progress strip, effect summary, help page,
active nav, empty-value wording, button roles.
"""

from __future__ import annotations

from conftest import LEADER_EMAIL, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_every_page_opens_with_an_intro_line(client, settings, store):
    """모든 화면 제목 아래에 '이 화면은 무엇을 하는 곳' 한 줄.

    Every page has a one-line intro under its title.
    """
    managed(store)
    login_as(client, settings)
    expected = {
        "/teams": "허브가 규칙을 정하는 팀",
        f"/teams/{TEAM}": "다음 조회",
        "/pending": "변경 사항",
        "/org": "안전망",
        "/agents": "레지스트리",
        "/audit": "누가 언제",
        "/help": "용어",
    }
    for path, phrase in expected.items():
        page = client.get(path).text
        assert 'class="intro"' in page and phrase in page, path


def test_login_page_explains_the_hub(client):
    page = client.get("/login").text
    assert "사용 규칙" in page and "팀 리더" in page


def test_help_page_is_open_to_leaders_and_linked_from_the_header(client, settings, store):
    """도움말은 리더도 보고, 메뉴에 있다.

    The help page is open to leaders and linked from the nav.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    page = client.get("/help")

    assert page.status_code == 200
    for term in ("허용 모델", "플러그인 저장소", "MCP", "잠금", "발행", "안전망", "게이트웨이"):
        assert term in page.text, term
    assert 'href="/help"' in client.get(f"/teams/{TEAM}").text


def test_team_page_shows_progress_summary_back_link_and_two_columns(client, settings, store):
    """팀 화면: 진행 단계 띠(현재 1단계), '지금 할 일' 카드, '← 팀 목록', 2단 배치, 처리 중 스크립트.

    Team page: progress strip (step 1 current), the 'what to do now' card, back link, two-column layout, busy script.
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert page.count('class="step current"') == 1
    assert "① 규칙 입력" in page and "④ PC 적용" in page
    assert "지금 할 일" in page
    assert "← 팀 목록" in page
    assert 'class="layout"' in page and "<aside" in page
    assert "처리 중" in page


def test_progress_strip_moves_with_the_status(client, settings, store):
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data={**FORM, "action": "submit"})

    page = client.get(f"/teams/{TEAM}").text

    assert 'class="step current">③' in page


def test_field_help_lines_explain_each_section(client, settings, store):
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    for phrase in (
        "게이트웨이가 서버에서 막으므로",
        "태그를 적으면",
        "외부 도구 서버(MCP)",
        "스스로 바꿀 수 있는 범위",
    ):
        assert phrase in page, phrase


def test_nav_marks_the_current_page_and_role_has_a_tooltip(client, settings, store):
    managed(store)
    login_as(client, settings)

    page = client.get("/pending").text

    assert 'class="active" href="/pending"' in page
    assert 'class="active" href="/teams"' not in page
    assert 'title="' in page.split('class="who"')[1].split("</span>")[0]


def test_empty_values_are_words_not_dashes(client, settings, store):
    """목록의 초안 없음, 변경 사항 표의 빈 칸은 말로.

    'No draft' in the list and empty cells in the change table are words, not dashes.
    """
    managed(store)
    login_as(client, settings)
    assert "초안 없음" in client.get("/teams").text

    client.post(SAVE, data={**FORM, "action": "publish"})
    client.post(SAVE, data={**FORM, "plugins": ""})
    page = client.get(f"/teams/{TEAM}").text
    assert "(비어 있음)" in page and ">—<" not in page


def test_empty_states_say_what_to_do_next(client, settings):
    login_as(client, settings)

    assert "저장 후 승인 요청" in client.get("/pending").text
    assert "등록 스크립트" in client.get("/agents").text


def test_primary_button_is_the_next_action_for_the_role(client, settings, store):
    """리더(작성 중): '저장 후 승인 요청' 이 채운 단추, 초안 저장은 보조. 관리자(승인 대기): 승인·발행이 채운 단추.

    Leader editing: submit is primary and save secondary; admin on a pending draft: approve is primary.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    page = client.get(f"/teams/{TEAM}").text
    assert 'class="primary" name="action" value="submit"' in page
    assert 'class="secondary" name="action" value="save"' in page

    client.post(SAVE, data={**FORM, "action": "submit"})
    login_as(client, settings)
    page = client.get(f"/teams/{TEAM}").text
    assert 'action="/teams/' in page and '<button class="primary">승인·발행</button>' in page
    assert 'class="danger"' in page
