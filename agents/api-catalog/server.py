"""api-catalog — in-house API catalog as an MCP server for AgentCore Runtime (MCP protocol).

Four plain tools (no LLM) over a bundled catalog (data/apis.json): list_apis, search_api,
get_spec, get_example. Claude Code reaches it through AgentCore Gateway as
`api-catalog___<tool>`.

References:
  - AWS devguide "Deploy MCP servers in AgentCore Runtime" (runtime-mcp): FastMCP on 0.0.0.0:8000/mcp,
    stateless streamable HTTP, payload passed through by InvokeAgentRuntime.
  - gonsoomoon-ml/aiops-multi-agent-workshop agents/*/runtime (Dockerfile / deploy_runtime.py layout).
"""
from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA = Path(__file__).resolve().parent / "data" / "apis.json"
CATALOG: dict = json.loads(DATA.read_text(encoding="utf-8"))

mcp = FastMCP("api-catalog", host="0.0.0.0", port=8000, stateless_http=True)


def _operations(api: dict) -> list[dict]:
    """API 하나의 OpenAPI paths 를 평평한 작업(operation) 목록으로 바꾼다: operation_id, method, path, summary.

    Flatten one API's OpenAPI paths into a list of operations: operation_id, method, path, summary.
    """
    ops = []
    for path, methods in api["openapi"].get("paths", {}).items():
        for method, op in methods.items():
            ops.append({"operation_id": op.get("operationId"), "method": method.upper(),
                        "path": path, "summary": op.get("summary", "")})
    return ops


@mcp.tool()
def list_apis() -> list[dict]:
    """카탈로그의 사내 API 전체를 나열한다: 이름, 설명, 담당 팀, 버전.

    List every internal API in the catalog: name, description, owner team, version.
    """
    return [{"name": n, "description": a["description"], "owner_team": a["owner_team"],
             "version": a["version"]} for n, a in CATALOG.items()]


@mcp.tool()
def search_api(query: str) -> list[dict]:
    """사내 API 와 그 작업(operation)을 키워드로 검색한다(예: 'refund', 'order status', 'email').
    일치한 API 를 일치한 작업과 함께 돌려준다.

    Search internal APIs and their operations by keyword (e.g. 'refund', 'order status', 'email').
    Returns the matching APIs with the operations that matched.
    """
    q = query.lower().split()
    hits = []
    for name, api in CATALOG.items():
        text = f"{name} {api['description']}".lower()
        ops = [o for o in _operations(api) if any(w in f"{o['operation_id']} {o['summary']} {o['path']}".lower() for w in q)]
        if ops or any(w in text for w in q):
            hits.append({"name": name, "description": api["description"], "owner_team": api["owner_team"],
                         "base_url": api["base_url"], "operations": ops or _operations(api)})
    return hits


@mcp.tool()
def get_spec(api_name: str) -> str:
    """사내 API 의 OpenAPI 3.0 문서(JSON)를 base_url·인증 메모와 함께 돌려준다.
    클라이언트 생성에 쓴다. api_name 은 list_apis() 가 돌려준 이름 중 하나.

    Return the OpenAPI 3.0 document (JSON) of an internal API together with base_url and auth notes.
    Use it to generate a client. api_name is one of the names returned by list_apis().
    """
    api = CATALOG.get(api_name)
    if api is None:
        return json.dumps({"error": f"unknown api '{api_name}'", "known": sorted(CATALOG)})
    return json.dumps({"api": api_name, "base_url": api["base_url"], "auth": api["auth"],
                       "openapi": api["openapi"]}, ensure_ascii=False)


@mcp.tool()
def get_example(api_name: str, operation_id: str) -> dict:
    """작업(operation) 하나의 요청/응답 예시를 돌려준다(operation_id 는 명세에 있는 값).

    Return a request/response example for one operation (operation_id is the value in the spec).
    """
    api = CATALOG.get(api_name)
    if api is None:
        return {"error": f"unknown api '{api_name}'", "known": sorted(CATALOG)}
    ex = api.get("examples", {}).get(operation_id)
    if ex is None:
        return {"error": f"no example for '{operation_id}'", "operations": [o["operation_id"] for o in _operations(api)]}
    return {"api": api_name, "operation_id": operation_id, **ex}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
