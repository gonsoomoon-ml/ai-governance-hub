"""미리보기 모달(0.1.8): 새 탭 대신 같은 화면의 <dialog> — 팀 화면과 조직 설정 화면 둘 다, 복사·새 창·닫기.

Preview modal (0.1.8): an in-page <dialog> instead of a new tab, on the team page and the org page; copy, open, close.
"""

from __future__ import annotations

import json

from conftest import TEAM, login_as
from test_ui import FORM, managed

SAVE = f"/teams/{TEAM}/save"


def test_team_page_preview_is_a_dialog_with_the_fragment_and_actions(client, settings, store):
    managed(store)
    login_as(client, settings)
    client.post(SAVE, data=FORM)

    page = client.get(f"/teams/{TEAM}").text
    expected = client.get(f"/teams/{TEAM}/preview.json").json()

    assert '<dialog id="preview"' in page and "aria-labelledby" in page
    assert "PC 가 받을 설정" in page and "(초안 기준)" in page
    assert "&#34;managedMcpServers&#34;" in page  # the JSON is rendered inside the dialog (autoescaped)
    assert expected["version"] in page
    assert "복사" in page and f'href="/teams/{TEAM}/preview.json"' in page and "닫기" in page
    assert 'id="preview-open"' in page and "showModal" in page
    assert f'data-post="/teams/{TEAM}/preview"' in page and 'data-form="policy-form"' in page  # live preview (0.1.22)


def test_org_page_previews_the_safety_net_settings(client, settings, store):
    login_as(client, settings)

    page = client.get("/org").text
    expected = client.get("/org/preview.json")

    assert expected.status_code == 200 and "managedSettings" in expected.json()
    assert "strictKnownMarketplaces" in json.dumps(expected.json())
    assert '<dialog id="preview"' in page and "허브가 관리하지 않는 팀이 받는 설정" in page
    assert expected.json()["version"] in page and 'href="/org/preview.json"' in page


def test_org_preview_needs_an_admin(client, settings, store):
    from conftest import LEADER_EMAIL

    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    assert client.get("/org/preview.json").status_code == 403


def test_live_preview_builds_the_fragment_from_the_form_as_typed_without_saving(client, settings, store):
    """미리보기 단추는 저장 전 폼을 그대로 보내 조각을 받는다. 초안은 생기지 않는다(0.1.22).

    The preview button posts the unsaved form and gets its fragment back; no draft is created (0.1.22).
    """
    managed(store)
    login_as(client, settings)

    resp = client.post(f"/teams/{TEAM}/preview", data=FORM)

    assert resp.status_code == 200
    body = resp.json()
    assert body["managedSettings"]["enabledPlugins"]["deck-design@example-extensions"] is True
    assert "tool-gateway" in body["managedSettings"]["managedMcpServers"] and len(body["version"]) == 16
    assert store.get_policy(TEAM, "DRAFT") is None


def test_live_preview_reports_validation_errors_instead_of_a_fragment(client, settings, store):
    managed(store)
    login_as(client, settings)

    resp = client.post(f"/teams/{TEAM}/preview", data={**FORM, "plugins": ["deck-design", "not-there"]})

    assert resp.status_code == 400
    assert any("not-there" in e for e in resp.json()["errors"])


def test_live_preview_respects_team_access(client, settings, store):
    from conftest import LEADER_EMAIL, OTHER_TEAM

    managed(store)
    managed(store, OTHER_TEAM, "mobile")
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    assert client.post(f"/teams/{OTHER_TEAM}/preview", data=FORM).status_code == 403
    assert client.post(f"/teams/{TEAM}/preview", data=FORM).status_code == 200


def test_org_live_preview_applies_the_fixed_locks_as_typed(client, settings, store):
    """조직 설정 미리보기는 저장 전 폼의 고정 항목을 적용한다: 폼이 '허용' 이어도 고정이 '막음' 이면 막음.

    The org preview applies the fixed locks as typed: fixed 'block' wins over the form's 'allow'.
    """
    login_as(client, settings)
    data = {
        "fixed_plugin_sources": "",
        "fixed_personal_customization": "block",
        "fixed_mcp": "",
        **{k: v for k, v in FORM.items() if k != "models"},
        "lock_personal_customization": "allow",
        "models_unrestricted": "on",
    }

    resp = client.post("/org/preview", data=data)

    assert resp.status_code == 200
    assert resp.json()["managedSettings"]["strictPluginOnlyCustomization"] is True
    assert store.org_settings()["locks_fixed"].get("personal_customization") is None  # nothing saved
    assert 'data-post="/org/preview"' in client.get("/org").text
