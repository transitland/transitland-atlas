"""Tests for the pure parts of feed_probe.

Everything here runs offline. The functions that shell out to `transitland` or
hit the network are deliberately not covered: their failure modes are timeouts
and a missing binary, both already handled and neither cheap to simulate
usefully.

Run: cd scripts && uv run --with pytest pytest -q
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feed_probe  # noqa: E402

TODAY = date(2026, 8, 8)


# --- calendar_status -------------------------------------------------------
# Expiry is the field most registration decisions turn on, and the boundaries
# are where date arithmetic goes wrong.

def test_calendar_active_midway():
    status, expired = feed_probe.calendar_status("2026-01-01", "2026-12-31", TODAY)
    assert status.startswith("active")
    assert expired is None


def test_calendar_expiring_today_is_still_active():
    # The last day of service is inclusive; a feed is not expired until the day
    # after its latest date.
    status, expired = feed_probe.calendar_status("2026-01-01", "2026-08-08", TODAY)
    assert status.startswith("active")
    assert expired is None


def test_calendar_expired_yesterday_reports_one_day():
    status, expired = feed_probe.calendar_status("2026-01-01", "2026-08-07", TODAY)
    assert expired == 1
    assert status == "expired 1d"


def test_calendar_long_expired_reports_the_distance():
    # The distinction the docstring cares about: mid-refresh versus abandoned.
    _, expired = feed_probe.calendar_status("2021-09-01", "2024-10-01", TODAY)
    assert expired == 676


def test_calendar_starting_in_future():
    status, expired = feed_probe.calendar_status("2026-09-01", "2026-12-31", TODAY)
    assert status.startswith("future")
    assert expired is None


def test_calendar_single_day_span_does_not_divide_by_zero():
    status, expired = feed_probe.calendar_status("2026-08-08", "2026-08-08", TODAY)
    assert status.startswith("active")
    assert expired is None


@pytest.mark.parametrize("earliest,latest", [
    ("", "2026-12-31"),
    ("2026-01-01", ""),
    ("", ""),
    (None, None),
])
def test_calendar_missing_dates_are_unknown_not_expired(earliest, latest):
    assert feed_probe.calendar_status(earliest, latest, TODAY) == ("unknown", None)


def test_calendar_accepts_gtfs_wire_format_dates():
    # GTFS writes dates as YYYYMMDD, which date.fromisoformat parses as ISO 8601
    # basic format. Worth pinning: it means dates lifted straight out of a feed
    # need no conversion, and a future tightening of the parser would break that
    # silently.
    status, expired = feed_probe.calendar_status("20260101", "20261231", TODAY)
    assert status.startswith("active")
    assert expired is None
    assert feed_probe.calendar_status("20260101", "20260807", TODAY)[1] == 1


@pytest.mark.parametrize("earliest,latest", [
    ("2026-13-01", "2026-12-31"),  # not a real month
    ("garbage", "2026-12-31"),
    ("2026-01-01\r", "2026-12-31"),  # stray carriage return, seen in a real feed
])
def test_calendar_unparseable_dates_are_reported_as_such(earliest, latest):
    # A feed whose dates cannot be read is a different finding from one with no
    # dates at all: a real archive was published with stray carriage returns in
    # its date fields, and reporting that as "unknown" would have hidden it.
    assert feed_probe.calendar_status(earliest, latest, TODAY) == ("unparseable", None)
