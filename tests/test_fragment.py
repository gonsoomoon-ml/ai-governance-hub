"""정책 문서 -> Claude Code managedSettings 조각. 모델 키는 절대 내지 않는다.

Policy document -> Claude Code managedSettings fragment. Never emits model keys.
"""

from __future__ import annotations

from conftest import CATALOG, GATEWAY_URL, policy

from hub.fragment import (
    FRAGMENT_KEYS,
    MODEL_KEYS,
    STRICT_DEFAULTS,
    build_fragment,
    empty_policy,
    validate_policy,
    version_of,
)

ORG = {"locks_fixed": {"plugin_sources": None, "personal_customization": None, "mcp": None}}
OAUTH = {
    "client_id": "example-client-id",
    "callback_port": 8094,
    "metadata_url": "https://idp.example.com/.well-known/openid-configuration",
    "scopes": "openid email",
}


def build(doc, org=ORG, catalog=CATALOG, gateway_url=GATEWAY_URL, oauth=OAUTH):
    """짧은 호출용 포장.

    Short wrapper around build_fragment for the tests.
    """
    return build_fragment(doc, org, catalog, gateway_url=gateway_url, oauth=oauth)


def test_fragment_never_contains_model_keys_even_when_the_policy_lists_models():
    """정책에 모델이 있어도 조각엔 모델 키가 없다(모델은 게이트웨이 조각만의 몫).

    Even with models in the policy the fragment has no model keys (models belong to the gateway slice).
    """
    fragment = build(policy(models=["claude-opus-5-5"]))

    assert not (set(fragment) & MODEL_KEYS)
    assert set(fragment) <= FRAGMENT_KEYS


def test_company_only_marketplace_pins_the_tag_and_blocks_sideload():
    """회사 저장소만: strictKnownMarketplaces 에 태그 고정된 회사 소스 하나, sideload 차단.

    Company only: strictKnownMarketplaces holds the one company source pinned to its tag; sideload blocked.
    """
    fragment = build(policy())

    source = {"source": "github", "repo": "example/claude-extensions", "ref": "demo-2026-11"}
    assert fragment["strictKnownMarketplaces"] == [source]
    assert fragment["extraKnownMarketplaces"] == {"example-extensions": {"source": source}}
    assert fragment["enabledPlugins"] == {
        "deck-design@example-extensions": True,
        "infographic-design@example-extensions": True,
    }
    assert fragment["disableSideloadFlags"] is True
    assert fragment["strictPluginOnlyCustomization"] is True


def test_company_and_official_adds_the_official_marketplace():
    """회사 + 공식 마켓: 공식 마켓 소스가 둘째로 들어간다.

    Company + official: the official marketplace source is the second entry.
    """
    fragment = build(policy(locks={"plugin_sources": "company_and_official"}))

    assert len(fragment["strictKnownMarketplaces"]) == 2
    assert fragment["strictKnownMarketplaces"][1]["repo"] == "anthropics/claude-plugins-official"
    assert fragment["disableSideloadFlags"] is True


def test_unrestricted_plugin_sources_removes_the_strict_keys_with_null():
    """제한 없음: 기본값의 strictKnownMarketplaces 를 지우도록 null 을 내고 sideload 를 허용한다.

    Unrestricted: emits null to delete the baseline strictKnownMarketplaces and allows sideload.
    """
    fragment = build(
        policy(locks={"plugin_sources": "unrestricted", "personal_customization": "allow", "mcp": "allow_extra"})
    )

    assert "strictKnownMarketplaces" in fragment and fragment["strictKnownMarketplaces"] is None
    assert fragment["disableSideloadFlags"] is False
    assert fragment["strictPluginOnlyCustomization"] is False
    assert fragment["allowManagedMcpServersOnly"] is False


def test_missing_locks_fall_back_to_the_strict_defaults():
    """잠금을 안 적은 정책은 엄격한 기본값으로 채워진다.

    A policy without locks is filled with the strict defaults.
    """
    fragment = build(policy(locks={}))

    assert STRICT_DEFAULTS == {
        "plugin_sources": "company_only",
        "personal_customization": "block",
        "mcp": "managed_only",
    }
    assert fragment["disableSideloadFlags"] is True
    assert fragment["allowManagedMcpServersOnly"] is True


def test_org_fixed_lock_overrides_the_team_choice():
    """조직 고정 항목은 팀이 적은 값보다 우선한다.

    An org-fixed lock wins over the team's own value.
    """
    org = {"locks_fixed": {"plugin_sources": None, "personal_customization": "block", "mcp": None}}

    fragment = build(policy(locks={"personal_customization": "allow"}), org=org)

    assert fragment["strictPluginOnlyCustomization"] is True


