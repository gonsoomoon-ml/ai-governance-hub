"""Cognito 로그인(인가 코드 + PKCE), id_token 검증, 역할 판정, 세션 쿠키.

Cognito login (authorization code + PKCE), id_token verification, role derivation, session cookie.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from conftest import ADMIN_EMAIL, ISSUER, LEADER_EMAIL, TEAM, login_as
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import hub.auth as auth_module

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def id_token(email: str, groups: list[str] | None = None, **over) -> str:
    """테스트 키로 서명한 Cognito 모양의 id_token.

    A Cognito-shaped id_token signed with the test key.
    """
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "hub-client",
        "sub": "sub-" + email,
        "email": email,
        "token_use": "id",
        "iat": now,
        "exp": now + 300,
    }
    if groups is not None:
        claims["cognito:groups"] = groups
    claims.update(over)
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    """JWKS 조회를 테스트 공개키로 바꾼다.

    Replace the JWKS lookup with the test public key.
    """
    monkeypatch.setattr(auth_module, "signing_key_for", lambda settings, token: PUBLIC_PEM)


def token_endpoint(admin_api, email, groups):
    """admin-api 흉내에 Cognito 토큰 끝점도 얹는다.

    Add the Cognito token endpoint to the fake admin-api handler.
    """
    original = admin_api.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "idp.example.com" and request.url.path == "/oauth2/token":
            form = parse_qs(request.content.decode())
            admin_api.calls.append(("POST", "/oauth2/token", form))
            return httpx.Response(
                200, json={"id_token": id_token(email, groups), "access_token": "at", "expires_in": 3600}
            )
        return original(request)

    admin_api.handle = handle


def start_login(client):
    """/auth/login 을 열어 authorize 리디렉트의 state 를 돌려준다.

    Open /auth/login and return the state from the authorize redirect.
    """
    resp = client.get("/auth/login")
    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    q = parse_qs(url.query)
    assert url.scheme == "https" and url.netloc == "idp.example.com" and url.path == "/oauth2/authorize"
    assert q["response_type"] == ["code"] and q["client_id"] == ["hub-client"]
    assert q["redirect_uri"] == ["https://hub.example.com/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert "openid" in q["scope"][0]
    return q["state"][0]


def test_login_redirects_to_cognito_with_pkce_and_state(client):
    """/auth/login 은 PKCE·state 를 담아 Cognito authorize 로 보낸다.

    /auth/login redirects to Cognito authorize with PKCE and state.
    """
    start_login(client)
    assert client.cookies.get("oidc_state") and client.cookies.get("oidc_verifier")


def test_callback_exchanges_the_code_and_makes_an_admin_session(client, admin_api, settings):
    """콜백: 코드 교환 -> id_token 검증 -> ClaudeAdmin 그룹이면 관리자 세션.

    Callback: code exchange -> id_token verification -> admin session for the ClaudeAdmin group.
    """
    token_endpoint(admin_api, ADMIN_EMAIL, ["ClaudeAdmin"])
    state = start_login(client)

    resp = client.get(f"/auth/callback?code=abc&state={state}")

    assert resp.status_code == 302 and resp.headers["location"] == "/teams"
    form = next(b for m, p, b in admin_api.calls if p == "/oauth2/token")
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["abc"]
    assert form["client_id"] == ["hub-client"] and form["code_verifier"][0]
    session = auth_module.read_session(settings, client.cookies.get("hub_session"))
    assert session["email"] == ADMIN_EMAIL and session["role"] == "admin"


def test_callback_makes_a_leader_session_from_the_gateway_tree(client, admin_api, settings, store):
    """그룹이 없고 admin-api 트리에서 리더인 팀이 있으면 팀 리더 세션(그 팀만).

    Without the group, a user who leads a team in the admin-api tree gets a leader session for that team.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": ""})
    token_endpoint(admin_api, LEADER_EMAIL, None)
    state = start_login(client)

    client.get(f"/auth/callback?code=abc&state={state}")

    session = auth_module.read_session(settings, client.cookies.get("hub_session"))
    assert session["role"] == "leader" and session["teams"] == [TEAM]


def test_callback_uses_the_hub_leader_email_when_the_gateway_has_no_leader(client, admin_api, settings, store):
    """admin-api 에 리더가 없으면 허브 팀 레코드의 leader_email 로 판정한다.

    When admin-api has no leader, the hub team record's leader_email decides.
    """
    store.put_team({"team_id": TEAM, "name": "platform", "dept": "engineering", "leader_email": "other@example.com"})
    token_endpoint(admin_api, "other@example.com", None)
    state = start_login(client)

    client.get(f"/auth/callback?code=abc&state={state}")

    session = auth_module.read_session(settings, client.cookies.get("hub_session"))
    assert session["role"] == "leader" and session["teams"] == [TEAM]


def test_callback_without_any_role_is_refused(client, admin_api, settings):
    """관리자도 리더도 아니면 세션 없이 403.

    Neither admin nor leader -> 403, no session.
    """
    token_endpoint(admin_api, "nobody@example.com", None)
    state = start_login(client)

    resp = client.get(f"/auth/callback?code=abc&state={state}")

    assert resp.status_code == 403
    assert not client.cookies.get("hub_session")


def test_callback_rejects_a_wrong_state(client, admin_api):
    """state 가 다르면 400.

    A different state -> 400.
    """
    token_endpoint(admin_api, ADMIN_EMAIL, ["ClaudeAdmin"])
    start_login(client)

    assert client.get("/auth/callback?code=abc&state=forged").status_code == 400


def test_callback_rejects_a_token_for_another_audience(client, admin_api, monkeypatch):
    """aud 가 다른 id_token 은 401.

    An id_token for another audience -> 401.
    """
    token_endpoint(admin_api, ADMIN_EMAIL, ["ClaudeAdmin"])
    monkeypatch.setattr(auth_module, "signing_key_for", lambda settings, token: PUBLIC_PEM)
    original = admin_api.handle

    def handle(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"id_token": id_token(ADMIN_EMAIL, ["ClaudeAdmin"], aud="other"), "expires_in": 3600}
            )
        return original(request)

    admin_api.handle = handle
    state = start_login(client)

    assert client.get(f"/auth/callback?code=abc&state={state}").status_code == 401


def test_tampered_session_cookie_is_ignored(client, settings):
    """서명이 깨진 세션 쿠키는 로그인 안 된 것으로 본다.

    A session cookie with a broken signature counts as not logged in.
    """
    login_as(client, settings)
    client.cookies.set("hub_session", client.cookies.get("hub_session")[:-3] + "xyz", domain="testserver.local")

    resp = client.get("/teams")

    assert resp.status_code == 302 and resp.headers["location"] == "/login"


def test_logout_clears_the_session(client, settings):
    """로그아웃은 쿠키를 지우고 /login 으로 보낸다.

    Logout clears the cookie and redirects to /login.
    """
    login_as(client, settings)

    resp = client.post("/auth/logout")

    assert resp.status_code == 302 and resp.headers["location"] == "/login"
    assert client.get("/teams").status_code == 302
