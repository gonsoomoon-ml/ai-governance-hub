"""권한 없음 화면(0.1.2): 리더도 관리자도 아닌 로그인, 리더의 관리자 화면 접근은 원시 JSON 이 아니라 안내 페이지.

Forbidden page (0.1.2): a login with no role, or a leader on an admin page, gets a friendly page instead of raw JSON.
"""

from __future__ import annotations

from conftest import LEADER_EMAIL, TEAM, login_as
from test_auth import fake_jwks, start_login, token_endpoint  # noqa: F401  (autouse JWKS fake must be in scope)
from test_ui import managed


def test_no_role_login_gets_a_friendly_page(client, admin_api, settings):
    """역할 없는 로그인: 403 HTML, 누가 왜 막혔는지와 돌아갈 길, 세션 없음.

    No-role login: 403 HTML saying who was refused and why, a way back, and no session.
    """
    token_endpoint(admin_api, "nobody@example.com", None)
    state = start_login(client)

    resp = client.get(f"/auth/callback?code=abc&state={state}")

    assert resp.status_code == 403 and resp.headers["content-type"].startswith("text/html")
    assert "권한이 없습니다" in resp.text and "nobody@example.com" in resp.text
    assert "로그인 화면으로" in resp.text
    assert not client.cookies.get("hub_session")


def test_leader_on_an_admin_page_gets_the_same_page(client, settings, store):
    """리더가 관리자 화면에 가면 같은 안내 페이지(로그아웃 단추 포함).

    A leader opening an admin page gets the same page, with the logout button.
    """
    managed(store)
    login_as(client, settings, email=LEADER_EMAIL, role="leader", teams=[TEAM])

    resp = client.get("/pending")

    assert resp.status_code == 403 and "권한이 없습니다" in resp.text and "로그아웃" in resp.text


def test_api_errors_stay_json(client):
    """API 경로(/v1/…)의 오류는 그대로 JSON.

    Errors on API paths (/v1/...) stay JSON.
    """
    resp = client.post("/v1/resolve", json={"team_id": "x"}, headers={"Authorization": "Bearer nope"})

    assert resp.status_code == 401 and resp.headers["content-type"].startswith("application/json")
