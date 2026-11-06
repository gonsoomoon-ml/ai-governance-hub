"""어디에서 막히나 표와 진행 단계 — 정책 문서를 덱 4장의 표(대상 / 값 / 서버 / PC / 적용 시점)로, 상태를 1~4 단계로.

Enforcement table and progress step: the policy document as the deck's table (item / value / server / PC / when),
the status as a step 1..4.
"""

from conftest import policy

from hub.i18n import translator
from hub.summary import enforcement_rows, step_of

t = translator("ko")
ORG = {"locks_fixed": {}, "default_policy": {}}


def by_item(rows: list[dict]) -> dict:
    """행 목록 -> {item: row}.

    Rows -> {item: row}.
    """
    return {r["item"]: r for r in rows}


def test_full_policy_fills_every_row_of_the_deck_table():
    rows = by_item(enforcement_rows(policy(), ORG, t))
    assert list(rows) == ["models", "plugins", "mcp", "agents", "prompt"]

    assert rows["models"]["value"] == "claude-sonnet-5-5, claude-haiku-5-5"
    assert "AI 게이트웨이" in rows["models"]["server"] and rows["models"]["when"] == "즉시"

    assert "deck-design, infographic-design" in rows["plugins"]["value"]
    assert rows["plugins"]["server"] == "(서버 없음)" and "회사 저장소만" in rows["plugins"]["pc"]
    assert rows["plugins"]["when"] == "다음 세션"

    assert "deepwiki" in rows["mcp"]["value"] and "다른 서버" in rows["mcp"]["pc"]

    assert "dev-guide-qa, api-catalog" in rows["agents"]["value"]
    assert "도구 게이트웨이" in rows["agents"]["server"] and "tool-gateway" in rows["agents"]["pc"]

    assert "보안 프록시" in rows["prompt"]["server"] and rows["prompt"]["design_stage"] is True


def test_unrestricted_and_empty_values_are_said_plainly():
    rows = by_item(
        enforcement_rows(policy(models=None, marketplace=None, plugins=[], mcp_servers={}, agents=[]), ORG, t)
    )
    assert rows["models"]["value"] == "전체(게이트웨이 목록)"
    assert rows["plugins"]["value"] == "없음(설치 불가)"
    assert rows["mcp"]["value"] == "없음"
    assert rows["agents"]["value"] == "없음"


def test_org_fixed_locks_win_in_the_pc_column():
    org = {"locks_fixed": {"plugin_sources": "company_and_official"}, "default_policy": {}}
    rows = by_item(enforcement_rows(policy(), org, t))
    assert "공식 마켓" in rows["plugins"]["pc"] and "회사 저장소만" not in rows["plugins"]["pc"]


def test_step_follows_the_draft_status():
    assert step_of(None, None) == 1
    assert step_of({"status": "draft"}, None) == 1
    assert step_of({"status": "rejected"}, {"version": 1}) == 1
    assert step_of({"status": "pending"}, None) == 3
    assert step_of({"status": "published"}, {"version": 1}) == 4
    assert step_of(None, {"version": 1}) == 4
