"""발행 효과 요약과 진행 단계 — 정책 문서를 "PC 에서 일어나는 일" 문장으로, 상태를 1~4 단계로.

Publish-effect summary and progress step: the policy document as plain sentences, the status as a step 1..4.
"""

from conftest import policy

from hub.i18n import translator
from hub.summary import policy_summary, step_of

t = translator("ko")
ORG = {"locks_fixed": {}, "default_policy": {}}


def test_full_policy_reads_as_plain_sentences():
    lines = policy_summary(policy(), ORG, t)
    text = "\n".join(lines)
    assert "claude-sonnet-5-5, claude-haiku-5-5" in text and "다른 모델" in text
    assert "deck-design, infographic-design" in text and "자동 설치" in text
    assert "deepwiki" in text and "/mcp" in text
    assert "dev-guide-qa, api-catalog" in text and "tool-gateway" in text
    assert "회사 저장소만" in text and "개인 스킬·훅" in text and "다른 서버를 추가할 수 없" in text


def test_unrestricted_models_and_no_marketplace_are_said_plainly():
    lines = policy_summary(policy(models=None, marketplace=None, plugins=[], mcp_servers={}, agents=[]), ORG, t)
    text = "\n".join(lines)
    assert "전체 목록" in text
    assert "회사 저장소가 없어" in text
    assert "등록되는 서버가 없습니다" in text
    assert "사내 에이전트: 없음" in text


def test_org_fixed_locks_win_in_the_summary():
    org = {"locks_fixed": {"plugin_sources": "company_and_official"}, "default_policy": {}}
    text = "\n".join(policy_summary(policy(), org, t))
    assert "공식 마켓" in text and "회사 저장소만" not in text


def test_step_follows_the_draft_status():
    assert step_of(None, None) == 1
    assert step_of({"status": "draft"}, None) == 1
    assert step_of({"status": "rejected"}, {"version": 1}) == 1
    assert step_of({"status": "pending"}, None) == 3
    assert step_of({"status": "published"}, {"version": 1}) == 4
    assert step_of(None, {"version": 1}) == 4
