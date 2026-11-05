"""pytest 공통 설정: moto 로 흉내 낸 DynamoDB 표, 테스트용 설정값, FastAPI 클라이언트.

Shared pytest fixtures: a moto-backed DynamoDB table, test settings and a FastAPI client.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from hub.config import Settings
from hub.main import create_app
from hub.store import Store

HUB_TOKEN = "hub-test-token"
TEAM = "00000000-0000-4000-b000-0000000000bb"
OTHER_TEAM = "00000000-0000-4000-b000-0000000000cc"
GATEWAY_URL = "https://example-gw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"  # mask-ok
ADMIN_API = "http://admin-api.test"
ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE"
LEADER_ID = "00000000-0000-4000-a000-000000000002"
LEADER_EMAIL = "lead@example.com"
ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    """moto 가 실제 AWS 로 나가지 않도록 가짜 자격증명과 리전을 둔다.

    Fake credentials and region so moto never reaches real AWS.
    """
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SECURITY_TOKEN", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """허브 설정값(환경 변수 대신 직접 지정).

    Hub settings given directly instead of through environment variables.
    """
    return Settings(
        hub_token=HUB_TOKEN,
        ddb_table="hub-test",
        aws_region="us-west-2",
        session_secret="test-session-secret",
        agentcore_gateway_url=GATEWAY_URL,
        mcp_oauth_client_id="example-client-id",
        mcp_oauth_callback_port=8094,
        mcp_oauth_metadata_url="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE/.well-known/openid-configuration",
        admin_api_url=ADMIN_API,
        admin_api_token="svc-test",
        oidc_issuer=ISSUER,
        oidc_client_id="hub-client",
        oidc_authorize_url="https://idp.example.com/oauth2/authorize",
        oidc_token_url="https://idp.example.com/oauth2/token",
        oidc_redirect_uri="https://hub.example.com/auth/callback",
        admin_group="ClaudeAdmin",
        _env_file=None,
    )


@pytest.fixture
def store(settings) -> Store:
    """moto 안에 표를 만들어 돌려주는 저장소.

    A Store backed by a table created inside moto.
    """
    with mock_aws():
        s = Store(settings.ddb_table, settings.aws_region)
        s.ensure_table()
        yield s


@pytest.fixture
def admin_api() -> FakeAdminApi:
    """admin-api 흉내: 조직 트리·모델 목록·허용 모델 PUT·사용자 검색. 호출을 기록한다.

    A fake admin-api: org tree, model list, allowed-models PUT, user search. Records calls.
    """
    return FakeAdminApi()


@pytest.fixture
def agentcore() -> FakeAgentCore:
    """AgentCore 흉내: 발행 때 보장한 대상 이름을 기록한다.

    A fake AgentCore that records the targets ensured at publish.
    """
    return FakeAgentCore()


@pytest.fixture
def client(settings, store, admin_api, agentcore) -> TestClient:
    """저장소·admin-api 흉내·AgentCore 흉내가 연결된 FastAPI 테스트 클라이언트(리디렉트는 따라가지 않음).

    A FastAPI test client wired to the moto-backed store, the fake admin-api and the fake AgentCore (no redirect following).
    """
    http = httpx.Client(transport=httpx.MockTransport(lambda request: admin_api.handle(request)))
    return TestClient(
        create_app(settings=settings, store=store, http_client=http, agentcore=agentcore), follow_redirects=False
    )


class FakeAgentCore:
    """허브가 부르는 네 메서드만 흉내.

    Imitates only the four methods the hub calls.
    """

    def __init__(self):
        """빈 기록으로 시작.

        Start with empty records.
        """
        self.ensured: list[list[str]] = []
        self.records: list[dict] = []
        self.status_changes: list[tuple[str, str]] = []

    def list_records(self):
        """레코드 목록.

        Record list.
        """
        return self.records

    def approved_catalog(self):
        """APPROVED 레코드를 카탈로그 모양으로.

        APPROVED records in catalog shape.
        """
        return [
            {
                "name": r["name"],
                "kind": r["kind"],
                "tools": r["tools"],
                "status": r["status"],
                "endpoint": r.get("endpoint", ""),
                "description": r.get("description", ""),
            }
            for r in self.records
            if r["status"] == "APPROVED"
        ]

    def set_record_status(self, name, status, reason):
        """상태 변경 기록.

        Record the status change.
        """
        self.status_changes.append((name, status))
        for r in self.records:
            if r["name"] == name:
                r["status"] = status

    def ensure_targets_for(self, agents, catalog):
        """보장 요청을 기록.

        Record the ensure request.
        """
        self.ensured.append(list(agents))
        return list(agents)


def login_as(client: TestClient, settings, email=ADMIN_EMAIL, role="admin", teams=()) -> None:
    """서명된 세션 쿠키를 심어 로그인 상태로 만든다(Cognito 를 거치지 않음).

    Plant a signed session cookie to be logged in without going through Cognito.
    """
    from hub.auth import session_cookie_value

    client.cookies.set(
        "hub_session",
        session_cookie_value(settings, {"email": email, "sub": "sub-" + email, "role": role, "teams": list(teams)}),
        domain="testserver.local",
    )


class FakeAdminApi:
    """admin-api 의 네 끝점을 흉내 내는 httpx 핸들러.

    An httpx handler imitating the four admin-api endpoints the hub uses.
    """

    def __init__(self):
        """빈 호출 기록으로 시작한다.

        Start with an empty call log.
        """
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail = False  # when True every call answers 503

    def handle(self, request: httpx.Request) -> httpx.Response:
        """경로별 가짜 응답. 토큰이 틀리면 401.

        A fake answer per path; 401 for a wrong token.
        """
        path = request.url.path
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content) if request.content and is_json else None
        self.calls.append((request.method, path, body))
        if request.headers.get("authorization") != "Bearer svc-test":
            return httpx.Response(401, json={"detail": "unauthorized"})
        if self.fail:
            return httpx.Response(503, json={"detail": "down"})
        if path == "/admin/users/tree":
            return httpx.Response(200, json=TREE)
        if path == "/admin/models":
            return httpx.Response(200, json={"items": MODELS})
        if path == "/admin/users/search":
            q = request.url.params.get("q", "")
            items = [u for u in USERS if q and q in u["email"]]
            return httpx.Response(200, json={"items": items, "truncated": False})
        if path.startswith("/admin/teams/") and path.endswith("/allowed-models") and request.method == "PUT":
            return httpx.Response(200, json={"team_id": path.split("/")[3], "model_aliases": body["model_aliases"]})
        return httpx.Response(404, json={"detail": f"no fake for {path}"})


def node(id_, name, type_, children=(), **meta) -> dict:
    """조직 트리 노드 하나.

    One org tree node.
    """
    return {"id": id_, "name": name, "type": type_, "children": list(children), "meta": meta}


TREE = node(
    "org",
    "Example Corp",
    "ORGANIZATION",
    [
        node(
            "dept-eng",
            "engineering",
            "DEPARTMENT",
            [
                node(TEAM, "platform", "TEAM", member_count=3, leader_user_id=LEADER_ID, leader_name="Lead"),
                node(OTHER_TEAM, "mobile", "TEAM", member_count=2, leader_user_id=None),
            ],
            team_count=2,
        ),
    ],
)
MODELS = [
    {"alias": "claude-sonnet-5-5", "display_name": "Sonnet 5.5", "status": "ACTIVE"},
    {"alias": "claude-haiku-5-5", "display_name": "Haiku 5.5", "status": "ACTIVE"},
    {"alias": "claude-opus-5-5", "display_name": "Opus 5.5", "status": "ACTIVE"},
    {"alias": "old-model", "display_name": "Old", "status": "INACTIVE"},
]
USERS = [{"id": LEADER_ID, "email": LEADER_EMAIL, "display_name": "Lead", "team_id": TEAM}]


def policy(**over) -> dict:
    """테스트용 팀 정책 문서. 키워드로 일부만 바꾼다.

    A team policy document for tests; keyword arguments override parts of it.
    """
    doc = {
        "models": ["claude-sonnet-5-5", "claude-haiku-5-5"],
        "marketplace": {"name": "example-extensions", "repo": "example/claude-extensions", "ref": "demo-2026-11"},
        "plugins": ["deck-design", "infographic-design"],
        "mcp_servers": {"deepwiki": {"url": "https://mcp.deepwiki.com/mcp"}},
        "agents": ["dev-guide-qa", "api-catalog"],
        "locks": {"plugin_sources": "company_only", "personal_customization": "block", "mcp": "managed_only"},
    }
    doc.update(over)
    return doc


CATALOG = [
    {"name": "dev-guide-qa", "kind": "AGENT", "tools": ["ask"], "status": "APPROVED"},
    {
        "name": "api-catalog",
        "kind": "MCP",
        "tools": ["list_apis", "search_api", "get_spec", "get_example"],
        "status": "APPROVED",
    },
]

os.environ.setdefault("HUB_TESTING", "1")
