"""감사 기록의 참(0.1.9): 승인 기록은 발행이 성공한 뒤에만 남고, 발행의 admin-api PUT 은 x-request-id 로
허브의 발행 기록과 이어진다.

Audit truthfulness (0.1.9): "approve" is recorded only after a successful publish, and the publish's
admin-api PUT carries an x-request-id that matches the hub's publish entry.
"""

from __future__ import annotations

from tests.conftest import LEADER_EMAIL, TEAM, login_as, policy
from tests.test_ui import managed


def actions(store):
    """감사 기록의 action 목록(최근 순) / audit actions, newest first."""
    return [e["action"] for e in store.list_audit()]


def test_no_approve_entry_when_publish_fails(client, settings, store, admin_api):
    """admin-api 장애(502)나 한 손 멈춤(409)이면 '승인' 기록이 남지 않는다.

    An admin-api failure (502) or a one-writer stop (409) leaves no "approve" entry.
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="pending", submitted_by=LEADER_EMAIL, gateway_models=[])
    login_as(client, settings)

    admin_api.fail = True
    assert client.post(f"/teams/{TEAM}/approve").status_code == 502
    assert "approve" not in actions(store)

    admin_api.fail = False
    admin_api.allowed[TEAM] = ["claude-opus-5-5"]  # changed elsewhere after the draft was saved
    assert client.post(f"/teams/{TEAM}/approve").status_code == 409
    assert "approve" not in actions(store) and "publish_stopped" in actions(store)


def test_approve_entry_follows_a_successful_publish_and_carries_the_version(client, settings, store, admin_api):
    """발행이 성공하면 '승인' 기록이 발행 번호와 함께 남는다.

    A successful publish leaves an "approve" entry carrying the version number.
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="pending", submitted_by=LEADER_EMAIL, gateway_models=[])
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/approve").status_code == 302

    approve = next(e for e in store.list_audit() if e["action"] == "approve")
    assert approve["detail"] == {"submitted_by": LEADER_EMAIL, "version": 1}


def test_publish_put_carries_the_request_id_recorded_in_the_audit_entry(client, settings, store, admin_api):
    """허용 모델 PUT 의 x-request-id = 허브 발행 기록의 request_id (admin-api 감사 기록과 잇는 값).

    The allowed-models PUT's x-request-id equals the hub publish entry's request_id (the link to admin-api's audit).
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="draft", gateway_models=[])
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/publish").status_code == 302

    put_ids = [rid for (m, _, _), rid in zip(admin_api.calls, admin_api.request_ids, strict=True) if m == "PUT"]
    publish = next(e for e in store.list_audit() if e["action"] == "publish")
    assert put_ids == [publish["detail"]["request_id"]] and put_ids[0].startswith("hub-")
    get_ids = [rid for (m, _, _), rid in zip(admin_api.calls, admin_api.request_ids, strict=True) if m == "GET"]
    assert all(rid == "" for rid in get_ids)  # reads are not audited on the admin-api side
