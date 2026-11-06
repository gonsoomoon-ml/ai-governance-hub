"""fmt_ts — 저장된 UTC ISO 시각을 화면 시간대의 짧은 표기로.

fmt_ts: stored UTC ISO timestamps rendered in the screen time zone, short form.
"""

from hub.timefmt import fmt_ts


def test_utc_iso_becomes_local_month_day_and_time():
    assert fmt_ts("2026-11-06T01:21:02Z", "Asia/Seoul") == "11/06 10:21"


def test_seconds_can_be_included():
    assert fmt_ts("2026-11-06T01:21:02Z", "Asia/Seoul", seconds=True) == "11/06 10:21:02"


def test_empty_values_render_as_empty():
    assert fmt_ts("", "Asia/Seoul") == ""
    assert fmt_ts(None, "Asia/Seoul") == ""


def test_unparseable_values_are_returned_unchanged():
    assert fmt_ts("yesterday", "Asia/Seoul") == "yesterday"


def test_unknown_zone_falls_back_to_utc():
    assert fmt_ts("2026-11-06T01:21:02Z", "Mars/Olympus") == "11/06 01:21"
