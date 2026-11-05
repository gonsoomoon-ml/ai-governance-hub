"""dev-guide-qa — in-house AI agent (Strands) served as ONE MCP tool for AgentCore Runtime (MCP protocol).

Tool `ask(question, context="", session_id="")` runs a Strands Agent that answers questions about the
company's developer guide and, when `context` (code / diff) is given, applies the rules to it. The agent
has its own tools (list_sections / read_guide) over the bundled guide (data/dev-guide.md), so the runtime
log shows tool calls -> reasoning -> answer. Per-session conversation is kept in memory by session_id
(the AgentCore Gateway MCP session keeps the same runtime session).

References:
  - AWS devguide runtime-mcp (FastMCP on 0.0.0.0:8000/mcp, stateless streamable HTTP)
  - gonsoomoon-ml/aiops-multi-agent-workshop agents/incident/shared/agent.py (Strands Agent + tools pattern)
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from strands import Agent, tool
from strands.models import BedrockModel

GUIDE = Path(__file__).resolve().parent / "data" / "dev-guide.md"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-5-5")
REGION = os.environ.get("AWS_REGION", "us-west-2")

mcp = FastMCP("dev-guide-qa", host="0.0.0.0", port=8000, stateless_http=True)
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dev-guide-qa")  # stdout -> CloudWatch /aws/bedrock-agentcore/runtimes/<id>-DEFAULT


def _sections() -> dict[str, str]:
    """내장 가이드 markdown 을 `## ` 제목 단위로 잘라 {절 제목: 본문} 으로 돌려준다(호출마다 다시 읽음).

    Split the bundled guide markdown at `## ` headings and return {section title: body} (re-read on every call).
    """
    text = GUIDE.read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.splitlines()[0].strip(): p for p in parts}


@tool
def list_sections() -> list[str]:
    """사내 개발 가이드의 절 제목을 나열한다.

    List the section titles of the company developer guide.
    """
    log.info("[agent tool] list_sections()")
    return list(_sections())


@tool
def read_guide(section: str) -> str:
    """사내 개발 가이드의 절 하나를 제목(또는 제목에 포함된 키워드)으로 읽는다.

    Read one section of the company developer guide by title (or a keyword contained in the title).
    """
    log.info("[agent tool] read_guide(section=%r)", section)
    secs = _sections()
    for title, body in secs.items():
        if section.strip() in title:
            return body
    return f"no such section; available: {list(secs)}"


SYSTEM = (
    "당신은 사내 개발 가이드 담당 에이전트입니다. 답은 반드시 가이드 본문(read_guide)에 근거하고, "
    "근거 절 번호와 제목을 함께 적습니다. context 에 코드나 diff 가 오면 가이드 규정에 비추어 위반 항목과 "
    "고칠 점을 조목조목 적습니다. 가이드에 없는 내용은 '가이드에 없음' 이라고 말합니다. 한국어로 간결하게."
)
_agents: dict[str, Agent] = {}


def _agent(session_id: str) -> Agent:
    """session_id 별 Strands 에이전트를 돌려준다. session_id 가 비어 있으면 매번 새 에이전트(단발),
    있으면 재사용해 대화를 이어 간다.

    Return the Strands agent for session_id. An empty session_id gets a fresh agent each time (one-shot);
    a given one is reused so the conversation continues.
    """
    key = session_id or "default"
    if key not in _agents or not session_id:
        _agents[key] = Agent(model=BedrockModel(model_id=MODEL_ID, region_name=REGION), system_prompt=SYSTEM,
                             tools=[list_sections, read_guide], callback_handler=None)
    return _agents[key]


@mcp.tool()
def ask(question: str, context: str = "", session_id: str = "") -> str:
    """사내 개발 가이드 에이전트에게 묻는다. `question`: 알고 싶거나 확인할 내용.
    `context`(선택): 가이드에 비추어 검사할 코드나 diff. `session_id`(선택): 호출 사이에 대화를 이어 간다.

    Ask the in-house developer-guide agent. `question`: what you want to know or check.
    `context` (optional): code or a diff to check against the guide. `session_id` (optional): keep a
    conversation across calls.
    """
    prompt = question if not context else f"{question}\n\n[context]\n{context}"
    log.info("[ask] session=%r context_chars=%d question=%r", session_id, len(context), question[:200])
    t0 = time.monotonic()
    result = _agent(session_id)(prompt)
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}
    log.info("[ask] done in %.1fs model=%s usage=%s", time.monotonic() - t0, MODEL_ID, dict(usage))
    return str(result)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
