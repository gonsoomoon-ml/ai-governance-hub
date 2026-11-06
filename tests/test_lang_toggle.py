"""한|EN 토글: ?lang= 으로 바꾸면 쿠키에 남아 다음 화면도 그 언어.

Language toggle: ?lang= switches the screen language and sticks through a cookie.
"""

from conftest import login_as


def test_lang_query_switches_the_screen_and_sticks(client, settings):
    login_as(client, settings)

    page = client.get("/teams?lang=en")
    assert page.status_code == 200
    assert "Managed teams" in page.text and "관리하는 팀" not in page.text
    assert client.cookies.get("hub_lang") == "en"

    assert "Managed teams" in client.get("/teams").text

    back = client.get("/teams?lang=ko")
    assert "관리하는 팀" in back.text and client.cookies.get("hub_lang") == "ko"


def test_unknown_lang_is_ignored(client, settings):
    login_as(client, settings)

    page = client.get("/teams?lang=xx")

    assert page.status_code == 200 and "관리하는 팀" in page.text
    assert client.cookies.get("hub_lang") in (None, "ko")


def test_header_offers_the_toggle(client, settings):
    login_as(client, settings)

    page = client.get("/teams").text

    assert "?lang=en" in page and "?lang=ko" in page
