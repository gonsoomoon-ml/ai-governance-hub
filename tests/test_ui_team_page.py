"""팀 화면(0.1.2): 한 폼의 action 단추, 승인 대기 경고, 잠금 기본값 표시, 변경 사항 표, 상태 카드 라벨.

Team page (0.1.2): one form with action buttons, pending warning, lock defaults shown, change table, status labels.
"""

from __future__ import annotations

from conftest import LEADER_EMAIL, TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_action_submit_saves_the_edit_and_requests_approval(client, settings, store):
    """리더가 '저장 후 승인 요청' 을 누르면 편집이 저장된 채로 승인 대기가 된다.

    'Save and request approval' stores the edit and marks the draft pending in one step.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    resp = client.post(SAVE, data={**FORM, "plugins": "concise-response\n", "action": "submit"})

    assert resp.status_code == 302
    draft = store.get_policy(TEAM, "DRAFT")
    assert draft["status"] == "pending" and draft["doc"]["plugins"] == ["concise-response"]
    assert draft["submitted_by"] == LEADER_EMAIL
    assert [e["action"] for e in store.list_audit()][:2] == ["submit", "save"]


def test_action_submit_with_errors_changes_nothing(client, settings, store):
    """검증에 걸리면 저장도 요청도 없다 — 이전 초안과 상태가 그대로.

    Validation errors save nothing and request nothing; the earlier draft and status stay.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data=FORM)

    resp = client.post(SAVE, data={**FORM, "plugins": ["deck-design", "not-there"], "action": "submit"})

    assert resp.status_code == 400
    draft = store.get_policy(TEAM, "DRAFT")
    assert draft["status"] == "draft"
    assert draft["doc"]["mcp_servers"] == {}


def test_admin_action_publish_saves_and_publishes_in_one_step(client, settings, store):
    """관리자의 '저장 후 바로 배포' 은 저장과 배포을 한 번에.

    The admin's 'save and publish now' stores and publishes in one step.
    """
    managed(store)
    login_as(client, settings)

    resp = client.post(SAVE, data={**FORM, "action": "publish"})

    assert resp.status_code == 302
    assert store.get_policy(TEAM, "PUBLISHED")["version"] == 1
    assert store.get_policy(TEAM, "DRAFT")["status"] == "published"
    assert [e["action"] for e in store.list_audit()][:2] == ["publish", "save"]


def test_leader_cannot_use_action_publish(client, settings, store):
    """리더에게 바로 배포은 없다.

    Leaders cannot publish directly.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    assert client.post(SAVE, data={**FORM, "action": "publish"}).status_code == 403
    assert store.get_policy(TEAM, "PUBLISHED") is None


def test_pending_draft_is_read_only_with_a_withdraw_button(client, settings, store):
    """승인 대기 중엔 경고 띠, 폼은 읽기 전용(fieldset disabled), 단추는 '승인 요청 취소' 뿐, 미리보기는 저장본으로(0.1.24).

    While pending: a warning band, the form read-only (disabled fieldset), only the withdraw button, and the
    preview falls back to the saved draft (0.1.24).
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data={**FORM, "action": "submit"})

    page = client.get(f"/teams/{TEAM}").text

    assert "기다리는 동안은 수정할 수 없습니다" in page
    assert '<fieldset class="plain" disabled>' in page
    assert f'action="/teams/{TEAM}/withdraw"' in page and "승인 요청 취소" in page
    assert 'name="action" value="submit"' not in page and 'name="action" value="save"' not in page
    assert 'data-post=""' in page  # the preview button posts nothing: the saved draft is what is shown
    login_as(client, settings)
    admin_page = client.get(f"/teams/{TEAM}").text
    assert "/withdraw" not in admin_page and "승인·배포" in admin_page  # the administrator approves or rejects only
    assert (
        "승인 요청 취소」를 누르세요" not in admin_page and "승인·배포하거나 반려하세요" in admin_page
    )  # role-specific band


