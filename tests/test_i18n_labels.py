"""화면 라벨 — 감사 행위명·레지스트리 상태처럼 원문이 그대로 보이던 값에 두 언어 문구가 있는지.

Screen labels: every audit action and registry status has a string in both languages (no raw values on screen).
"""

import pytest

from hub.i18n import STRINGS, translator

AUDIT_ACTIONS = [
    "team_added",
    "team_removed",
    "save",
    "submit",
    "approve",
    "reject",
    "publish",
    "org_saved",
    "agents_refreshed",
    "agent_approved",
    "agent_rejected",
]
REGISTRY_STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED"]


@pytest.mark.parametrize("lang", sorted(STRINGS))
@pytest.mark.parametrize("action", AUDIT_ACTIONS)
def test_every_audit_action_has_a_label(lang, action):
    t = translator(lang)
    assert t("audit.action." + action) != "audit.action." + action


@pytest.mark.parametrize("lang", sorted(STRINGS))
@pytest.mark.parametrize("status", REGISTRY_STATUSES)
def test_every_registry_status_has_a_label(lang, status):
    t = translator(lang)
    assert t("agents.status." + status) != "agents.status." + status


def test_both_languages_have_the_same_keys():
    assert set(STRINGS["ko"]) == set(STRINGS["en"])
