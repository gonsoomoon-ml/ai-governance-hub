"""팀 화면 요약 — '이 팀이 받는 것' 한 줄, 진행 단계(1~4), 상태·역할별 '지금 할 일' 키.

Team-page summaries: the 'what this team receives' line, the progress step (1..4), the 'what to do now' key.
"""

from conftest import CATALOG, policy

from hub.i18n import translator
from hub.summary import inhouse_counts, receives_summary, step_of, todo_key

t = translator("ko")


def test_receives_line_counts_what_the_published_copy_gives_pcs():
    assert receives_summary(policy(), CATALOG, t) == "모델 2 · 플러그인 2 · 사내 MCP 서버 1 · 사내 에이전트 1"
    assert inhouse_counts(policy(agents=["api-catalog", "ghost"]), CATALOG) == (1, 1)  # unknown names count as agents


def test_receives_line_says_all_models_when_unrestricted_and_zero_when_empty():
    doc = policy(models=None, marketplace=None, plugins=[], mcp_servers={}, agents=[])
    assert receives_summary(doc, CATALOG, t) == "모델 전체 · 플러그인 0 · 사내 MCP 서버 0 · 사내 에이전트 0"


def test_step_follows_the_draft_status():
    assert step_of(None, None) == 1
    assert step_of({"status": "draft"}, None) == 1
    assert step_of({"status": "rejected"}, {"version": 1}) == 1
    assert step_of({"status": "pending"}, None) == 3
    assert step_of({"status": "published"}, {"version": 1}) == 4
    assert step_of(None, {"version": 1}) == 4


def test_todo_key_follows_role_and_step():
    assert todo_key("leader", None, None) == "todo.leader.edit"
    assert todo_key("leader", {"status": "rejected"}, None) == "todo.leader.edit"
    assert todo_key("leader", {"status": "pending"}, None) == "todo.leader.pending"
    assert todo_key("leader", None, {"version": 1}) == "todo.leader.published"
    assert todo_key("admin", None, None) == "todo.admin.edit"
    assert todo_key("admin", {"status": "pending"}, None) == "todo.admin.pending"
    assert todo_key("admin", {"status": "published"}, {"version": 1}) == "todo.admin.published"
