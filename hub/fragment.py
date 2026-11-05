"""팀 정책 문서 -> Claude Code managedSettings 조각.

정책 문서는 관리자가 화면에서 다루는 모양(마켓·플러그인·MCP 서버·에이전트·잠금 세 가지)이고,
조각은 Claude Code 가 읽는 키다. 모델은 여기서 절대 내지 않는다 — 모델은 발행 때 admin-api 에
써 넣고, 게이트웨이의 models 조각만이 PC 에 닿는다. 그래서 두 조각은 키가 겹치지 않는다.

조각 안의 null 은 "기본값의 그 키를 지워라" 다(policy-helper merge 규칙). 엄격한 기본값
(strictKnownMarketplaces: []) 을 팀 정책이 풀어 줄 때 쓴다.

Team policy document -> Claude Code managedSettings fragment.

The policy document is the shape administrators edit (marketplace, plugins, MCP servers,
agents, three locks); the fragment is what Claude Code reads. Models are never emitted here:
they are written to admin-api at publish time and only the gateway's models fragment reaches
the PC, so the two fragments never share a key.

A null in the fragment means "delete that key from the baseline" (policy-helper merge rule),
used when a team policy lifts a strict default such as strictKnownMarketplaces: [].
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MODEL_KEYS = frozenset({"availableModels", "enforceAvailableModels", "model"})
FRAGMENT_KEYS = frozenset(
    {
        "disableSideloadFlags",
        "strictPluginOnlyCustomization",
        "allowManagedMcpServersOnly",
        "strictKnownMarketplaces",
        "extraKnownMarketplaces",
        "enabledPlugins",
        "managedMcpServers",
        "permissions",
    }
)
POLICY_KEYS = ("models", "marketplace", "plugins", "mcp_servers", "agents", "locks")
LOCK_CHOICES = {
    "plugin_sources": ("company_only", "company_and_official", "unrestricted"),
    "personal_customization": ("block", "allow"),
    "mcp": ("managed_only", "allow_extra"),
}
STRICT_DEFAULTS = {"plugin_sources": "company_only", "personal_customization": "block", "mcp": "managed_only"}
OFFICIAL_MARKETPLACE = {"source": "github", "repo": "anthropics/claude-plugins-official"}
AGENT_SERVER = "tool-gateway"  # the one managed MCP entry that fronts every in-house agent

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def empty_policy() -> dict:
    """아무것도 허용하지 않는 정책: 모델 제한 없음, 마켓·플러그인·MCP·에이전트 없음, 잠금은 엄격.

    A policy that allows nothing: no model restriction, no marketplace, plugins, MCP servers or
    agents, strict locks.
    """
    return {
        "models": None,
        "marketplace": None,
        "plugins": [],
        "mcp_servers": {},
        "agents": [],
        "locks": dict(STRICT_DEFAULTS),
    }


def effective_locks(doc: dict, org: dict) -> dict:
    """팀 값 위에 조직 고정 항목을 덮고, 빠진 항목은 엄격한 기본값으로 채운 잠금 세 가지.

    The three locks: team values, overridden by org-fixed values, missing ones filled with the
    strict defaults.
    """
    locks = {**STRICT_DEFAULTS, **{k: v for k, v in (doc.get("locks") or {}).items() if v}}
    for key, fixed in (org.get("locks_fixed") or {}).items():
        if fixed:
            locks[key] = fixed
    return locks


def validate_policy(doc: Any, org: dict) -> list[str]:
    """저장 전 검증. 잘못된 키 하나가 모든 PC 의 Claude Code 시작을 막으므로 모양을 엄격히 본다.

    Validation before saving. One bad key stops Claude Code on every PC, so the shape is
    checked strictly.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["policy must be an object"]
    for key in doc:
        if key not in POLICY_KEYS:
            errors.append(f"unknown key: {key}")

    models = doc.get("models")
    if models is not None and (not isinstance(models, list) or any(not _nonempty_str(m) for m in models)):
        errors.append("models must be null or a list of model aliases")

    marketplace = doc.get("marketplace")
    if marketplace is not None:
        errors.extend(_validate_marketplace(marketplace))

    plugins = doc.get("plugins") or []
    if not isinstance(plugins, list) or any(not (_nonempty_str(p) and _NAME.match(p)) for p in plugins):
        errors.append("plugins must be a list of plugin names (lowercase, digits, -)")
    elif plugins and marketplace is None:
        errors.append("plugins need a marketplace to come from")

    servers = doc.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        errors.append("mcp_servers must be an object")
    else:
        for name, server in servers.items():
            errors.extend(_validate_server(name, server))

    agents = doc.get("agents") or []
    if not isinstance(agents, list) or any(not _nonempty_str(a) for a in agents):
        errors.append("agents must be a list of agent names")

    locks = doc.get("locks") or {}
    if not isinstance(locks, dict):
        errors.append("locks must be an object")
    else:
        fixed = org.get("locks_fixed") or {}
        for key, value in locks.items():
            if key not in LOCK_CHOICES:
                errors.append(f"locks: unknown lock {key}")
            elif value not in (None, *LOCK_CHOICES[key]):
                errors.append(f"locks.{key}: must be one of {', '.join(LOCK_CHOICES[key])}")
            elif value and fixed.get(key) and value != fixed[key]:
                errors.append(f"locks.{key}: fixed by the organization to {fixed[key]}")
    return errors


