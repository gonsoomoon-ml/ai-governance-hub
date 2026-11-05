"""LLM 게이트웨이 admin-api 클라이언트(서비스 토큰). 허브가 쓰는 네 끝점만.

  GET /admin/users/tree                    팀 목록(부서 이름·리더)
  GET /admin/models                        모델 목록(ACTIVE 만 씀)
  PUT /admin/teams/{id}/allowed-models     발행 때 허용 모델 반영 — [] = 행 전부 삭제 = 제한 없음
  GET /admin/users/search?q=<email>        로그인 이메일 -> 사용자 id (팀 리더 판정)

클러스터 안 DNS 로 부르므로 VPN 이 필요 없다. 실패는 전부 GatewayApiError 하나로 올린다.

LLM gateway admin-api client (service token). Only the four endpoints the hub uses (see above).
Called over in-cluster DNS, so no VPN. Every failure is raised as one GatewayApiError.
"""

from __future__ import annotations

from typing import Any

import httpx


class GatewayApiError(Exception):
    """admin-api 호출 실패(설정 없음·연결 불가·비2xx).

    An admin-api call failed (not configured, unreachable, non-2xx).
    """


class GatewayApi:
    """admin-api 한 벌에 대한 클라이언트.

    A client for one admin-api deployment.
    """

    def __init__(self, base_url: str, token: str, http: httpx.Client):
        """기본 URL·서비스 토큰·공유 httpx 클라이언트.

        Base URL, service token and the shared httpx client.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http

    def teams(self) -> list[dict]:
        """조직 트리를 펼친 팀 목록: team_id·name·dept·leader_user_id·leader_name·member_count.

        The org tree flattened into teams: team_id, name, dept, leader_user_id, leader_name, member_count.
        """
        tree = self._request("GET", "/admin/users/tree")
        teams: list[dict] = []
        _walk(tree, "", teams)
        return sorted(teams, key=lambda t: (t["dept"], t["name"]))

    def models(self) -> list[dict]:
        """ACTIVE 모델만: alias·display_name.

        ACTIVE models only: alias and display_name.
        """
        items = self._request("GET", "/admin/models").get("items", [])
        return [
            {"alias": m["alias"], "display_name": m.get("display_name") or m["alias"]}
            for m in items
            if m.get("status") == "ACTIVE"
        ]

    def set_allowed_models(self, team_id: str, aliases: list[str] | None) -> None:
        """팀 허용 모델을 바꾼다. None(제한 없음) 은 [] 로 보낸다(admin-api 의 [] = 전체 허용).

        Set the team's allowed models. None (no restriction) is sent as [] (admin-api's [] = all).
        """
        self._request("PUT", f"/admin/teams/{team_id}/allowed-models", json={"model_aliases": list(aliases or [])})

    def find_user_id(self, email: str) -> str | None:
        """이메일이 정확히 같은 사용자의 id. 없으면 None.

        The id of the user whose email matches exactly, or None.
        """
        if not email:
            return None
        items = self._request("GET", "/admin/users/search", params={"q": email, "limit": 50}).get("items", [])
        for item in items:
            if item.get("email", "").lower() == email.lower():
                return item["id"]
        return None

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        """요청 한 번. 설정 없음·전송 오류·비2xx 는 GatewayApiError.

        One request. Not configured, transport error and non-2xx become GatewayApiError.
        """
        if not self.base_url:
            raise GatewayApiError("admin-api is not configured (ADMIN_API_URL)")
        try:
            resp = self.http.request(
                method, self.base_url + path, headers={"Authorization": f"Bearer {self.token}"}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise GatewayApiError(f"admin-api unreachable: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 300:
            raise GatewayApiError(f"admin-api {method} {path} -> {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json() if resp.content else {}
        except ValueError as exc:
            raise GatewayApiError(f"admin-api {method} {path}: not JSON") from exc


def _walk(node: dict | None, dept: str, out: list[dict]) -> None:
    """트리를 내려가며 TEAM 노드를 모은다. 부서 이름은 바로 위 DEPARTMENT 노드.

    Walk the tree collecting TEAM nodes; the dept name is the nearest DEPARTMENT above.
    """
    if not node:
        return
    kind = node.get("type")
    if kind == "DEPARTMENT":
        dept = node.get("name", "")
    if kind == "TEAM":
        meta = node.get("meta") or {}
        out.append(
            {
                "team_id": node["id"],
                "name": node.get("name", ""),
                "dept": dept,
                "leader_user_id": meta.get("leader_user_id") or "",
                "leader_name": meta.get("leader_name") or "",
                "member_count": meta.get("member_count") or 0,
            }
        )
        return
    for child in node.get("children") or []:
        _walk(child, dept, out)
