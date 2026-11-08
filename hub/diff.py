"""지금 PC 가 받는 값과 초안의 차이 — 승인자가 "무엇이 바뀌는지" 를 항목별 전/후로 보는 표.

기준(current)은 배포본이 있으면 그 문서, 없으면 조직 기본 정책(안전망) — 어느 쪽이든 지금 PC 가 받는 값이다.
정책 문서(models·marketplace·plugins·agents·locks)를 화면 폼 순서대로 비교해 달라진 항목만 돌려준다.
값은 화면 문구(번역기 t)로 바꾼 표시 문자열이다 — 조각(fragment)이 아니라 문서를 비교하므로 사람이 입력한
단위 그대로 읽힌다. 사내 항목은 카탈로그의 종류로 MCP 서버 / 에이전트 두 줄로 나누고(카탈로그에 없는 이름은
에이전트), 잠금은 조직 고정값을 적용한 실제 값으로 비교한다.

Difference between what PCs receive now and the draft: per-field before/after rows for the approver.
The baseline (current) is the published document when there is one, else the organization default policy
(safety net); either way it is what PCs receive now. Compares the policy documents (models, marketplace,
plugins, agents, locks) in form order and returns only the fields that changed, rendered as display strings
through the translator t. Documents, not fragments, so rows read in the units people typed. In-house entries
split into MCP servers / agents by catalog kind (unknown names count as agents); locks compare as effective
values with the org-fixed ones applied.
"""

from collections.abc import Callable, Iterable

from hub.fragment import LOCK_CHOICES

T = Callable[[str], str]


def policy_diff(
    current: dict | None, draft: dict, t: T, catalog: Iterable[dict] = (), locks_fixed: dict | None = None
) -> list[dict]:
    """지금 PC 가 받는 문서(current; None 이면 빈 문서)와 초안을 비교해 바뀐 항목의 {field, before, after} 목록.

    Compare the document PCs receive now (current; None means an empty document) with the draft and return
    {field, before, after} rows for the fields that differ, in form order.
    """
    kinds = {a["name"]: a.get("kind") for a in catalog}
    fixed = locks_fixed or {}
    rows = []
    for field, before, after in _pairs(current or {}, draft, t, kinds, fixed):
        if before != after:
            rows.append({"field": field, "before": before, "after": after})
    return rows


def _pairs(a: dict, b: dict, t: T, kinds: dict, fixed: dict):
    """두 문서의 (항목 키, a 표시값, b 표시값) 를 폼 순서대로 낸다.

    Yield (field key, display of a, display of b) in form order.
    """
    yield "team.models", _models(a, t), _models(b, t)
    yield "diff.marketplace", _market(a), _market(b)
    yield "team.plugins", _joined(a.get("plugins")), _joined(b.get("plugins"))
    yield "diff.mcp_inhouse", _inhouse(a, kinds, mcp=True), _inhouse(b, kinds, mcp=True)
    yield "diff.agents", _inhouse(a, kinds, mcp=False), _inhouse(b, kinds, mcp=False)
    for key in LOCK_CHOICES:
        yield f"lock.{key}", _lock(a, key, t, fixed), _lock(b, key, t, fixed)


def _models(doc: dict, t: T) -> str:
    """모델 목록 표시: 키가 없으면 빈 문자열, None 이면 '제한 없음' 문구.

    Model list display: empty when absent, the 'unrestricted' wording when None.
    """
    if "models" not in doc:
        return ""
    models = doc.get("models")
    return t("team.models.unrestricted") if models is None else ", ".join(models)


def _market(doc: dict) -> str:
    """저장소 한 줄: '이름 — owner/repo @ ref'(ref 없으면 HEAD), 저장소가 없으면 빈 문자열.

    The repository on one line: 'name — owner/repo @ ref' (HEAD without a ref); empty when there is none.
    """
    market = doc.get("marketplace") or {}
    if not (market.get("name") or market.get("repo")):
        return ""
    return f"{market.get('name') or ''} — {market.get('repo') or ''} @ {market.get('ref') or 'HEAD'}"


def _joined(values) -> str:
    """목록을 ', ' 로 잇는다(없으면 빈 문자열).

    Join a list with ', ' (empty when missing).
    """
    return ", ".join(values or [])


def _inhouse(doc: dict, kinds: dict, *, mcp: bool) -> str:
    """허용한 사내 항목 가운데 MCP 서버(mcp=True) 또는 에이전트(카탈로그에 없는 이름 포함) 를 ', ' 로.

    The allowed in-house entries that are MCP servers (mcp=True) or agents (incl. names missing from the catalog).
    """
    return ", ".join(name for name in doc.get("agents") or [] if (kinds.get(name) == "MCP") is mcp)


def _lock(doc: dict, key: str, t: T, fixed: dict) -> str:
    """잠금의 실제 값(조직 고정이 우선) 의 화면 문구(값이 없으면 빈 문자열).

    Screen wording of the effective lock value (org-fixed wins); empty when there is no value.
    """
    value = fixed.get(key) or (doc.get("locks") or {}).get(key)
    return t(f"lock.{key}.{value}") if value else ""
