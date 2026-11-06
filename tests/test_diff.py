"""policy_diff — 발행본과 초안의 항목별 전/후 (승인자가 무엇이 바뀌는지 보는 표).

policy_diff: per-field before/after between the published and draft documents (what the approver sees).
"""

from conftest import policy

from hub.diff import policy_diff
from hub.i18n import translator

t = translator("ko")


def rows_by_field(rows: list[dict]) -> dict:
    """행 목록 -> {field: (before, after)}.

    Rows -> {field: (before, after)}.
    """
    return {r["field"]: (r["before"], r["after"]) for r in rows}


def test_identical_documents_have_no_rows():
    assert policy_diff(policy(), policy(), t) == []


def test_changed_models_show_before_and_after_as_joined_lists():
    rows = policy_diff(policy(), policy(models=["claude-sonnet-5-5"]), t)
    assert [r["field"] for r in rows] == ["team.models"]
    assert rows[0]["before"] == "claude-sonnet-5-5, claude-haiku-5-5"
    assert rows[0]["after"] == "claude-sonnet-5-5"


def test_unrestricted_models_are_shown_with_the_screen_wording():
    rows = rows_by_field(policy_diff(policy(), policy(models=None), t))
    assert rows["team.models"][1] == t("team.models.unrestricted")


def test_marketplace_and_plugin_changes_follow_the_form_order():
    draft = policy(
        marketplace={"name": "example-extensions", "repo": "example/claude-extensions", "ref": "demo-2026-12"},
        plugins=["concise-response"],
    )
    rows = policy_diff(policy(), draft, t)
    assert [r["field"] for r in rows] == ["team.marketplace.ref", "team.plugins"]
    assert rows[0]["before"] == "demo-2026-11" and rows[0]["after"] == "demo-2026-12"
    assert rows[1]["after"] == "concise-response"


def test_mcp_agents_and_locks_are_shown_with_labels():
    draft = policy(
        mcp_servers={
            "deepwiki": {"url": "https://mcp.deepwiki.com/mcp"},
            "wiki": {"url": "https://wiki.example.com/mcp"},
        },
        agents=["api-catalog"],
        locks={"plugin_sources": "company_and_official", "personal_customization": "block", "mcp": "managed_only"},
    )
    rows = rows_by_field(policy_diff(policy(), draft, t))
    assert rows["team.mcp_servers"][1] == "deepwiki https://mcp.deepwiki.com/mcp, wiki https://wiki.example.com/mcp"
    assert rows["team.agents"] == ("dev-guide-qa, api-catalog", "api-catalog")
    assert rows["lock.plugin_sources"] == (
        t("lock.plugin_sources.company_only"),
        t("lock.plugin_sources.company_and_official"),
    )
    assert "lock.mcp" not in rows


def test_without_a_published_copy_every_filled_field_is_new():
    rows = policy_diff(None, policy(), t)
    fields = [r["field"] for r in rows]
    assert fields[:2] == ["team.models", "team.marketplace.name"]
    assert all(r["before"] == "" for r in rows)
    assert "lock.mcp" in fields
