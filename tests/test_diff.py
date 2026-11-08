"""policy_diff — 지금 PC 가 받는 값(배포본 또는 조직 기본 정책)과 초안의 항목별 전/후.

policy_diff: per-field before/after between what PCs receive now (published copy or org default policy) and the draft.
"""

from conftest import CATALOG, policy

from hub.diff import policy_diff
from hub.fragment import empty_policy
from hub.i18n import translator

t = translator("ko")
STRICT = {"plugin_sources": "company_only", "personal_customization": "block", "mcp": "managed_only"}


def rows_by_field(rows: list[dict]) -> dict:
    """행 목록 -> {field: (before, after)}.

    Rows -> {field: (before, after)}.
    """
    return {r["field"]: (r["before"], r["after"]) for r in rows}


def test_identical_documents_have_no_rows():
    assert policy_diff(policy(), policy(), t, CATALOG) == []


def test_changed_models_show_before_and_after_as_joined_lists():
    rows = policy_diff(policy(), policy(models=["claude-sonnet-5-5"]), t, CATALOG)
    assert [r["field"] for r in rows] == ["team.models"]
    assert rows[0]["before"] == "claude-sonnet-5-5, claude-haiku-5-5"
    assert rows[0]["after"] == "claude-sonnet-5-5"


def test_unrestricted_models_are_shown_with_the_screen_wording():
    rows = rows_by_field(policy_diff(policy(), policy(models=None), t, CATALOG))
    assert rows["team.models"][1] == t("team.models.unrestricted")


def test_the_repository_is_one_row_and_plugins_follow_the_form_order():
    """저장소는 '이름 — owner/repo @ ref' 한 줄(0.1.23), 그다음 자동 설치 플러그인.

    The repository is one row, 'name — owner/repo @ ref' (0.1.23), followed by the auto-installed plugins.
    """
    draft = policy(
        marketplace={"name": "example-extensions", "repo": "example/claude-extensions", "ref": "demo-2026-12"},
        plugins=["concise-response"],
    )
    rows = policy_diff(policy(), draft, t, CATALOG)
    assert [r["field"] for r in rows] == ["diff.marketplace", "team.plugins"]
    assert rows[0]["before"] == "example-extensions — example/claude-extensions @ demo-2026-11"
    assert rows[0]["after"] == "example-extensions — example/claude-extensions @ demo-2026-12"
    assert rows[1]["after"] == "concise-response"


def test_inhouse_entries_split_by_catalog_kind_and_locks_use_their_labels():
    """사내 항목은 카탈로그 종류로 MCP 서버 / 에이전트 두 줄, 잠금은 화면 문구.

    In-house entries split into MCP servers / agents by catalog kind; locks show their screen wording.
    """
    draft = policy(agents=["api-catalog"], locks={**STRICT, "plugin_sources": "company_and_official"})
    rows = rows_by_field(policy_diff(policy(), draft, t, CATALOG))
    assert rows["diff.agents"] == ("dev-guide-qa", "")
    assert "diff.mcp_inhouse" not in rows  # api-catalog stays allowed on both sides
    assert rows["lock.plugin_sources"] == (
        t("lock.plugin_sources.company_only"),
        t("lock.plugin_sources.company_and_official"),
    )
    assert "lock.mcp" not in rows


def test_names_missing_from_the_catalog_count_as_agents():
    rows = rows_by_field(policy_diff(policy(agents=[]), policy(agents=["mystery", "api-catalog"]), t, CATALOG))
    assert rows["diff.mcp_inhouse"] == ("", "api-catalog") and rows["diff.agents"] == ("", "mystery")


def test_org_fixed_locks_apply_to_both_sides_so_they_never_differ():
    before = policy(locks={**STRICT, "mcp": "allow_extra"})
    after = policy(locks=STRICT)
    assert policy_diff(before, after, t, CATALOG, {"mcp": "managed_only"}) == []
    assert [r["field"] for r in policy_diff(before, after, t, CATALOG)] == ["lock.mcp"]


def test_against_the_org_default_policy_the_baseline_is_what_pcs_receive_now():
    """배포본이 없을 때의 기준 = 조직 기본 정책: 모델 '제한 없음', 저장소·플러그인 없음(빈 문자열), 잠금은 그 값.

    Without a published copy the baseline is the org default policy: models 'unrestricted', no repository or
    plugins (empty strings), locks as set there.
    """
    default = {**empty_policy(), "locks": STRICT}
    rows = rows_by_field(policy_diff(default, policy(locks={**STRICT, "plugin_sources": "unrestricted"}), t, CATALOG))
    assert rows["team.models"][0] == t("team.models.unrestricted")
    assert rows["diff.marketplace"][0] == "" and rows["team.plugins"][0] == ""
    assert rows["lock.plugin_sources"] == (t("lock.plugin_sources.company_only"), t("lock.plugin_sources.unrestricted"))
    assert "lock.personal_customization" not in rows and "lock.mcp" not in rows
