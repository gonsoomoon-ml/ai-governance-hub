"""FastAPI 앱 조립. 설정·저장소·HTTP 클라이언트를 인자로 받으므로 테스트가 가짜를 끼울 수 있다.

FastAPI app assembly. Settings, store and HTTP client are arguments so tests can inject fakes.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hub import auth
from hub.config import Settings
from hub.gateway_api import GatewayApi
from hub.i18n import translator
from hub.routers import resolve, ui
from hub.store import Store

TEMPLATES = Path(__file__).parent / "templates"


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    http_client: httpx.Client | None = None,
    agentcore=None,
) -> FastAPI:
    """앱을 만든다. 인자를 안 주면 환경 변수·실제 DynamoDB·실제 HTTP 를 쓴다.

    Build the app. Without arguments it uses environment variables, real DynamoDB and real HTTP.
    """
    settings = settings or Settings()
    store = store or Store(settings.ddb_table, settings.aws_region, settings.ddb_endpoint_url or None)
    http_client = http_client or httpx.Client(timeout=10.0)
    app = FastAPI(title="AI Governance Hub", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.http = http_client
    app.state.gateway = GatewayApi(settings.admin_api_url, settings.admin_api_token, http_client)
    app.state.agentcore = agentcore if agentcore is not None else _default_agentcore(settings)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    app.state.t = translator(settings.hub_lang)
    app.include_router(resolve.router)
    app.include_router(auth.router)
    app.include_router(ui.router)

    @app.exception_handler(auth.LoginRequired)
    def _login_required(request: Request, exc: auth.LoginRequired) -> RedirectResponse:
        """로그인 안 된 화면 요청은 /login 으로.

        Page requests without a login go to /login.
        """
        return RedirectResponse("/login", status_code=302)

    @app.exception_handler(HTTPException)
    async def _forbidden_page(request: Request, exc: HTTPException) -> Response:
        """화면 요청의 403 은 안내 페이지로(누가 왜 막혔는지, 돌아갈 길). API 경로와 다른 상태는 기본 JSON 그대로.

        A 403 on a page request becomes a friendly page (who was refused, why, a way back);
        API paths and other statuses keep FastAPI's JSON answer.
        """
        if exc.status_code != 403 or request.url.path.startswith("/v1/"):
            return await http_exception_handler(request, exc)
        session = auth.current_session(request)
        context = {
            "request": request,
            "session": session,
            "t": request.app.state.t,
            "msg": "",
            "detail": str(exc.detail),
        }
        return request.app.state.templates.TemplateResponse(request, "forbidden.html", context, status_code=403)

    @app.get("/healthz")
    def healthz() -> dict:
        """살아 있음 응답(ALB 헬스체크).

        Liveness answer (ALB health check).
        """
        return {"status": "ok"}

    return app


def _default_agentcore(settings: Settings):
    """AgentCore 설정이 있으면 실제 클라이언트, 없으면 None(화면이 "설정 없음" 을 보여 준다).

    The real AgentCore client when configured, else None (the page says "not configured").
    """
    if not settings.agentcore_registry_id:
        return None
    from hub.agentcore import AgentCore

    return AgentCore(settings)
