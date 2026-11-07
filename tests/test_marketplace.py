"""회사 플러그인 저장소 읽기 — manifest 주소, 정상 파싱, 404·JSON 아님·manifest 아님·연결 실패.

Company marketplace reader: manifest URL, a good parse, 404, non-JSON, not-a-manifest, connection failure.
"""

import httpx
import pytest

from hub.marketplace import MarketplaceError, fetch_manifest, manifest_url

GOOD = {
    "name": "example-extensions",
    "plugins": [{"name": "deck-design", "description": "Decks"}, {"name": "x"}, {"bad": 1}],
}


def client_with(handler) -> httpx.Client:
    """핸들러 하나로 응답하는 httpx 클라이언트.

    An httpx client answering through one handler.
    """
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_manifest_url_pins_the_ref_and_defaults_to_head():
    assert manifest_url("o/r", "v1") == "https://raw.githubusercontent.com/o/r/v1/.claude-plugin/marketplace.json"
    assert manifest_url("o/r") == "https://raw.githubusercontent.com/o/r/HEAD/.claude-plugin/marketplace.json"
    assert (
        manifest_url("o/r", "v1", "sub/dir")
        == "https://raw.githubusercontent.com/o/r/v1/sub/dir/.claude-plugin/marketplace.json"
    )


def test_fetch_manifest_returns_name_and_plugins_with_descriptions():
    seen = []
    http = client_with(lambda r: (seen.append(str(r.url)), httpx.Response(200, json=GOOD))[1])
    out = fetch_manifest(http, "o/r", "v1")
    assert seen == ["https://raw.githubusercontent.com/o/r/v1/.claude-plugin/marketplace.json"]
    assert out == {
        "name": "example-extensions",
        "plugins": [{"name": "deck-design", "description": "Decks"}, {"name": "x", "description": ""}],
    }


@pytest.mark.parametrize(
    "response, phrase",
    [
        (httpx.Response(404, text="404: Not Found"), "HTTP 404"),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json={"name": "x"}), "no plugins list"),
    ],
)
def test_fetch_manifest_turns_bad_answers_into_marketplace_error(response, phrase):
    with pytest.raises(MarketplaceError, match=phrase):
        fetch_manifest(client_with(lambda r: response), "o/r", "v1")


def test_fetch_manifest_turns_connection_failure_into_marketplace_error():
    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(MarketplaceError, match="cannot reach"):
        fetch_manifest(client_with(boom), "o/r", "v1")
