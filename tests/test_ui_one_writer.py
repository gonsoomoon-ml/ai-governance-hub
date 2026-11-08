"""한 손 규칙(0.1.9): 팀 허용 모델은 허브와 AI 게이트웨이(admin-ui)에 다 있는 값이라, 허브는 편집을 시작할 때
현재 값을 읽고, 저장할 때 본 값을 초안에 남기며, 배포 직전 다시 읽어 다르면 배포을 멈춘다.

One-writer rule (0.1.9): team allowed models live in both the hub and the AI gateway (admin-ui), so the hub
reads the current value when editing starts, snapshots what it saw at save time, and re-reads right before
publishing — a different value stops the publish.
"""

from __future__ import annotations

from tests.conftest import ADMIN_EMAIL, LEADER_EMAIL, TEAM, login_as, policy
from tests.test_ui import FORM, managed

SONNET, HAIKU, OPUS = "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"


def puts(admin_api):
    """admin-api 에 보낸 PUT 본문들 / the PUT bodies sent to admin-api."""
    return [c[2] for c in admin_api.calls if c[0] == "PUT"]


def test_editor_starts_from_the_gateway_value_when_the_hub_has_nothing(client, settings, store, admin_api):
    """초안·배포본이 없는 팀의 편집 화면은 '제한 없음' 이 아니라 게이트웨이의 현재 값으로 시작한다.

    A team with no draft or published copy starts from the gateway's current value, not "no restriction".
    """
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings)

    page = client.get(f"/teams/{TEAM}").text

    assert f'value="{OPUS}" checked' in page
    assert 'id="models_unrestricted" checked' not in page
    assert "gateway-current" not in page  # same value on both sides: no notice


def test_save_snapshots_the_gateway_value_and_publish_passes_when_unchanged(client, settings, store, admin_api):
    """저장은 그때 본 게이트웨이 값을 초안에 남기고, 배포 직전 값이 같으면 배포된다.

    Saving snapshots the gateway value seen; publishing goes through when it is still the same.
    """
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/save", data=FORM).status_code == 302
    assert store.get_policy(TEAM, "DRAFT")["gateway_models"] == [OPUS]
    assert client.post(f"/teams/{TEAM}/publish").status_code == 302

    assert puts(admin_api) == [{"model_aliases": [SONNET, HAIKU]}]
    assert store.get_policy(TEAM, "PUBLISHED")["version"] == 1


def test_publish_stops_when_the_gateway_value_changed_after_the_save(client, settings, store, admin_api):
    """저장 뒤 admin-ui 가 값을 바꾸면 배포은 409 로 멈추고, PUT 도 배포본도 없다. 화면엔 두 값과 할 일.

    When admin-ui changes the value after the save, publishing stops with 409 — no PUT, no published copy;
    the screen shows both values and what to do.
    """
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)
    admin_api.allowed[TEAM] = [OPUS, HAIKU]  # another hand (admin-ui) edits the gateway

    resp = client.post(f"/teams/{TEAM}/publish")

    assert resp.status_code == 409
    assert f"{OPUS} · {HAIKU}" in resp.text and f"{SONNET} · {HAIKU}" in resp.text
    assert puts(admin_api) == [] and store.get_policy(TEAM, "PUBLISHED") is None
    stopped = [e for e in store.list_audit() if e["action"] == "publish_stopped"]
    assert stopped and stopped[0]["detail"]["gateway_models"] == [OPUS, HAIKU]


def test_editor_shows_the_gateway_value_when_it_differs_and_a_resave_lets_publish_through(
    client, settings, store, admin_api
):
    """어긋난 뒤 편집 화면은 게이트웨이의 지금 값을 알리고, 다시 저장(확인)하면 배포이 통과한다.

    After the drift the editor shows the gateway's value now; saving again (acknowledging) lets publish through.
    """
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)
    admin_api.allowed[TEAM] = [OPUS, HAIKU]

    page = client.get(f"/teams/{TEAM}").text
    assert "gateway-current" in page and f"{OPUS} · {HAIKU}" in page

    assert client.post(f"/teams/{TEAM}/save", data=FORM).status_code == 302
    assert store.get_policy(TEAM, "DRAFT")["gateway_models"] == [OPUS, HAIKU]
    assert client.post(f"/teams/{TEAM}/publish").status_code == 302
    assert puts(admin_api) == [{"model_aliases": [SONNET, HAIKU]}]


