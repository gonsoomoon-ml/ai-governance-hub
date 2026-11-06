"""화면 시각 — 저장소의 UTC ISO 문자열을 설정한 시간대의 짧은 표기('11/06 10:21')로.

Screen time: the store's UTC ISO strings in the configured zone, short form ('11/06 10:21').
"""

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: str) -> tzinfo:
    """시간대 이름 -> tzinfo. 모르는 이름이면 UTC (페이지가 죽지 않게).

    Zone name -> tzinfo; UTC for an unknown name so a page never fails on it.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return UTC


def fmt_ts(value: str | None, zone_name: str, *, seconds: bool = False) -> str:
    """ISO 시각 -> 'MM/DD HH:MM'(seconds=True 면 초까지). 빈 값은 빈 문자열, 못 읽는 값은 그대로.

    ISO timestamp -> 'MM/DD HH:MM' (with seconds when asked). Empty stays empty; unparseable is returned as is.
    """
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(zone(zone_name))
    return local.strftime("%m/%d %H:%M:%S" if seconds else "%m/%d %H:%M")
