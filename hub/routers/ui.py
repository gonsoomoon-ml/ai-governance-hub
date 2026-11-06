"""웹 화면(Jinja2, 서버 렌더, 빌드 없음). 팀 목록 → 정책 편집 → 제출 → 승인·발행 → 조직 설정 → 감사.

발행 순서가 중요하다: admin-api 에 허용 모델을 먼저 쓰고(실패하면 발행하지 않음), 그 다음 발행본을
저장한다. 반대로 하면 PC 의 /model 목록과 서버 강제가 어긋나는 창이 생긴다.

Web screens (Jinja2, server-rendered, no build). Teams -> policy editor -> submit -> approve/publish ->
organization settings -> audit.

Publish order matters: write allowed models to admin-api first (no publish on failure), then store the
published copy. The other order opens a window where the PC's /model list and server enforcement disagree.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from hub.agentcore import AgentCoreError
from hub.auth import current_session, require_admin, require_session, require_team_access
from hub.diff import policy_diff
from hub.fragment import LOCK_CHOICES, STRICT_DEFAULTS, build_fragment, empty_policy, validate_policy, version_of
from hub.gateway_api import GatewayApiError
from hub.i18n import LANG_COOKIE, THEME_COOKIE, lang_of, theme_of, translator
from hub.summary import enforcement_rows, step_of
from hub.timefmt import fmt_ts

router = APIRouter()


# --- pages ---


@router.get("/")
def home(request: Request) -> Response:
    """로그인돼 있으면 /teams.

    /teams when logged in.
    """
    require_session(request)
    return RedirectResponse("/teams", status_code=302)


@router.get("/login")
def login_page(request: Request) -> Response:
    """로그인 화면(이미 로그인이면 /teams).

    The login page (/teams when already logged in).
    """
    if current_session(request):
        return RedirectResponse("/teams", status_code=302)
    return _render(request, "login.html", session=None)


@router.get("/teams")
def teams_page(request: Request) -> Response:
    """관리자: 관리 팀 + 추가 후보(게이트웨이 트리). 리더: 자기 팀.

    Admin: managed teams + candidates from the gateway tree. Leader: own teams.
    """
    session = require_session(request)
    store = request.app.state.store
    teams = store.list_teams()
    if session["role"] != "admin":
        teams = [t for t in teams if t["team_id"] in session["teams"]]
        if len(teams) == 1:  # a leader of one team goes straight to it
            return RedirectResponse(f"/teams/{teams[0]['team_id']}", status_code=302)
    rows = [
        {
            **t,
            "draft": store.get_policy(t["team_id"], "DRAFT"),
            "published": store.get_policy(t["team_id"], "PUBLISHED"),
        }
        for t in teams
    ]
    candidates, gateway_error = [], ""
    if session["role"] == "admin":
        managed = {t["team_id"] for t in teams}
        try:
            candidates = [t for t in request.app.state.gateway.teams() if t["team_id"] not in managed]
        except GatewayApiError as exc:
            gateway_error = str(exc)
    return _render(
        request, "teams.html", session=session, rows=rows, candidates=candidates, gateway_error=gateway_error
    )


@router.post("/teams")
def add_team(request: Request, team_id: str = Form(...)) -> Response:
    """게이트웨이 트리의 팀을 관리 대상에 넣는다(이름·부서·리더는 트리에서).

    Register a gateway-tree team as managed (name, dept and leader from the tree).
    """
    session = require_admin(request)
    try:
        team = next(t for t in request.app.state.gateway.teams() if t["team_id"] == team_id)
    except (GatewayApiError, StopIteration) as exc:
        raise HTTPException(status_code=400, detail=f"team {team_id} not found in the gateway tree: {exc}") from exc
    store = request.app.state.store
    existing = store.get_team(team_id) or {}
    store.put_team(
        {
            "team_id": team_id,
            "name": team["name"],
            "dept": team["dept"],
            "leader_email": existing.get("leader_email", ""),
            "leader_name": team["leader_name"],
        }
    )
    store.audit("team_added", actor=session["email"], team_id=team_id, detail={"name": team["name"]})
    return RedirectResponse("/teams?msg=msg.team_added", status_code=302)


@router.post("/teams/{team_id}/remove")
def remove_team(request: Request, team_id: str) -> Response:
    """관리 해제.

    Unmanage.
    """
    session = require_admin(request)
    request.app.state.store.remove_team(team_id)
    request.app.state.store.audit("team_removed", actor=session["email"], team_id=team_id)
    return RedirectResponse("/teams?msg=msg.team_removed", status_code=302)


@router.get("/teams/{team_id}")
def team_page(request: Request, team_id: str) -> Response:
    """정책 편집 화면.

    The policy editor.
    """
    session = require_team_access(request, team_id)
    return _render_editor(request, session, team_id, errors=None, doc=None)


@router.post("/teams/{team_id}/save")
async def save_draft(request: Request, team_id: str) -> Response:
    """폼 -> 정책 문서 -> 검증 -> 초안 저장. action 이 submit 이면 이어서 승인 요청, publish(관리자)면 바로 발행.

    Form -> policy document -> validation -> draft saved. action=submit then requests approval;
    action=publish (admin) publishes right away. Errors save nothing.
    """
    session = require_team_access(request, team_id)
    store = request.app.state.store
    form = await request.form()
    action = form.get("action") or "save"
    if action == "publish":
        require_admin(request)
    doc = policy_from_form(form)
    errors = validate_policy(doc, store.org_settings())
    if errors:
        return _render_editor(request, session, team_id, errors=errors, doc=doc, status_code=400)
    store.put_draft(team_id, doc, status="draft", edited_by=session["email"])
    store.audit("save", actor=session["email"], team_id=team_id)
    if action == "submit":
        store.put_draft(team_id, doc, status="pending", submitted_by=session["email"], edited_by=session["email"])
        store.audit("submit", actor=session["email"], team_id=team_id)
        return RedirectResponse(f"/teams/{team_id}?msg=msg.submitted", status_code=302)
    if action == "publish":
        _publish(request, session, team_id, doc)
        return RedirectResponse(f"/teams/{team_id}?msg=msg.published", status_code=302)
    return RedirectResponse(f"/teams/{team_id}?msg=msg.saved", status_code=302)


@router.post("/teams/{team_id}/submit")
def submit_draft(request: Request, team_id: str) -> Response:
    """초안을 승인 대기로.

    Put the draft into pending approval.
    """
    session = require_team_access(request, team_id)
    store = request.app.state.store
    draft = store.get_policy(team_id, "DRAFT")
    if not draft:
        raise HTTPException(status_code=400, detail="nothing to submit: save a draft first")
    store.put_draft(team_id, draft["doc"], status="pending", submitted_by=session["email"], edited_by=session["email"])
    store.audit("submit", actor=session["email"], team_id=team_id)
    return RedirectResponse(f"/teams/{team_id}?msg=msg.submitted", status_code=302)


@router.post("/teams/{team_id}/approve")
def approve_draft(request: Request, team_id: str) -> Response:
    """승인 = 발행(관리자). 승인 대기 상태여야 한다.

    Approve = publish (admin). The draft must be pending.
    """
    session = require_admin(request)
    store = request.app.state.store
    draft = store.get_policy(team_id, "DRAFT")
    if not draft or draft.get("status") != "pending":
        raise HTTPException(status_code=400, detail="nothing is pending approval for this team")
    store.audit(
        "approve", actor=session["email"], team_id=team_id, detail={"submitted_by": draft.get("submitted_by", "")}
    )
    _publish(request, session, team_id, draft["doc"])
    return RedirectResponse(f"/teams/{team_id}?msg=msg.published", status_code=302)


@router.post("/teams/{team_id}/reject")
def reject_draft(request: Request, team_id: str, reason: str = Form("")) -> Response:
    """반려(관리자): 상태 rejected + 사유.

    Reject (admin): status rejected with the reason.
    """
    session = require_admin(request)
    store = request.app.state.store
    draft = store.get_policy(team_id, "DRAFT")
    if not draft or draft.get("status") != "pending":
        raise HTTPException(status_code=400, detail="nothing is pending approval for this team")
    store.put_draft(
        team_id, draft["doc"], status="rejected", rejected_reason=reason.strip(), edited_by=session["email"]
    )
    store.audit("reject", actor=session["email"], team_id=team_id, detail={"reason": reason.strip()})
    return RedirectResponse(f"/teams/{team_id}?msg=msg.rejected", status_code=302)


@router.post("/teams/{team_id}/publish")
def publish_draft(request: Request, team_id: str) -> Response:
    """관리자가 초안을 바로 발행(승인 절차 없이).

    Admin publishes the draft directly (no approval step).
    """
    session = require_admin(request)
    draft = request.app.state.store.get_policy(team_id, "DRAFT")
    if not draft:
        raise HTTPException(status_code=400, detail="nothing to publish: save a draft first")
    _publish(request, session, team_id, draft["doc"])
    return RedirectResponse(f"/teams/{team_id}?msg=msg.published", status_code=302)


@router.get("/teams/{team_id}/preview.json")
def preview(request: Request, team_id: str) -> Response:
    """초안(없으면 발행본, 그것도 없으면 빈 정책)의 조각과 버전.

    The fragment and version of the draft (else the published copy, else the empty policy).
    """
    require_team_access(request, team_id)
    store = request.app.state.store
    item = store.get_policy(team_id, "DRAFT") or store.get_policy(team_id, "PUBLISHED")
    doc = item["doc"] if item else empty_policy()
    fragment = _fragment(request, doc)
    return JSONResponse({"version": version_of(fragment), "managedSettings": fragment})


@router.get("/pending")
def pending_page(request: Request) -> Response:
    """승인 대기 목록(관리자).

    Pending list (admin).
    """
    session = require_admin(request)
    store = request.app.state.store
    rows = []
    for team in store.list_teams():
        draft = store.get_policy(team["team_id"], "DRAFT")
        if draft and draft.get("status") == "pending":
            rows.append({**team, "draft": draft})
    return _render(request, "pending.html", session=session, rows=rows)


@router.get("/org")
def org_page(request: Request) -> Response:
    """조직 설정 화면(관리자).

    Organization settings (admin).
    """
    session = require_admin(request)
    return _render_org(request, session, errors=None, org=None)


@router.post("/org")
async def org_save(request: Request) -> Response:
    """조직 고정 항목 + 기본 정책 저장.

    Save fixed locks and the default policy.
    """
    session = require_admin(request)
    store = request.app.state.store
    form = await request.form()
    fixed = {key: (form.get(f"fixed_{key}") or None) for key in LOCK_CHOICES}
    doc = policy_from_form(form)
    doc["models"] = None  # the default policy never restricts models; admin-api is per team
    org = {"locks_fixed": fixed, "default_policy": doc}
    errors = [
        f"fixed_{k}: must be one of {', '.join(LOCK_CHOICES[k])}"
        for k, v in fixed.items()
        if v and v not in LOCK_CHOICES[k]
    ]
    errors += validate_policy(doc, {"locks_fixed": {}})
    if errors:
        return _render_org(request, session, errors=errors, org=org, status_code=400)
    store.put_org_settings(org)
    store.audit("org_saved", actor=session["email"], detail={"locks_fixed": fixed})
    return RedirectResponse("/org?msg=msg.org_saved", status_code=302)


@router.get("/agents")
def agents_page(request: Request) -> Response:
    """사내 에이전트: 카탈로그(허브 표) + 레지스트리 레코드(AgentCore).

    In-house agents: the catalog (hub table) + registry records (AgentCore).
    """
    session = require_admin(request)
    agentcore = request.app.state.agentcore
    records, error = [], ""
    if agentcore is None:
        error = _t(request)("agents.not_configured")
    else:
        try:
            records = agentcore.list_records()
        except Exception as exc:  # noqa: BLE001 — shown on the page, never a 500
            error = f"{type(exc).__name__}: {exc}"
    return _render(
        request,
        "agents.html",
        session=session,
        catalog=request.app.state.store.list_agents(),
        records=records,
        error=error,
    )


@router.post("/agents/refresh")
def agents_refresh(request: Request) -> Response:
    """레지스트리의 APPROVED 레코드를 카탈로그로 복사(도구 이름 포함).

    Copy the registry's APPROVED records into the catalog (tool names included).
    """
    session = require_admin(request)
    agentcore = request.app.state.agentcore
    if agentcore is None:
        raise HTTPException(status_code=503, detail="AgentCore is not configured")
    catalog = agentcore.approved_catalog()
    request.app.state.store.put_agents(catalog)
    request.app.state.store.audit("agents_refreshed", actor=session["email"], detail={"count": len(catalog)})
    return RedirectResponse("/agents?msg=msg.agents_refreshed", status_code=302)


@router.post("/agents/{name}/approve")
def agents_approve(request: Request, name: str) -> Response:
    """레지스트리 레코드 승인(curator) 후 카탈로그 갱신.

    Approve the registry record (curator) and refresh the catalog.
    """
    session = require_admin(request)
    agentcore = request.app.state.agentcore
    if agentcore is None:
        raise HTTPException(status_code=503, detail="AgentCore is not configured")
    agentcore.set_record_status(name, "APPROVED", f"approved in Governance Hub by {session['email']}")
    request.app.state.store.put_agents(agentcore.approved_catalog())
    request.app.state.store.audit("agent_approved", actor=session["email"], detail={"name": name})
    return RedirectResponse("/agents?msg=msg.agents_refreshed", status_code=302)


@router.post("/agents/{name}/reject")
def agents_reject(request: Request, name: str, reason: str = Form("")) -> Response:
    """레지스트리 레코드 반려.

    Reject the registry record.
    """
    session = require_admin(request)
    agentcore = request.app.state.agentcore
    if agentcore is None:
        raise HTTPException(status_code=503, detail="AgentCore is not configured")
    agentcore.set_record_status(name, "REJECTED", reason.strip() or f"rejected in Governance Hub by {session['email']}")
    request.app.state.store.put_agents(agentcore.approved_catalog())
    request.app.state.store.audit("agent_rejected", actor=session["email"], detail={"name": name, "reason": reason})
    return RedirectResponse("/agents?msg=msg.agents_refreshed", status_code=302)


@router.get("/help")
def help_page(request: Request) -> Response:
    """도움말(용어·흐름·역할·FAQ). 로그인한 누구나.

    Help page (terms, flow, roles, FAQ) for any signed-in user.
    """
    session = require_session(request)
    return _render(request, "help.html", session=session)


@router.get("/audit")
def audit_page(request: Request) -> Response:
    """감사 기록(관리자).

    Audit log (admin).
    """
    session = require_admin(request)
    store = request.app.state.store
    team_names = {team["team_id"]: f"{team.get('dept', '')} / {team.get('name', '')}" for team in store.list_teams()}
    return _render(request, "audit.html", session=session, entries=store.list_audit(limit=200), team_names=team_names)


# --- helpers ---


def policy_from_form(form: Any) -> dict:
    """HTML 폼 -> 정책 문서. 모델은 체크박스 목록 또는 "제한 없음"(None).

    HTML form -> policy document. Models are the checked list or "unrestricted" (None).
    """
    unrestricted = bool(form.get("models_unrestricted"))
    models = None if unrestricted else [m for m in form.getlist("models") if m]
    name, repo, ref = (
        (form.get("marketplace_name") or "").strip(),
        (form.get("marketplace_repo") or "").strip(),
        (form.get("marketplace_ref") or "").strip(),
    )
    marketplace = None
    if name or repo:
        marketplace = {"name": name, "repo": repo}
        if ref:
            marketplace["ref"] = ref
    plugins = _lines(form.get("plugins"))
    servers: dict = {}
    for line in _lines(form.get("mcp_servers")):
        parts = line.split()
        servers[parts[0]] = {"url": parts[1] if len(parts) > 1 else ""}
    locks = {key: (form.get(f"lock_{key}") or STRICT_DEFAULTS[key]) for key in LOCK_CHOICES}
    doc = {
        "models": models,
        "marketplace": marketplace,
        "plugins": plugins,
        "mcp_servers": servers,
        "agents": [a for a in form.getlist("agents") if a],
        "locks": locks,
    }
    return doc


def _publish(request: Request, session: dict, team_id: str, doc: dict) -> int:
    """검증 -> admin-api 허용 모델 -> 게이트웨이 대상 보장 -> 발행본 저장 -> 감사. 실패하면 HTTPException(400/502).

    Validate -> admin-api allowed models -> ensure gateway targets -> store the published copy -> audit.
    HTTPException (400/502) on failure.
    """
    store = request.app.state.store
    errors = validate_policy(doc, store.org_settings())
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    if doc.get("models") == []:
        raise HTTPException(status_code=400, detail="models: pick at least one model or choose no restriction")
    try:
        request.app.state.gateway.set_allowed_models(team_id, doc.get("models"))
    except GatewayApiError as exc:
        raise HTTPException(status_code=502, detail=f"not published: {exc}") from exc
    agentcore = request.app.state.agentcore
    if agentcore is not None and doc.get("agents"):
        try:  # the tool-gateway must expose every allowed agent before PCs learn about it
            agentcore.ensure_targets_for(doc["agents"], store.list_agents())
        except AgentCoreError as exc:
            raise HTTPException(status_code=502, detail=f"not published: {exc}") from exc
    fragment = _fragment(request, doc)
    version = store.publish(team_id, doc, by=session["email"], version_hash=version_of(fragment))
    store.audit(
        "publish",
        actor=session["email"],
        team_id=team_id,
        detail={"version": version, "hash": version_of(fragment), "models": doc.get("models")},
    )
    return version


def _fragment(request: Request, doc: dict) -> dict:
    """설정값·카탈로그를 붙여 조각을 만든다.

    Build the fragment with the deployment's settings and catalog.
    """
    settings = request.app.state.settings
    store = request.app.state.store
    return build_fragment(
        doc,
        store.org_settings(),
        store.list_agents(),
        gateway_url=settings.agentcore_gateway_url,
        oauth=settings.oauth(),
    )


def _render_editor(
    request: Request, session: dict, team_id: str, *, errors: list[str] | None, doc: dict | None, status_code: int = 200
) -> Response:
    """편집 화면 렌더(초안·발행본·모델 목록·카탈로그·조직 고정 항목).

    Render the editor (draft, published copy, model list, catalog, org-fixed locks).
    """
    store = request.app.state.store
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team is not managed by the hub")
    draft = store.get_policy(team_id, "DRAFT")
    published = store.get_policy(team_id, "PUBLISHED")
    if doc is None:
        doc = (draft or published or {}).get("doc") or empty_policy()
    models, models_error = [], ""
    try:
        models = request.app.state.gateway.models()
    except GatewayApiError as exc:
        models_error = str(exc)
    known = {m["alias"] for m in models}
    for alias in doc.get("models") or []:  # keep a draft's aliases visible even if admin-api is down
        if alias not in known:
            models.append({"alias": alias, "display_name": alias})
    t = _t(request)
    org = store.org_settings()
    changes = policy_diff((published or {}).get("doc"), draft["doc"], t) if draft else []
    preview_basis = "draft" if draft else ("published" if published else "empty")
    enforcement = enforcement_rows(doc, org, t)
    step = step_of(draft, published)
    return _render(
        request,
        "team.html",
        session=session,
        team=team,
        doc=doc,
        draft=draft,
        published=published,
        changes=changes,
        preview_basis=preview_basis,
        enforcement=enforcement,
        step=step,
        models=models,
        models_error=models_error,
        catalog=store.list_agents(),
        org=org,
        versions=store.list_versions(team_id),
        errors=errors or [],
        lock_choices=LOCK_CHOICES,
        status_code=status_code,
        mcp_text="\n".join(f"{n} {s.get('url', '')}" for n, s in (doc.get("mcp_servers") or {}).items()),
        plugins_text="\n".join(doc.get("plugins") or []),
    )


def _render_org(
    request: Request, session: dict, *, errors: list[str] | None, org: dict | None, status_code: int = 200
) -> Response:
    """조직 설정 화면 렌더.

    Render the organization settings page.
    """
    store = request.app.state.store
    org = org or store.org_settings()
    doc = org["default_policy"]
    return _render(
        request,
        "org.html",
        session=session,
        org=org,
        doc=doc,
        receiving=_teams_receiving_default(request),
        catalog=store.list_agents(),
        errors=errors or [],
        lock_choices=LOCK_CHOICES,
        status_code=status_code,
        mcp_text="\n".join(f"{n} {s.get('url', '')}" for n, s in (doc.get("mcp_servers") or {}).items()),
        plugins_text="\n".join(doc.get("plugins") or []),
    )


def _teams_receiving_default(request: Request) -> list[str] | None:
    """지금 조직 기본 정책을 받는 팀 이름('부서 / 팀') — 게이트웨이의 팀 중 허브가 관리하지 않거나 발행본이 없는 팀.
    게이트웨이를 못 읽으면 None(화면은 목록을 생략한다).

    Teams currently receiving the org default ('dept / name'): gateway teams the hub does not manage or that have
    no published copy. None when the gateway cannot be read (the page omits the list).
    """
    store = request.app.state.store
    try:
        gateway_teams = request.app.state.gateway.teams()
    except GatewayApiError:
        return None
    managed = {team["team_id"] for team in store.list_teams()}
    names = []
    for team in gateway_teams:
        if team["team_id"] in managed and store.get_policy(team["team_id"], "PUBLISHED"):
            continue
        names.append(f"{team['dept']} / {team['name']}")
    return names


def _render(request: Request, name: str, *, session: dict | None, status_code: int = 200, **context: Any) -> Response:
    """템플릿 렌더(공통 문맥: 문구 함수 t, 세션, 알림 메시지).

    Render a template with the shared context (string function t, session, flash message).
    """
    templates = request.app.state.templates
    msg_key = request.query_params.get("msg", "")
    lang = lang_of(request, request.app.state.settings.hub_lang)
    theme = theme_of(request)
    t = translator(lang)
    context.update(
        request=request,
        session=session,
        t=t,
        lang=lang,
        theme=theme,
        path=request.url.path,
        pillar=_pillar_of(request.url.path),
        msg=t(msg_key) if msg_key else "",
        json_dumps=lambda v: json.dumps(v, ensure_ascii=False, indent=2),
        json_dumps_line=lambda v: json.dumps(v, ensure_ascii=False),
        lock_defaults=STRICT_DEFAULTS,
        ts=_ts_filter(request.app.state.settings.hub_tz),
    )
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    if request.query_params.get("lang") == lang:
        response.set_cookie(LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    if request.query_params.get("theme") == theme:
        response.set_cookie(THEME_COOKIE, theme, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


def _pillar_of(path: str) -> str:
    """경로 -> 덱의 기둥 칩: 정책 화면은 p1(AI 거버넌스), 사내 에이전트는 p2(공유), 그 밖은 없음.

    Path -> deck pillar chip: policy pages p1 (AI governance), agents p2 (sharing), none otherwise.
    """
    if path.startswith(("/teams", "/pending", "/org")):
        return "p1"
    if path.startswith("/agents"):
        return "p2"
    return ""


def _t(request: Request):
    """요청 언어의 문구 함수.

    The string function for the request's language.
    """
    return translator(lang_of(request, request.app.state.settings.hub_lang))


def _ts_filter(zone_name: str):
    """템플릿용 시각 함수: 저장된 UTC ISO -> 설정 시간대 'MM/DD HH:MM'(seconds=True 면 초까지).

    Timestamp function for templates: stored UTC ISO -> 'MM/DD HH:MM' in the configured zone (seconds on request).
    """

    def ts(value: str | None, seconds: bool = False) -> str:
        """한 값을 포맷한다.

        Format one value.
        """
        return fmt_ts(value, zone_name, seconds=seconds)

    return ts


def _lines(text: str | None) -> list[str]:
    """여러 줄 입력 -> 비어 있지 않은 줄 목록.

    Multi-line input -> non-empty stripped lines.
    """
    return [line.strip() for line in (text or "").splitlines() if line.strip()]
