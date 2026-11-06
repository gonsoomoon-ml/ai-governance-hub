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
