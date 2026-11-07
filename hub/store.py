"""DynamoDB 단일 표 저장소. 키는 pk/sk 두 문자열이고 항목 종류는 접두어로 가른다.

  ORG / SETTINGS                 조직 고정 항목 + 조직 기본 정책
  TEAM#<id> / META               관리할 팀(이름·부서·팀 리더 이메일)
  TEAM#<id> / POLICY#DRAFT       편집 중 정책 + 상태(draft·pending·rejected·published)
  TEAM#<id> / POLICY#PUBLISHED   발행본(+ 버전 번호·해시)
  TEAM#<id> / VERSION#<n>        발행 이력
  AGENTS / <name>                Registry 에서 복사한 승인 에이전트 카탈로그(도구 이름 포함)
  MARKETS / <name>               회사 플러그인 저장소 카탈로그(repo·ref·플러그인 목록, GitHub 에서 읽음)
  AUDIT / <ns>#<uuid>            감사 기록

DynamoDB 를 고른 이유: Fargate 라 디스크가 없고, Aurora 에 DB 를 새로 파는 것보다 일이 적다.
/v1/resolve 는 이 표만 읽는다(AWS 의 다른 API 는 발행 때만).

Single-table DynamoDB store. Keys are two strings pk/sk; item kinds are told apart by prefix
(see the Korean list above). DynamoDB because Fargate has no disk and a new Aurora database is
more work. /v1/resolve reads only this table (other AWS APIs are called at publish time only).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from hub.fragment import empty_policy

DEFAULT_LOCKS_FIXED = {"plugin_sources": None, "personal_customization": None, "mcp": None}


#: put_draft 의 "스냅샷은 그대로" 표시값 / sentinel for put_draft: keep the stored snapshot
KEEP: object = object()


class Store:
    """표 하나를 감싼 저장소. 메서드는 동기(boto3)이며 FastAPI 가 스레드 풀에서 돌린다.

    A store around one table. Methods are synchronous (boto3); FastAPI runs them in its thread pool.
    """

    def __init__(self, table_name: str, region: str, endpoint_url: str | None = None):
        """표 이름·리전(·로컬 DynamoDB 주소)으로 리소스를 준비한다. 네트워크는 아직 안 쓴다.

        Prepare the resource from table name, region (and a local DynamoDB endpoint). No network yet.
        """
        self.table_name = table_name
        self._ddb = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url or None)
        self._table = self._ddb.Table(table_name)

    # --- table ---

    def ensure_table(self) -> None:
        """표가 없으면 만든다(pk/sk, 온디맨드). 로컬·테스트용; 운영 표는 배포 때 만든다.

        Create the table if missing (pk/sk, on-demand). For local use and tests; the production
        table is created at deploy time.
        """
        existing = self._ddb.meta.client.list_tables()["TableNames"]
        if self.table_name in existing:
            return
        table = self._ddb.create_table(
            TableName=self.table_name,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()

    # --- organization ---

    def org_settings(self) -> dict:
        """조직 설정. 없으면 고정 항목 없음 + 빈 기본 정책.

        Org settings; without a stored item, no fixed locks and the empty default policy.
        """
        item = self._get("ORG", "SETTINGS") or {}
        return {
            "locks_fixed": {**DEFAULT_LOCKS_FIXED, **(item.get("locks_fixed") or {})},
            "default_policy": {**empty_policy(), **(item.get("default_policy") or {})},
        }

    def put_org_settings(self, settings: dict) -> None:
        """조직 설정 저장(고정 항목 + 기본 정책).

        Save org settings (fixed locks + default policy).
        """
        self._put(
            {
                "pk": "ORG",
                "sk": "SETTINGS",
                "locks_fixed": settings.get("locks_fixed") or {},
                "default_policy": settings.get("default_policy") or empty_policy(),
                "updated_at": _now(),
            }
        )

    # --- teams ---

    def list_teams(self) -> list[dict]:
        """관리할 팀 전부, 이름순.

        Every managed team, by name.
        """
        items = self._scan_sk("META")
        return sorted((_strip(i) for i in items), key=lambda t: (t.get("dept", ""), t.get("name", "")))

    def get_team(self, team_id: str) -> dict | None:
        """관리할 팀 하나. 없으면 None.

        One managed team, or None.
        """
        item = self._get(f"TEAM#{team_id}", "META")
        return _strip(item) if item else None

    def put_team(self, team: dict) -> None:
        """팀을 관리 대상으로 등록(또는 이름·리더 갱신).

        Register a team as managed (or update its name and leader).
        """
        self._put({"pk": f"TEAM#{team['team_id']}", "sk": "META", **team, "updated_at": _now()})

    def remove_team(self, team_id: str) -> None:
        """관리 대상에서 뺀다. 정책·이력은 남겨 두어 다시 등록하면 그대로 보인다.

        Unmanage a team. Policy and history stay so re-registering shows them again.
        """
        self._table.delete_item(Key={"pk": f"TEAM#{team_id}", "sk": "META"})

    # --- policies ---

    def get_policy(self, team_id: str, which: str) -> dict | None:
        """팀의 DRAFT 또는 PUBLISHED 항목(doc + 상태·버전 메타). 없으면 None.

        The team's DRAFT or PUBLISHED item (doc + status and version meta), or None.
        """
        item = self._get(f"TEAM#{team_id}", f"POLICY#{which}")
        return _strip(item) if item else None

    def put_draft(
        self,
        team_id: str,
        doc: dict,
        *,
        status: str = "draft",
        submitted_by: str = "",
        rejected_reason: str = "",
        edited_by: str = "",
        gateway_models: list[str] | None | object = KEEP,
    ) -> None:
        """초안 저장(상태 포함). 제출이면 submitted_by·submitted_at 이 채워진다.

        gateway_models = 저장할 때 본 AI 게이트웨이의 팀 허용 모델(한 손 규칙의 스냅샷; [] = 제한 없음).
        생략(KEEP)하면 이미 있던 스냅샷을 그대로 두고, None 이면 지운다(읽지 못함).

        Save the draft with its status; a submission fills submitted_by and submitted_at.

        gateway_models = the AI gateway's team allowed models as seen at save time (the one-writer
        snapshot; [] = no restriction). Omitted (KEEP) keeps the existing snapshot; None drops it (unread).
        """
        item = {
            "pk": f"TEAM#{team_id}",
            "sk": "POLICY#DRAFT",
            "doc": doc,
            "status": status,
            "edited_by": edited_by,
            "updated_at": _now(),
            "rejected_reason": rejected_reason,
        }
        if gateway_models is KEEP:
            previous = self._get(f"TEAM#{team_id}", "POLICY#DRAFT") or {}
            gateway_models = previous.get("gateway_models")
        if isinstance(gateway_models, list):
            item["gateway_models"] = [str(a) for a in gateway_models]
        if status == "pending":
            item["submitted_by"] = submitted_by
            item["submitted_at"] = _now()
        self._put(item)

    def publish(self, team_id: str, doc: dict, *, by: str, version_hash: str) -> int:
        """발행: 버전 번호를 올리고 VERSION 이력·PUBLISHED·DRAFT(상태 published) 를 쓴다. 번호를 돌려준다.

        Publish: bump the version number, write VERSION history, PUBLISHED and DRAFT (status
        published). Returns the number.
        """
        current = self._get(f"TEAM#{team_id}", "POLICY#PUBLISHED")
        version = int(current["version"]) + 1 if current else 1
        now = _now()
        record = {"doc": doc, "version": version, "version_hash": version_hash, "published_by": by, "published_at": now}
        self._put({"pk": f"TEAM#{team_id}", "sk": f"VERSION#{version:06d}", **record})
        self._put({"pk": f"TEAM#{team_id}", "sk": "POLICY#PUBLISHED", **record})
        # The DRAFT is rewritten without gateway_models on purpose: after a publish the published
        # copy itself is the value the hub last wrote, which is what the one-writer check compares.
        self._put(
            {
                "pk": f"TEAM#{team_id}",
                "sk": "POLICY#DRAFT",
                "doc": doc,
                "status": "published",
                "updated_at": now,
                "edited_by": by,
                "rejected_reason": "",
            }
        )
        return version

    def list_versions(self, team_id: str, limit: int = 20) -> list[dict]:
        """발행 이력, 최신 순.

        Publish history, newest first.
        """
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(f"TEAM#{team_id}") & Key("sk").begins_with("VERSION#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_strip(i) for i in resp.get("Items", [])]

    # --- agent catalog ---

    def list_agents(self) -> list[dict]:
        """승인 에이전트 카탈로그(이름순).

        The approved agent catalog, by name.
        """
        resp = self._table.query(KeyConditionExpression=Key("pk").eq("AGENTS"))
        return sorted((_strip(i) for i in resp.get("Items", [])), key=lambda a: a["name"])

    def put_agents(self, agents: list[dict]) -> None:
        """카탈로그를 통째로 바꾼다(Registry 를 읽을 때마다).

        Replace the whole catalog (each time the Registry is read).
        """
        for old in self.list_agents():
            self._table.delete_item(Key={"pk": "AGENTS", "sk": old["name"]})
        for agent in agents:
            self._put({"pk": "AGENTS", "sk": agent["name"], **agent, "updated_at": _now()})

    # --- audit ---

    def audit(self, action: str, *, actor: str, team_id: str | None = None, detail: dict | None = None) -> None:
        """감사 기록 한 줄.

        One audit entry.
        """
        self._put(
            {
                "pk": "AUDIT",
                "sk": f"{time.time_ns():020d}#{uuid.uuid4().hex[:8]}",
                "action": action,
                "actor": actor,
                "team_id": team_id or "",
                "detail": detail or {},
                "at": _now(),
            }
        )

    def list_audit(self, limit: int = 50) -> list[dict]:
        """감사 기록, 최신 순.

        Audit entries, newest first.
        """
        resp = self._table.query(KeyConditionExpression=Key("pk").eq("AUDIT"), ScanIndexForward=False, Limit=limit)
        return [_strip(i) for i in resp.get("Items", [])]

    # --- catalogs the teams pick from ---

    def list_marketplaces(self) -> list[dict]:
        """회사 플러그인 저장소 카탈로그(이름순).

        The company marketplace catalog, by name.
        """
        resp = self._table.query(KeyConditionExpression=Key("pk").eq("MARKETS"))
        return sorted((_strip(i) for i in resp.get("Items", [])), key=lambda m: m["name"])

    def put_marketplace(self, market: dict) -> None:
        """저장소 하나 저장/교체({name, repo, ref, path, plugins, fetched_at}).

        Save or replace one marketplace ({name, repo, ref, path, plugins, fetched_at}).
        """
        self._put({"pk": "MARKETS", "sk": market["name"], **market, "updated_at": _now()})

    def delete_marketplace(self, name: str) -> None:
        """저장소 하나 삭제(발행된 정책은 값을 품고 있어 영향 없음).

        Delete one marketplace (published policies carry their own copy and are unaffected).
        """
        self._table.delete_item(Key={"pk": "MARKETS", "sk": name})

    # --- low level ---

    def _get(self, pk: str, sk: str) -> dict | None:
        """항목 하나를 읽어 평범한 파이썬 값으로 돌려준다.

        Read one item and return it as plain Python values.
        """
        resp = self._table.get_item(Key={"pk": pk, "sk": sk})
        item = resp.get("Item")
        return _plain(item) if item else None

    def _put(self, item: dict) -> None:
        """항목 저장. 빈 문자열은 DynamoDB 가 받으므로 그대로 둔다.

        Save one item. Empty strings are accepted by DynamoDB and kept as they are.
        """
        self._table.put_item(Item=_to_ddb(item))

    def _scan_sk(self, sk: str) -> list[dict]:
        """sk 가 정확히 같은 항목 전부(META 처럼 적은 수에만 쓴다).

        Every item whose sk equals the value (only for small sets such as META).
        """
        from boto3.dynamodb.conditions import Attr

        items: list[dict] = []
        kwargs: dict[str, Any] = {"FilterExpression": Attr("sk").eq(sk)}
        while True:
            resp = self._table.scan(**kwargs)
            items.extend(_plain(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _now() -> str:
    """UTC ISO 8601 시각(초 단위).

    UTC ISO 8601 timestamp (seconds).
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip(item: dict) -> dict:
    """pk/sk 를 뺀 항목(Decimal 은 평범한 수로).

    The item without pk/sk (Decimals as plain numbers).
    """
    return {k: _plain(v) for k, v in item.items() if k not in ("pk", "sk")}


def _plain(value: Any) -> Any:
    """DynamoDB 의 Decimal 을 int/float 로 되돌린다(중첩 포함).

    Turn DynamoDB Decimals back into int/float, recursively.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _to_ddb(value: Any) -> Any:
    """float 을 Decimal 로 바꿔 DynamoDB 가 받게 한다(JSON 왕복으로 간단히).

    Make floats Decimal so DynamoDB accepts them (via a JSON round-trip for simplicity).
    """
    return json.loads(json.dumps(value), parse_float=Decimal)