def build_fragment(doc: dict, org: dict, catalog: list[dict], *, gateway_url: str, oauth: dict | None) -> dict:
    """정책 문서를 Claude Code 조각으로 바꾼다(모델 키 없음).

    - 잠금 셋 -> disableSideloadFlags / strictPluginOnlyCustomization / allowManagedMcpServersOnly /
      strictKnownMarketplaces (제한 없음이면 null 로 기본값의 키를 지움)
    - 마켓 -> extraKnownMarketplaces(태그 고정) · 플러그인 -> enabledPlugins
    - MCP 서버 + 에이전트 -> managedMcpServers (에이전트가 하나라도 있으면 tool-gateway 항목 + oauth)
    - 카탈로그에 있지만 허용되지 않은 에이전트의 도구 -> permissions.deny (정확한 이름)

    Turn a policy document into the Claude Code fragment (no model keys).

    - three locks -> disableSideloadFlags / strictPluginOnlyCustomization / allowManagedMcpServersOnly /
      strictKnownMarketplaces (null deletes the baseline key when unrestricted)
    - marketplace -> extraKnownMarketplaces (tag pinned) · plugins -> enabledPlugins
    - MCP servers + agents -> managedMcpServers (any agent -> the tool-gateway entry with oauth)
    - tools of catalog agents the team may not use -> permissions.deny (exact names)
    """
    locks = effective_locks(doc, org)
    fragment: dict = {
        "disableSideloadFlags": locks["plugin_sources"] != "unrestricted",
        "strictPluginOnlyCustomization": locks["personal_customization"] == "block",
        "allowManagedMcpServersOnly": locks["mcp"] == "managed_only",
    }

    marketplace = doc.get("marketplace")
    company = _marketplace_source(marketplace) if marketplace else None
    if locks["plugin_sources"] == "unrestricted":
        fragment["strictKnownMarketplaces"] = None
    else:
        known = [company] if company else []
        if locks["plugin_sources"] == "company_and_official":
            known.append(dict(OFFICIAL_MARKETPLACE))
        fragment["strictKnownMarketplaces"] = known
    if company:
        fragment["extraKnownMarketplaces"] = {marketplace["name"]: {"source": company}}
        plugins = doc.get("plugins") or []
        if plugins:
            fragment["enabledPlugins"] = {f"{p}@{marketplace['name']}": True for p in plugins}

    servers = {name: {"type": "http", "url": server["url"]} for name, server in (doc.get("mcp_servers") or {}).items()}
    agents = [a for a in (doc.get("agents") or [])]
    if agents and gateway_url:
        entry: dict = {"type": "http", "url": gateway_url}
        if oauth:
            entry["oauth"] = {
                "clientId": oauth["client_id"],
                "callbackPort": int(oauth["callback_port"]),
                "authServerMetadataUrl": oauth["metadata_url"],
                "scopes": oauth["scopes"],
            }
        servers[AGENT_SERVER] = entry
        deny = agent_deny_list(agents, catalog)
        if deny:
            fragment["permissions"] = {"deny": deny}
    fragment["managedMcpServers"] = servers
    return fragment