def test_no_marketplace_and_company_only_blocks_every_marketplace():
    """마켓 없이 회사 저장소만 = 아무 마켓도 허용 안 함([]), 플러그인 키 없음.

    No marketplace with company only = no marketplace allowed ([]), no plugin keys.
    """
    fragment = build(policy(marketplace=None, plugins=[]))

    assert fragment["strictKnownMarketplaces"] == []
    assert "extraKnownMarketplaces" not in fragment
    assert "enabledPlugins" not in fragment


def test_managed_mcp_servers_include_the_tool_gateway_with_oauth_when_agents_are_allowed():
    """에이전트가 허용되면 tool-gateway 항목(oauth 포함)이 관리형 MCP 서버에 붙는다.

    With agents allowed, the tool-gateway entry (with oauth) joins the managed MCP servers.
    """
    fragment = build(policy())

    servers = fragment["managedMcpServers"]
    assert servers["deepwiki"] == {"type": "http", "url": "https://mcp.deepwiki.com/mcp"}
    assert servers["tool-gateway"] == {
        "type": "http",
        "url": GATEWAY_URL,
        "oauth": {
            "clientId": "example-client-id",
            "callbackPort": 8094,
            "authServerMetadataUrl": OAUTH["metadata_url"],
            "scopes": "openid email",
        },
    }
    assert "permissions" not in fragment  # every catalog agent allowed: nothing to deny


def test_agents_not_allowed_are_denied_by_exact_tool_name():
    """허용 밖 에이전트의 도구는 정확한 이름으로 permissions.deny 에 들어간다(와일드카드 없음).

    Tools of agents not allowed go into permissions.deny by exact name (no wildcards).
    """
    fragment = build(policy(agents=["api-catalog"]))

    assert fragment["permissions"] == {"deny": ["mcp__tool-gateway__dev-guide-qa___ask"]}
    assert "tool-gateway" in fragment["managedMcpServers"]


def test_no_agents_means_no_tool_gateway_and_no_deny_list():
    """에이전트가 없으면 tool-gateway 항목도 deny 목록도 없다.

    With no agents there is neither a tool-gateway entry nor a deny list.
    """
    fragment = build(policy(agents=[]))

    assert "tool-gateway" not in fragment["managedMcpServers"]
    assert "permissions" not in fragment


def test_tool_gateway_is_skipped_when_the_gateway_url_is_not_configured():
    """게이트웨이 주소가 설정돼 있지 않으면 tool-gateway 항목을 만들지 않는다.

    Without a configured gateway URL no tool-gateway entry is made.
    """
    fragment = build(policy(), gateway_url="", oauth=None)

    assert "tool-gateway" not in fragment.get("managedMcpServers", {})
    assert "permissions" not in fragment


def test_version_changes_with_the_fragment_and_not_with_key_order():
    """버전은 내용의 해시: 키 순서가 달라도 같고, 값이 바뀌면 다르다.

    The version hashes the content: same for a different key order, different for a changed value.
    """
    a = {"x": 1, "y": {"b": 2, "a": 1}}
    b = {"y": {"a": 1, "b": 2}, "x": 1}

    assert version_of(a) == version_of(b)
    assert len(version_of(a)) == 16
    assert version_of({"x": 2}) != version_of(a)


def test_empty_policy_is_valid_and_strict():
    """빈 정책은 유효하고 엄격한 기본값으로 조각이 만들어진다.

    The empty policy is valid and builds a strict fragment.
    """
    doc = empty_policy()

    assert validate_policy(doc, ORG) == []
    assert build(doc)["disableSideloadFlags"] is True


def test_validation_rejects_unknown_keys_and_bad_values():
    """알 수 없는 키·잘못된 값은 저장 전에 거부된다(잘못된 키 하나가 모든 PC 의 시작을 막으므로).

    Unknown keys and bad values are rejected before saving (one bad key blocks every PC from starting).
    """
    errors = validate_policy(
        policy(extra_key=1, locks={"plugin_sources": "whatever"}, mcp_servers={"bad name": {"url": "http://insecure"}}),
        ORG,
    )

    text = "\n".join(errors)
    assert "extra_key" in text
    assert "plugin_sources" in text
    assert "bad name" in text
    assert "https" in text


def test_validation_rejects_plugins_without_a_marketplace():
    """마켓 없이 플러그인만 적으면 거부된다.

    Plugins without a marketplace are rejected.
    """
    errors = validate_policy(policy(marketplace=None), ORG)

    assert any("marketplace" in e for e in errors)


def test_validation_rejects_a_team_value_that_differs_from_an_org_fixed_lock():
    """조직 고정 항목과 다른 팀 값은 거부된다.

    A team value that differs from an org-fixed lock is rejected.
    """
    org = {"locks_fixed": {"plugin_sources": "company_only", "personal_customization": None, "mcp": None}}

    errors = validate_policy(policy(locks={"plugin_sources": "unrestricted"}), org)

    assert any("plugin_sources" in e and "fixed" in e for e in errors)
