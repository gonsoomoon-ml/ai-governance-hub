"""Cognito 로그인(인가 코드 + PKCE, 공개 클라이언트)과 서명 쿠키 세션, 역할 판정.

역할 둘: 시스템 관리자(cognito:groups 에 ADMIN_GROUP) / 팀 리더(admin-api 트리의 leader_user_id 가
로그인 사용자이거나, 허브 팀 레코드의 leader_email 이 로그인 이메일). 둘 다 아니면 403.
id_token 은 발급자 JWKS 로 검증한다(US-12 교훈: 정적 키로 검증하면 설치 직후 전부 실패).

Cognito login (authorization code + PKCE, public client), a signed-cookie session and role derivation.

Two roles: system administrator (ADMIN_GROUP in cognito:groups) / team leader (the admin-api tree's
leader_user_id is the logged-in user, or the hub team record's leader_email is the login email).
Neither -> 403. The id_token is verified with the issuer's JWKS (US-12 lesson: a static key fails
everything right after install).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from hub.gateway_api import GatewayApiError

SESSION_COOKIE = "hub_session"
SESSION_MAX_AGE = 8 * 3600
TEMP_COOKIE_MAX_AGE = 600

router = APIRouter()


class LoginRequired(Exception):
    """로그인 안 된 요청. 앱의 예외 처리기가 /login 으로 보낸다.

    A request without a login; the app's exception handler redirects to /login.
    """


# --- session cookie ---


def session_cookie_value(settings: Any, payload: dict) -> str:
    """세션 내용을 서명해 쿠키 값으로.

    Sign the session content into a cookie value.
    """
    return _serializer(settings).dumps(payload)


def read_session(settings: Any, value: str | None) -> dict | None:
    """쿠키 값 -> 세션. 서명이 깨졌거나 만료면 None.

    Cookie value -> session; None when the signature is broken or expired.
    """
    if not value:
        return None
    try:
        return _serializer(settings).loads(value, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None


def current_session(request: Request) -> dict | None:
    """요청의 세션(없으면 None).

    The request's session, or None.
    """
    return read_session(request.app.state.settings, request.cookies.get(SESSION_COOKIE))


def require_session(request: Request) -> dict:
    """세션이 없으면 LoginRequired.

    LoginRequired when there is no session.
    """
    session = current_session(request)
    if session is None:
        raise LoginRequired()
    return session


def require_admin(request: Request) -> dict:
    """관리자 세션만.

    Administrator sessions only.
    """
    session = require_session(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="administrators only")
    return session


def require_team_access(request: Request, team_id: str) -> dict:
    """관리자이거나 그 팀의 리더.

    Administrator, or leader of that team.
    """
    session = require_session(request)
    if session.get("role") == "admin" or team_id in (session.get("teams") or []):
        return session
    raise HTTPException(status_code=403, detail="not your team")


def _serializer(settings: Any) -> URLSafeTimedSerializer:
    """세션 서명기. SESSION_SECRET 이 비면 시작을 거부한다.

    The session signer; refuses to start without SESSION_SECRET.
    """
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeTimedSerializer(settings.session_secret, salt="hub-session")


# --- OIDC ---


@router.get("/auth/login")
def login(request: Request) -> Response:
    """state·PKCE 를 만들어 임시 쿠키에 두고 Cognito authorize 로 보낸다.

    Make state and PKCE, keep them in temporary cookies, redirect to Cognito authorize.
    """
    settings = request.app.state.settings
    if not (settings.oidc_authorize_url and settings.oidc_client_id):
        raise HTTPException(status_code=503, detail="login is not configured (OIDC_AUTHORIZE_URL, OIDC_CLIENT_ID)")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": _redirect_uri(request),
            "scope": settings.oidc_scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    resp = RedirectResponse(f"{settings.oidc_authorize_url}?{query}", status_code=302)
    for name, value in (("oidc_state", state), ("oidc_verifier", verifier)):
        resp.set_cookie(
            name, value, max_age=TEMP_COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=_secure(request)
        )
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "") -> Response:
    """코드 교환 -> id_token 검증 -> 역할 판정 -> 세션 쿠키.

    Exchange the code -> verify id_token -> derive the role -> session cookie.
    """
    settings = request.app.state.settings
    if not state or state != request.cookies.get("oidc_state") or not code:
        raise HTTPException(status_code=400, detail="login state mismatch; start again at /auth/login")
    verifier = request.cookies.get("oidc_verifier", "")
    tokens = _exchange_code(request, code, verifier)
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="token response has no id_token (is openid in the scopes?)")
    try:
        claims = verify_id_token(settings, id_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"id_token rejected: {exc}") from exc

    email = (claims.get("email") or "").lower()
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    role, teams = derive_role(
        settings, email, [str(g) for g in groups], request.app.state.store, request.app.state.gateway
    )
    if role is None:
        raise HTTPException(status_code=403, detail=f"{email} is neither an administrator nor a team leader")

    resp = RedirectResponse("/teams", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        session_cookie_value(settings, {"email": email, "sub": claims.get("sub", ""), "role": role, "teams": teams}),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )
    for name in ("oidc_state", "oidc_verifier"):
        resp.delete_cookie(name)
    return resp


@router.post("/auth/logout")
def logout(request: Request) -> Response:
    """세션 쿠키를 지우고 /login 으로.

    Clear the session cookie and go to /login.
    """
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def verify_id_token(settings: Any, token: str) -> dict:
    """발급자 JWKS 의 키로 서명·iss·aud·exp 를 검증한 클레임.

    Claims after verifying signature, iss, aud and exp with the issuer's JWKS key.
    """
    key = signing_key_for(settings, token)
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=settings.oidc_client_id,
        issuer=settings.oidc_issuer,
        options={"require": ["exp", "iss", "aud"]},
    )


def signing_key_for(settings: Any, token: str):
    """토큰의 kid 에 맞는 JWKS 공개키(캐시됨). 테스트는 이 함수를 바꿔 끼운다.

    The JWKS public key for the token's kid (cached). Tests replace this function.
    """
    client = _jwks_clients.get(settings.oidc_issuer)
    if client is None:
        client = jwt.PyJWKClient(f"{settings.oidc_issuer}/.well-known/jwks.json", cache_keys=True)
        _jwks_clients[settings.oidc_issuer] = client
    return client.get_signing_key_from_jwt(token).key


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def derive_role(settings: Any, email: str, groups: list[str], store: Any, gateway: Any) -> tuple[str | None, list[str]]:
    """관리자 그룹이면 ("admin", []); 아니면 리더인 관리 팀이 있을 때 ("leader", [team ids]); 없으면 (None, []).

    ("admin", []) for the admin group; otherwise ("leader", [team ids]) when leading managed teams; else (None, []).
    """
    if settings.admin_group and settings.admin_group in groups:
        return "admin", []
    managed = store.list_teams()
    led = {t["team_id"] for t in managed if (t.get("leader_email") or "").lower() == email and email}
    try:
        user_id = gateway.find_user_id(email)
        if user_id:
            led |= {t["team_id"] for t in gateway.teams() if t["leader_user_id"] == user_id}
    except GatewayApiError:
        pass  # admin-api unreachable: the hub's own leader_email still decides
    managed_ids = {t["team_id"] for t in managed}
    teams = sorted(led & managed_ids)
    return ("leader", teams) if teams else (None, [])


def _exchange_code(request: Request, code: str, verifier: str) -> dict:
    """토큰 끝점에 코드를 교환한다(공개 클라이언트는 client_id 를 본문에, 비밀이 있으면 Basic).

    Exchange the code at the token endpoint (client_id in the body for a public client; Basic with a secret).
    """
    settings = request.app.state.settings
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(request),
        "code_verifier": verifier,
        "client_id": settings.oidc_client_id,
    }
    headers = {"Accept": "application/json"}
    auth = (settings.oidc_client_id, settings.oidc_client_secret) if settings.oidc_client_secret else None
    try:
        resp = request.app.state.http.post(settings.oidc_token_url, data=data, headers=headers, auth=auth)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"token endpoint unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"token endpoint {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _redirect_uri(request: Request) -> str:
    """설정의 redirect_uri, 없으면 요청 호스트로 조립(https 기준).

    The configured redirect_uri, else built from the request host (https).
    """
    settings = request.app.state.settings
    if settings.oidc_redirect_uri:
        return settings.oidc_redirect_uri
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"https://{host}/auth/callback"


def _secure(request: Request) -> bool:
    """https 요청(또는 ALB 뒤)이면 Secure 쿠키.

    Secure cookies for https requests (or behind the ALB).
    """
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