def agent_deny_list(allowed: list[str], catalog: list[dict]) -> list[str]:
    """허용 목록 밖 카탈로그 에이전트의 도구 이름(mcp__tool-gateway__<agent>___<tool>) 전부.

    서버가 막는 자리(Cedar)가 생기면 이 함수 하나만 바꾸면 된다.

    Every tool name (mcp__tool-gateway__<agent>___<tool>) of catalog agents outside the allowed
    list. When server-side enforcement (Cedar) arrives, this one function is what changes.
    """
    deny: list[str] = []
    for agent in catalog:
        if agent["name"] in allowed:
            continue
        for tool in agent.get("tools") or []:
            deny.append(f"mcp__{AGENT_SERVER}__{agent['name']}___{tool}")
    return deny


def version_of(fragment: dict) -> str:
    """조각 내용의 sha256 앞 16자. 키 순서와 무관하다.

    First 16 hex chars of the fragment's sha256, independent of key order.
    """
    canonical = json.dumps(fragment, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _marketplace_source(marketplace: dict) -> dict:
    """정책의 마켓 -> Claude Code 의 github 소스 객체(repo, 선택 ref·path).

    Policy marketplace -> Claude Code github source object (repo, optional ref and path).
    """
    source = {"source": "github", "repo": marketplace["repo"]}
    for key in ("ref", "path"):
        if marketplace.get(key):
            source[key] = marketplace[key]
    return source


def _validate_marketplace(marketplace: Any) -> list[str]:
    """마켓 객체 검증: name·repo 필수, ref·path 선택, 그 밖의 키 거부.

    Marketplace object validation: name and repo required, ref and path optional, other keys rejected.
    """
    if not isinstance(marketplace, dict):
        return ["marketplace must be null or an object"]
    errors = [f"marketplace: unknown key {k}" for k in marketplace if k not in ("name", "repo", "ref", "path")]
    if not (_nonempty_str(marketplace.get("name")) and _NAME.match(marketplace["name"])):
        errors.append("marketplace.name must be lowercase letters, digits, - or _")
    if not (_nonempty_str(marketplace.get("repo")) and _REPO.match(marketplace["repo"])):
        errors.append("marketplace.repo must look like owner/repo")
    for key in ("ref", "path"):
        if marketplace.get(key) is not None and not _nonempty_str(marketplace[key]):
            errors.append(f"marketplace.{key} must be a string")
    return errors


def _validate_server(name: Any, server: Any) -> list[str]:
    """관리형 MCP 서버 항목 검증: 이름 규칙, https URL, tool-gateway 이름 예약.

    Managed MCP server entry validation: name rule, https URL, the tool-gateway name reserved.
    """
    errors: list[str] = []
    if not (isinstance(name, str) and _NAME.match(name)):
        errors.append(f"mcp_servers: bad server name {name!r} (lowercase letters, digits, - or _)")
    elif name == AGENT_SERVER:
        errors.append(f"mcp_servers: {AGENT_SERVER} is reserved for in-house agents")
    if not isinstance(server, dict):
        return errors + [f"mcp_servers.{name}: must be an object"]
    errors.extend(f"mcp_servers.{name}: unknown key {k}" for k in server if k not in ("url", "type"))
    url = server.get("url")
    if not (_nonempty_str(url) and url.startswith("https://")):
        errors.append(f"mcp_servers.{name}: url must start with https:// (managedMcpServers accepts https only)")
    if server.get("type") not in (None, "http"):
        errors.append(f"mcp_servers.{name}: type must be http")
    return errors


def _nonempty_str(value: Any) -> bool:
    """비어 있지 않은 문자열인가.

    Is this a non-empty string.
    """
    return isinstance(value, str) and value.strip() != ""
