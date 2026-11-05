"""POST /v1/resolve — gateway-proxy 가 부르는 유일한 길. 이 표만 읽고 AWS 의 다른 API 는 부르지 않는다.

본문 {user_id, team_id, client} 에서 team_id 만 쓴다(추가 키는 무시, 빠지면 빈 값). 관리 팀의
발행본이 있으면 그 조각, 아니면 조직 기본 정책 조각. 어느 쪽이든 200 — 게이트웨이는 비200 을
"허브 없음" 으로 보고 모델 조각만 주므로, 관리하지 않는 팀이 5xx 가 되어서는 안 된다.

POST /v1/resolve — the only route gateway-proxy calls. Reads this table only; no other AWS API.

Of the body {user_id, team_id, client} only team_id is used (extra keys ignored, missing = empty).
A managed team with a published policy gets that fragment, anyone else the org default fragment.
Both are 200: the gateway treats non-200 as "no hub" and sends models only, so an unmanaged
team must never be a 5xx.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Body, HTTPException, Request

from hub.fragment import build_fragment, version_of

router = APIRouter()


@router.post("/v1/resolve")
def resolve(request: Request, body: dict = Body(default_factory=dict)) -> dict:
    """팀의 비(非)모델 조각과 그 버전.

    The team's non-model fragment and its version.
    """
    settings = request.app.state.settings
    store = request.app.state.store
    _require_hub_token(request.headers.get("authorization", ""), settings.hub_token)

    team_id = str(body.get("team_id") or "") if isinstance(body, dict) else ""
    org = store.org_settings()
    published = store.get_policy(team_id, "PUBLISHED") if team_id and store.get_team(team_id) else None
    if published:
        doc, source = published["doc"], "team"
    else:
        doc, source = org["default_policy"], "org-default"
    fragment = build_fragment(
        doc, org, store.list_agents(), gateway_url=settings.agentcore_gateway_url, oauth=settings.oauth()
    )
    return {"version": version_of(fragment), "managedSettings": fragment, "source": source}


def _require_hub_token(header: str, expected: str) -> None:
    """Bearer 토큰 대조(상수 시간). 설정에 토큰이 없으면 503 — 잘못 배포된 허브는 조용히 열리지 않는다.

    Constant-time bearer check. No token configured -> 503, so a misdeployed hub never opens silently.
    """
    if not expected:
        raise HTTPException(status_code=503, detail="hub token not configured")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), expected):
        raise HTTPException(status_code=401, detail="invalid hub token")
