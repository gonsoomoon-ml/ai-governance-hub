"""회사 플러그인 저장소 읽기 — GitHub 의 `.claude-plugin/marketplace.json` 에서 플러그인 이름·설명을 가져온다.

관리자가 조직 설정에 저장소(owner/repo, 태그)를 등록하면 허브가 이 파일을 읽어 카탈로그에 저장하고,
팀은 그 목록에서 고른다. 비공개 저장소(토큰)는 시제품 범위 밖.

Company plugin marketplace reader: plugin names and descriptions from `.claude-plugin/marketplace.json`
on GitHub. An administrator registers a repository (owner/repo, tag) in organization settings, the hub
reads this file into the catalog, and teams pick from it. Private repositories (tokens) are out of scope.
"""

from __future__ import annotations

from typing import Any

import httpx

RAW = "https://raw.githubusercontent.com"
MANIFEST = ".claude-plugin/marketplace.json"


class MarketplaceError(Exception):
    """저장소를 읽지 못했거나 marketplace.json 모양이 아니다.

    The repository could not be read or the manifest is not a marketplace.
    """


def manifest_url(repo: str, ref: str = "", path: str = "") -> str:
    """raw.githubusercontent.com 의 manifest 주소. ref 가 비면 HEAD(기본 브랜치).

    The manifest URL on raw.githubusercontent.com; an empty ref means HEAD (the default branch).
    """
    prefix = f"{path.strip('/')}/" if path else ""
    return f"{RAW}/{repo}/{ref or 'HEAD'}/{prefix}{MANIFEST}"


def fetch_manifest(http: httpx.Client, repo: str, ref: str = "", path: str = "") -> dict:
    """manifest 를 읽어 {name, plugins: [{name, description}]} 로 돌려준다. 실패는 MarketplaceError.

    Read the manifest and return {name, plugins: [{name, description}]}; failures raise MarketplaceError.
    """
    url = manifest_url(repo, ref, path)
    try:
        resp = http.get(url, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise MarketplaceError(f"cannot reach {url}: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise MarketplaceError(f"HTTP {resp.status_code} for {url}")
    try:
        body: Any = resp.json()
    except ValueError as exc:
        raise MarketplaceError(f"not JSON: {url}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("plugins"), list):
        raise MarketplaceError(f"not a marketplace manifest (no plugins list): {url}")
    plugins = []
    for entry in body["plugins"]:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"]:
            plugins.append({"name": entry["name"], "description": str(entry.get("description") or "")})
    return {"name": str(body.get("name") or ""), "plugins": plugins}