def test_withdraw_reopens_the_pending_draft_without_changing_it(client, settings, store):
    """승인 요청 취소: 상태만 작성 중으로, 내용·게이트웨이 스냅샷 유지, 감사 'withdraw'(화면 '승인 요청 취소'); 대기 중이 아니면 409.

    Withdraw: status back to draft, content and gateway snapshot kept, audit 'withdraw' (shown as such); 409 when
    nothing is pending.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(SAVE, data={**FORM, "action": "submit"})
    before = store.get_policy(TEAM, "DRAFT")

    resp = client.post(f"/teams/{TEAM}/withdraw")

    assert resp.status_code == 302 and resp.headers["location"] == f"/teams/{TEAM}?msg=msg.withdrawn"
    after = store.get_policy(TEAM, "DRAFT")
    assert after["status"] == "draft" and after["doc"] == before["doc"]
    assert after.get("gateway_models") == before.get("gateway_models")
    assert store.list_audit()[0]["action"] == "withdraw"
    page = client.get(f"/teams/{TEAM}").text
    assert 'name="action" value="submit"' in page and '<fieldset class="plain">' in page
    assert client.post(f"/teams/{TEAM}/withdraw").status_code == 409
    login_as(client, settings)
    assert "승인 요청 취소" in client.get("/audit").text


def test_lock_radios_default_to_the_strict_value_when_unset(client, settings, store):
    """초안이 없으면 잠금 라디오는 엄격 기본값이 찍혀 있고 '(기본)' 표시가 있다.

    With no draft the lock radios come pre-checked at the strict default, marked '(기본)'.
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert 'name="lock_plugin_sources" value="company_only" checked' in page
    assert 'name="lock_mcp" value="managed_only" checked' in page
    assert "(기본)" in page


def test_change_table_lists_differences_between_published_and_draft(client, settings, store):
    """배포본과 다른 초안이 있으면 '변경 사항' 표에 전/후가 보이고, 같아지면 사라진다.

    A draft that differs from the published copy shows a change table with before/after; equal again hides it.
    """
    managed(store)
    login_as(client, settings)
    client.post(SAVE, data={**FORM, "action": "publish"})
    client.post(SAVE, data={**FORM, "plugins": "concise-response\n"})

    page = client.get(f"/teams/{TEAM}").text
    assert "변경 사항" in page
    assert "deck-design, infographic-design" in page and "concise-response" in page

    client.post(SAVE, data={**FORM, "plugins": "concise-response\n", "action": "publish"})
    assert "변경 사항" not in client.get(f"/teams/{TEAM}").text


def test_status_card_labels_preview_basis_and_current_version(client, settings, store):
    """상태 카드에 '초안:'·'배포본:' 라벨, 미리보기 기준 표시, 배포 이력의 '현재' 표시.

    Status card has draft/published labels, the preview names its basis, the history marks the current version.
    """
    managed(store)
    login_as(client, settings)

    empty = client.get(f"/teams/{TEAM}").text
    assert "초안:" in empty and "배포본:" in empty and "(빈 정책 기준)" in empty

    client.post(SAVE, data={**FORM, "action": "publish"})
    client.post(SAVE, data={**FORM, "plugins": "concise-response\n"})
    page = client.get(f"/teams/{TEAM}").text
    assert "(초안 기준)" in page and "현재" in page


def test_admin_editor_offers_publish_and_save_but_no_approval_request(client, settings, store):
    """관리자 편집 화면: '저장 후 바로 배포'·'초안 저장' 만, '저장 후 승인 요청' 없음(승인 요청은 리더의 일, 0.1.27).

    Admin editor: 'save and publish now' and 'save draft' only; no 'request approval' (that is the leader's move, 0.1.27).
    """
    managed(store)
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert 'name="action" value="publish"' in page and 'name="action" value="save"' in page
    assert 'name="action" value="submit"' not in page and "바로 배포하지 않으려면" in page
