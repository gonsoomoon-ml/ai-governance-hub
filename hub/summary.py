"""팀 화면 요약 — 발행본이 PC 에 주는 것 한 줄, 진행 단계(1~4), 상태·역할에 맞는 '지금 할 일' 문구 키.

Team-page summaries: one line of what the published copy gives PCs, the progress step (1..4), and the
'what to do now' string key for the current status and role.
"""

from collections.abc import Callable

T = Callable[[str], str]


def receives_summary(doc: dict, t: T) -> str:
    """발행본 문서 -> '모델 n · 플러그인 n · MCP 서버 n · 에이전트 n' (모델 제한 없음이면 '전체').

    Published document -> 'models n · plugins n · MCP servers n · agents n' ('all' when unrestricted).
    """
    models = doc.get("models")
    return t("team.receives.summary").format(
        models=t("team.receives.models.all") if models is None else len(models),
        plugins=len(doc.get("plugins") or []),
        mcp=len(doc.get("mcp_servers") or {}),
        agents=len(doc.get("agents") or []),
    )


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
