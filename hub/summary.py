"""어디에서 막히나 표와 진행 단계 — 정책 문서를 덱 4장의 표로, 상태를 1~4 단계로.

표의 행은 덱 4장과 같다: 모델 · 플러그인·스킬 · MCP 서버 · 사내 에이전트 · 프롬프트(보안 프록시, 설계 단계).
열은 이 팀의 값 / 서버에서 막는 곳 / 직원 PC 에서 막는 곳 / 적용 시점. 잠금은 조직 고정을 반영한 실제 값으로 말한다.

Enforcement table and progress step: the policy document as the deck's slide-4 table, the status as a step 1..4.
Rows match the deck: models, plugins/skills, MCP servers, in-house agents, prompts (security proxy, design stage).
Columns: this team's value / enforced on the server / enforced on the PC / when. Locks are effective (org-fixed) values.
"""

from collections.abc import Callable

from hub.fragment import effective_locks

T = Callable[[str], str]


def enforcement_rows(doc: dict, org: dict, t: T) -> list[dict]:
    """정책 문서 -> 덱 4장 표의 행 목록 {item, label, value, server, pc, when, design_stage}.

    Policy document -> rows of the deck's table {item, label, value, server, pc, when, design_stage}.
    """
    locks = effective_locks(doc, org)
    return [
        _models_row(doc, t),
        _plugins_row(doc, locks, t),
        _mcp_row(doc, locks, t),
        _agents_row(doc, t),
        {
            "item": "prompt",
            "label": t("enf.item.prompt"),
            "value": t("enf.prompt.value"),
            "server": t("enf.prompt.server"),
            "pc": t("enf.prompt.pc"),
            "when": t("enf.prompt.when"),
            "design_stage": True,
        },
    ]


def _models_row(doc: dict, t: T) -> dict:
    """모델 행: 허용 목록(없으면 전체), AI 게이트웨이가 즉시 막음.

    Models row: the allowed list (or all), enforced by the AI gateway immediately.
    """
    models = doc.get("models")
    return {
        "item": "models",
        "label": t("enf.item.models"),
        "value": t("enf.models.all") if models is None else ", ".join(models),
        "server": t("enf.models.server"),
        "pc": t("enf.models.pc"),
        "when": t("enf.when.now"),
        "design_stage": False,
    }


def _plugins_row(doc: dict, locks: dict, t: T) -> dict:
    """플러그인·스킬 행: 저장소·플러그인 값, 서버 없음, PC 가 잠금으로 막음, 다음 세션.

    Plugins row: marketplace and plugin values, no server, enforced on the PC by the locks, next session.
    """
    market = doc.get("marketplace") or {}
    plugins = doc.get("plugins") or []
    if not market:
        value = t("enf.plugins.none")
    else:
        fields = {"name": market.get("name", ""), "ref": market.get("ref") or "HEAD"}
        value = (
            t("enf.plugins.value").format(plugins=", ".join(plugins), **fields)
            if plugins
            else t("enf.plugins.market_only").format(**fields)
        )
    return {
        "item": "plugins",
        "label": t("enf.item.plugins"),
        "value": value,
        "server": t("enf.plugins.server"),
        "pc": t("enf.plugins.pc").format(
            sources=t("enf.lock.plugin_sources." + locks["plugin_sources"]),
            personal=t("enf.lock.personal_customization." + locks["personal_customization"]),
        ),
        "when": t("enf.when.next"),
        "design_stage": False,
    }


def _mcp_row(doc: dict, locks: dict, t: T) -> dict:
    """MCP 서버 행: 관리형 목록, 서버 없음(PC 허용 목록), 직원 추가 가능 여부, 다음 세션.

    MCP row: the managed list, no server (PC allow-list), whether employees may add servers, next session.
    """
    servers = doc.get("mcp_servers") or {}
    return {
        "item": "mcp",
        "label": t("enf.item.mcp"),
        "value": ", ".join(servers) if servers else t("enf.mcp.none"),
        "server": t("enf.mcp.server"),
        "pc": t("enf.mcp.pc").format(extra=t("enf.lock.mcp." + locks["mcp"])),
        "when": t("enf.when.next"),
        "design_stage": False,
    }


def _agents_row(doc: dict, t: T) -> dict:
    """사내 에이전트 행: 허용 목록, 도구 게이트웨이가 호출마다 막음, PC 는 연결 고정 + 차단 목록.

    Agents row: the allowed list, enforced per call by the tool gateway; the PC pins the connection and denies the rest.
    """
    agents = doc.get("agents") or []
    return {
        "item": "agents",
        "label": t("enf.item.agents"),
        "value": ", ".join(agents) if agents else t("enf.agents.none"),
        "server": t("enf.agents.server"),
        "pc": t("enf.agents.pc"),
        "when": t("enf.when.both"),
        "design_stage": False,
    }


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