def test_submit_and_approve_keep_the_snapshot_with_a_restricted_gateway(client, settings, store, admin_api):
    """제출(리더) → 승인(관리자) 길에서도 스냅샷이 남아, 게이트웨이에 제한이 있어도 배포된다.

    The leader-submit → admin-approve path keeps the snapshot, so a restricted gateway still publishes.
    """
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])
    client.post(f"/teams/{TEAM}/save", data=FORM)
    assert client.post(f"/teams/{TEAM}/submit").status_code == 302
    assert store.get_policy(TEAM, "DRAFT")["gateway_models"] == [OPUS]

    login_as(client, settings)
    assert client.post(f"/teams/{TEAM}/approve").status_code == 302
    assert store.get_policy(TEAM, "PUBLISHED")["published_by"] == ADMIN_EMAIL


def test_the_hubs_own_published_value_counts_as_seen(client, settings, store, admin_api):
    """배포본의 값 = 허브가 마지막으로 쓴 값. 그 뒤 만든 초안(스냅샷 없음)도 게이트웨이가 그 값이면 배포된다.

    The published copy is what the hub last wrote; a later draft without a snapshot still publishes when
    the gateway holds that value.
    """
    managed(store)
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)
    assert client.post(f"/teams/{TEAM}/publish").status_code == 302
    assert admin_api.allowed[TEAM] == [SONNET, HAIKU]
    store.put_draft(TEAM, policy(models=[SONNET]), status="draft", gateway_models=None)  # no snapshot

    assert client.post(f"/teams/{TEAM}/publish").status_code == 302
    assert puts(admin_api)[-1] == {"model_aliases": [SONNET]}


def test_a_draft_without_snapshot_never_releases_a_restriction_set_elsewhere(client, settings, store, admin_api):
    """스냅샷도 배포본도 없는 초안(0.1.9 이전)은 게이트웨이에 제한이 없을 때만 배포된다 — 남이 건 제한은 풀지 않는다.

    A draft with neither snapshot nor published copy (pre-0.1.9) publishes only over an unrestricted gateway
    row — a restriction set by another hand is never released.
    """
    managed(store)
    store.put_draft(TEAM, policy(models=None), status="draft")
    login_as(client, settings)

    assert client.post(f"/teams/{TEAM}/publish").status_code == 302  # gateway unrestricted: fine
    store.put_draft(TEAM, policy(models=None), status="draft", gateway_models=None)
    admin_api.allowed[TEAM] = [OPUS]  # someone restricted the team in admin-ui
    store._put(
        {
            "pk": f"TEAM#{TEAM}",
            "sk": "POLICY#PUBLISHED",
            "doc": policy(models=[HAIKU]),
            "version": 9,
            "version_hash": "x",
            "published_by": ADMIN_EMAIL,
            "published_at": "2026-01-01T00:00:00+00:00",
        }
    )

    assert client.post(f"/teams/{TEAM}/publish").status_code == 409
    assert admin_api.allowed[TEAM] == [OPUS]


def test_publish_is_refused_when_the_current_value_cannot_be_read(client, settings, store, admin_api):
    """현재 값을 읽지 못하면(admin-api 장애) 배포하지 않는다(502).

    When the current value cannot be read (admin-api down) nothing is published (502).
    """
    managed(store)
    store.put_draft(TEAM, policy(), status="draft")
    login_as(client, settings)
    admin_api.fail = True

    assert client.post(f"/teams/{TEAM}/publish").status_code == 502
    assert store.get_policy(TEAM, "PUBLISHED") is None


def test_a_draft_that_changes_the_models_is_not_drift(client, settings, store, admin_api):
    """초안이 모델을 바꾸려는 것은 어긋남이 아니다: 저장 뒤 게이트웨이 값이 그대로면 알림이 없다(0.1.25).

    A draft that intends to change the models is not drift: no notice while the gateway value is unchanged since
    the save (0.1.25).
    """
    managed(store)
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)  # the gateway is unrestricted ([]) at save; the draft asks for two

    assert "gateway-current" not in client.get(f"/teams/{TEAM}").text


def test_the_notice_names_the_value_the_hub_last_saw(client, settings, store, admin_api):
    managed(store)
    admin_api.allowed[TEAM] = [OPUS]
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)
    admin_api.allowed[TEAM] = [OPUS, HAIKU]

    page = client.get(f"/teams/{TEAM}").text

    assert "gateway-current" in page and f"허브가 마지막으로 본 값({OPUS})과 다릅니다" in page


def test_drift_from_the_published_copy_is_noticed_too(client, settings, store, admin_api):
    managed(store)
    login_as(client, settings)
    client.post(f"/teams/{TEAM}/save", data=FORM)
    assert client.post(f"/teams/{TEAM}/publish").status_code == 302
    assert "gateway-current" not in client.get(f"/teams/{TEAM}").text  # gateway == published copy
    admin_api.allowed[TEAM] = [OPUS]  # another hand changes it in admin-ui

    assert "gateway-current" in client.get(f"/teams/{TEAM}").text
