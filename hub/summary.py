"""발행 효과 요약과 진행 단계 — 정책 문서를 "PC 에서 일어나는 일" 문장으로, 상태를 1~4 단계로.

문장은 화면 문구(i18n `summary.*`)의 자리표시자를 채워 만든다. 잠금은 조직 고정값을 반영한 실제 값으로 말한다.

Publish-effect summary and progress step: the policy document as plain sentences, the status as a step 1..4.
Sentences fill the `summary.*` screen strings; locks are described as effective (org-fixed) values.
"""

from collections.abc import Callable

from hub.fragment import LOCK_CHOICES, effective_locks

T = Callable[[str], str]


def policy_summary(doc: dict, org: dict, t: T) -> list[str]:
    """정책 문서 -> PC 에서 일어나는 일 문장 목록(모델·플러그인·MCP·에이전트·잠금 3개 순).

    Policy document -> sentences about what happens on PCs (models, plugins, MCP, agents, then the three locks).
    """
    lines = []
    models = doc.get("models")
    if models is None:
        lines.append(t("summary.models.unrestricted"))
    else:
        lines.append(t("summary.models.list").format(models=", ".join(models)))

    market = doc.get("marketplace") or {}
    plugins = doc.get("plugins") or []
    if not market:
        lines.append(t("summary.plugins.none"))
    else:
        fields = {"name": market.get("name", ""), "repo": market.get("repo", ""), "ref": market.get("ref") or "HEAD"}
        if plugins:
            lines.append(t("summary.plugins.install").format(plugins=", ".join(plugins), **fields))
        else:
            lines.append(t("summary.plugins.market_only").format(**fields))

    servers = doc.get("mcp_servers") or {}
    if servers:
        lines.append(t("summary.mcp.list").format(names=", ".join(servers)))
    else:
        lines.append(t("summary.mcp.none"))

    agents = doc.get("agents") or []
    if agents:
        lines.append(t("summary.agents.list").format(names=", ".join(agents)))
    else:
        lines.append(t("summary.agents.none"))

    locks = effective_locks(doc, org)
    for key in LOCK_CHOICES:
        lines.append(t(f"summary.lock.{key}.{locks[key]}"))
    return lines


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
