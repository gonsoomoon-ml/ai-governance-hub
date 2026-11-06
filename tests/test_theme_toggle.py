"""테마 토글(0.1.5): ?theme= 으로 밝게/어둡게/자동을 고르면 쿠키에 남고 <html data-theme> 로 적용된다.
어두운 테마도 읽히게: 두 테마의 토큰이 CSS 에 있고, 채운 단추는 대비를 맞춘 색을 쓴다.

Theme toggle (0.1.5): ?theme= picks light/dark/auto, sticks in a cookie and is applied as <html data-theme>.
Dark must stay readable: both token sets exist and filled buttons use contrast-safe colors.
"""

from conftest import login_as


def test_theme_query_switches_and_sticks(client, settings):
    login_as(client, settings)

    dark = client.get("/teams?theme=dark")
    assert dark.status_code == 200 and '<html lang="ko" data-theme="dark">' in dark.text
    assert client.cookies.get("hub_theme") == "dark"
    assert 'data-theme="dark"' in client.get("/teams").text

    light = client.get("/teams?theme=light")
    assert 'data-theme="light"' in light.text and client.cookies.get("hub_theme") == "light"

    auto = client.get("/teams?theme=auto")
    assert '<html lang="ko">' in auto.text and client.cookies.get("hub_theme") == "auto"  # no attribute = follow the OS


def test_unknown_theme_is_ignored_and_default_is_auto(client, settings):
    login_as(client, settings)

    page = client.get("/teams?theme=neon")

    assert page.status_code == 200 and '<html lang="ko">' in page.text
    assert client.cookies.get("hub_theme") in (None, "auto")


def test_header_offers_the_three_theme_choices(client, settings):
    login_as(client, settings)

    page = client.get("/teams").text

    assert "?theme=light" in page and "?theme=dark" in page and "?theme=auto" in page
    assert "밝게" in page and "어둡게" in page and "자동" in page


def test_both_token_sets_and_contrast_safe_buttons_are_in_the_stylesheet(client):
    css = client.get("/login").text

    assert ':root[data-theme="dark"]' in css
    assert "prefers-color-scheme: dark" in css
    assert "color-scheme" in css
    assert "#D63C1C" in css  # light theme: filled coral button with white text (4.6:1)
    assert "#05070D" in css  # dark theme: ground, and the text color on filled coral buttons
