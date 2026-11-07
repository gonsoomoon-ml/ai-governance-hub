"""팀 화면 요약 — 발행본이 PC 에 주는 것 한 줄, 진행 단계(1~4), 상태·역할에 맞는 '지금 할 일' 문구 키.

Team-page summaries: one line of what the published copy gives PCs, the progress step (1..4), and the
'what to do now' string key for the current status and role.
"""

from collections.abc import Callable

T = Callable[[str], str]


def receives_summary(doc: dict, catalog: list[dict], t: T) -> str:
    """발행본 문서 -> '모델 n · 플러그인 n · 사내 MCP 서버 n · 사내 에이전트 n' (모델 제한 없음이면 '전체').
    사내 MCP 서버와 에이전트는 카탈로그의 종류(kind MCP | AGENT)로 센다.

    Published document -> 'models n · plugins n · in-house MCP servers n · in-house agents n' ('all' when
    unrestricted). In-house MCP servers and agents are counted by the catalog kind (MCP | AGENT).
    """
    models = doc.get("models")
    mcp, agents = inhouse_counts(doc, catalog)
    return t("team.receives.summary").format(
        models=t("team.receives.models.all") if models is None else len(models),
        plugins=len(doc.get("plugins") or []),
        mcp=mcp,
        agents=agents,
    )


def inhouse_counts(doc: dict, catalog: list[dict]) -> tuple[int, int]:
    """허용한 사내 항목을 (MCP 서버 수, 에이전트 수) 로 — 카탈로그에 없는 이름은 에이전트로 센다.

    Allowed in-house entries as (MCP servers, agents); names missing from the catalog count as agents.
    """
    kinds = {a["name"]: a.get("kind") for a in catalog}
    allowed = doc.get("agents") or []
    mcp = sum(1 for name in allowed if kinds.get(name) == "MCP")
    return mcp, len(allowed) - mcp


def todo_key(role: str, draft: dict | None, published: dict | None) -> str:
    """역할(admin|leader)과 단계로 '지금 할 일' 문구 키를 고른다: 입력 / 승인 대기 / 발행됨.

    Pick the 'what to do now' string key from the role (admin|leader) and the step: edit / pending / published.
    """
    who = "admin" if role == "admin" else "leader"
    step = step_of(draft, published)
    state = {3: "pending", 4: "published"}.get(step, "edit")
    return f"todo.{who}.{state}"


def step_of(draft: dict | None, published: dict | None) -> int:
    """지금 단계: 1 규칙 입력(초안 없음·작성 중·반려) · 3 관리자 승인 대기 · 4 발행됨(PC 적용 중).

    Current step: 1 editing (no draft, draft, rejected), 3 waiting for approval, 4 published (applied to PCs).
    """
    status = (draft or {}).get("status")
    if status == "pending":
        return 3
    if status == "published" or (draft is None and published):
        return 4
    return 1
