"""발행본과 초안의 차이 — 승인자가 "무엇이 바뀌는지" 를 항목별 전/후로 보는 표.

정책 문서(models·marketplace·plugins·mcp_servers·agents·locks)를 화면 폼 순서대로 비교해
달라진 항목만 돌려준다. 값은 화면 문구(번역기 t)로 바꾼 표시 문자열이다 — 조각(fragment)이 아니라
문서를 비교하므로 사람이 입력한 단위 그대로 읽힌다.

Difference between the published copy and the draft: per-field before/after rows for the approver.
Compares the policy documents (models, marketplace, plugins, mcp_servers, agents, locks) in form
order and returns only the fields that changed, rendered as display strings through the
translator t. Documents, not fragments, so rows read in the units people typed.
"""

from collections.abc import Callable

from hub.fragment import LOCK_CHOICES

T = Callable[[str], str]


def policy_diff(published: dict | None, draft: dict, t: T) -> list[dict]:
    """발행본(없으면 None)과 초안을 비교해 바뀐 항목의 {field, before, after} 목록.

    Compare the published document (None if never published) with the draft and return
    {field, before, after} rows for the fields that differ, in form order.
    """
    before_doc = published or {}
    rows = []
    for field, before, after in _pairs(before_doc, draft, t):
        if before != after:
            rows.append({"field": field, "before": before, "after": after})
    return rows


def _pairs(a: dict, b: dict, t: T):
    """두 문서의 (항목 키, a 표시값, b 표시값) 를 폼 순서대로 낸다.

    Yield (field key, display of a, display of b) in form order.
    """
    yield "team.models", _models(a, t), _models(b, t)
    for part in ("name", "repo", "ref"):
        yield f"team.marketplace.{part}", _market(a, part), _market(b, part)
    yield "team.plugins", _joined(a.get("plugins")), _joined(b.get("plugins"))
    yield "team.mcp_servers", _mcp(a), _mcp(b)
    yield "diff.inhouse", _joined(a.get("agents")), _joined(b.get("agents"))
    for key in LOCK_CHOICES:
        yield f"lock.{key}", _lock(a, key, t), _lock(b, key, t)


def _models(doc: dict, t: T) -> str:
    """모델 목록 표시: 키가 없으면 빈 문자열, None 이면 '제한 없음' 문구.

    Model list display: empty when absent, the 'unrestricted' wording when None.
    """
    if "models" not in doc:
        return ""
    models = doc.get("models")
    return t("team.models.unrestricted") if models is None else ", ".join(models)


def _market(doc: dict, part: str) -> str:
    """마켓 항목 하나의 표시값.

    Display of one marketplace field.
    """
    market = doc.get("marketplace") or {}
    return str(market.get(part) or "")


def _joined(values) -> str:
    """목록을 ', ' 로 잇는다(없으면 빈 문자열).

    Join a list with ', ' (empty when missing).
    """
    return ", ".join(values or [])


def _mcp(doc: dict) -> str:
    """MCP 서버 표시: '이름 주소' 를 ', ' 로 잇는다.

    MCP servers display: 'name url' entries joined with ', '.
    """
    servers = doc.get("mcp_servers") or {}
    return ", ".join(f"{name} {spec.get('url', '')}" for name, spec in servers.items())


def _lock(doc: dict, key: str, t: T) -> str:
    """잠금 값의 화면 문구(없으면 빈 문자열).

    Screen wording of a lock value (empty when missing).
    """
    value = (doc.get("locks") or {}).get(key)
    return t(f"lock.{key}.{value}") if value else ""
