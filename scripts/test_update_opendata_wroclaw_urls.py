"""Tests for the OpenData Wrocław re-pinning helpers.

Covers the decisions that would otherwise pin the wrong file: which upload
counts as current when the portal mixes fortnightly files with one- and
two-day holiday files, what counts as GTFS, and that the pin never walks back
to an older upload. Nothing here touches the network.

Run from the repository root, as CI does:
    uv run --with pytest pytest scripts/ -q
"""

import importlib.util
import io
import json
import os
import zipfile
from datetime import date

import pytest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update-opendata-wroclaw-urls.py")
_spec = importlib.util.spec_from_file_location("update_opendata_wroclaw_urls", _PATH)
ow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ow)

TODAY = date(2026, 11, 9)


def gtfs_zip(start, end, extra_dates=(), drop=None):
    """A minimal GTFS archive whose calendar runs start..end (YYYYMMDD strings)."""
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n1,A,https://a,Europe/Warsaw\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\ns,S,51,17\n",
        "routes.txt": "route_id,route_type\nr,3\n",
        "trips.txt": "route_id,service_id,trip_id\nr,c,t\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nt,08:00:00,08:00:00,s,1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        f"c,1,1,1,1,1,1,1,{start},{end}\n",
        "calendar_dates.txt": "service_id,date,exception_type\n"
                              + "".join(f"c,{d},{t}\n" for d, t in extra_dates),
    }
    if drop:
        files.pop(drop)
    buf = io.BytesIO()
    # Stored rather than deflated, so corrupt() can find and damage the bytes.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload, self.content = payload, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, ids, files):
        self.ids, self.files, self.downloaded = ids, files, []

    def get(self, url, timeout=None):
        if url.startswith("https://api.open-data.cui.wroclaw.pl/od2/"):
            return FakeResponse({"id": 6, "active": True, "pliki": self.ids})
        fid = ow.pinned_id(url)
        self.downloaded.append(fid)
        return FakeResponse(content=self.files[fid])


# --- service_window

def test_window_spans_calendar_and_added_dates():
    data = gtfs_zip("20261107", "20261122", extra_dates=[("20261125", 1), ("20261101", 2)])
    assert ow.service_window(data) == (date(2026, 11, 7), date(2026, 11, 25))


def test_removed_dates_do_not_extend_window():
    data = gtfs_zip("20261107", "20261122", extra_dates=[("20261130", 2)])
    assert ow.service_window(data)[1] == date(2026, 11, 22)


def corrupt(data):
    """Flip a byte inside calendar.txt's stored data, so reading it fails its CRC."""
    i = data.index(b"c,1,1,1,1,1,1,1,")
    return data[:i] + b"X" + data[i + 1:]


@pytest.mark.parametrize("data", [b"<html>not a zip</html>",
                                  gtfs_zip("20261107", "20261122", drop="stop_times.txt"),
                                  corrupt(gtfs_zip("20261107", "20261122"))])
def test_non_gtfs_or_damaged_has_no_window(data):
    assert ow.service_window(data) is None


# --- choose

def test_skips_holiday_file_for_fortnightly_one():
    # Newest upload covers today but expires tomorrow; the older one runs on.
    candidates = [(154, (date(2026, 11, 8), date(2026, 11, 10))),
                  (149, (date(2026, 11, 2), date(2026, 11, 22)))]
    assert ow.choose(candidates, TODAY) == 149


def test_newest_qualifying_file_wins():
    candidates = [(160, (date(2026, 11, 7), date(2026, 11, 22))),
                  (156, (date(2026, 11, 3), date(2026, 11, 22)))]
    assert ow.choose(candidates, TODAY) == 160


def test_falls_back_to_longest_running_covering_file():
    # Neither runs 7 days ahead; the older file outlasts the newer one.
    candidates = [(160, (date(2026, 11, 8), date(2026, 11, 10))),
                  (156, (date(2026, 11, 1), date(2026, 11, 11)))]
    assert ow.choose(candidates, TODAY) == 156


def test_fallback_tie_goes_to_newest():
    candidates = [(160, (date(2026, 11, 8), date(2026, 11, 11))),
                  (156, (date(2026, 11, 1), date(2026, 11, 11)))]
    assert ow.choose(candidates, TODAY) == 160


def test_choose_honours_min_days_ahead():
    candidates = [(160, (date(2026, 11, 8), date(2026, 11, 12))),
                  (156, (date(2026, 11, 1), date(2026, 11, 30)))]
    assert ow.choose(candidates, TODAY, min_days_ahead=3) == 160
    assert ow.choose(candidates, TODAY) == 156


def test_future_only_or_unreadable_files_are_not_chosen():
    candidates = [(161, (date(2026, 11, 20), date(2026, 12, 5))), (160, None)]
    assert ow.choose(candidates, TODAY) is None


# --- resolve

def test_resolve_updates_and_stops_downloading_at_first_qualifying():
    s = FakeSession([156, 160, 154],
                    {160: gtfs_zip("20261107", "20261122"),
                     156: gtfs_zip("20261103", "20261122"),
                     154: gtfs_zip("20261031", "20261101")})
    url, reason = ow.resolve(s, "6", ow.DOWNLOAD_URL.format(file_id=154), TODAY)
    assert (url, reason) == (ow.DOWNLOAD_URL.format(file_id=160), "update")
    assert s.downloaded == [160]


def test_resolve_reports_current():
    s = FakeSession([160], {160: gtfs_zip("20261107", "20261122")})
    current = ow.DOWNLOAD_URL.format(file_id=160)
    assert ow.resolve(s, "6", current, TODAY) == (current, "current")


def test_resolve_does_not_walk_back_while_pin_covers_today():
    # The pinned upload still covers today, so an older file is not considered
    # even though it runs longer.
    s = FakeSession([160, 156],
                    {160: gtfs_zip("20261105", "20261112"),
                     156: gtfs_zip("20261101", "20261130")})
    current = ow.DOWNLOAD_URL.format(file_id=160)
    assert ow.resolve(s, "6", current, TODAY) == (current, "current")
    assert s.downloaded == [160]


def test_holiday_file_cannot_strand_the_feed():
    # The reviewer's case: full timetable 149 runs 2-14 Nov, holiday file 154
    # runs 8-10 Nov. On 9 Nov neither runs a week ahead; the job keeps 149
    # rather than moving to the newer but shorter 154.
    files = {154: gtfs_zip("20261108", "20261110"), 149: gtfs_zip("20261102", "20261114")}
    pin149 = ow.DOWNLOAD_URL.format(file_id=149)
    assert ow.resolve(FakeSession([154, 149], files), "6", pin149, date(2026, 11, 9)) == (pin149, "current")
    # And if something did pin 154, once it runs out the job returns to 149.
    pin154 = ow.DOWNLOAD_URL.format(file_id=154)
    s = FakeSession([154, 149], files)
    assert ow.resolve(s, "6", pin154, date(2026, 11, 11)) == (pin149, "update")
    assert s.downloaded == [154, 149]


def test_resolve_recovers_when_pinned_file_is_withdrawn():
    # 160 was pinned but has been removed from the listing.
    s = FakeSession([156, 154],
                    {156: gtfs_zip("20261103", "20261122"),
                     154: gtfs_zip("20261031", "20261101")})
    url, reason = ow.resolve(s, "6", ow.DOWNLOAD_URL.format(file_id=160), TODAY)
    assert (url, reason) == (ow.DOWNLOAD_URL.format(file_id=156), "update")


def test_resolve_reports_stale_when_nothing_covers_today():
    s = FakeSession([160], {160: gtfs_zip("20261101", "20261108")})
    assert ow.resolve(s, "6", ow.DOWNLOAD_URL.format(file_id=160), TODAY) == (None, ow.STALE)


def test_corrupt_newest_upload_falls_back_to_older():
    s = FakeSession([161, 160],
                    {161: corrupt(gtfs_zip("20261108", "20261130")),
                     160: gtfs_zip("20261107", "20261122")})
    url, reason = ow.resolve(s, "6", ow.DOWNLOAD_URL.format(file_id=156), TODAY)
    assert (url, reason) == (ow.DOWNLOAD_URL.format(file_id=160), "update")


def test_resolve_adopts_portal_from_another_host():
    s = FakeSession([160], {160: gtfs_zip("20261107", "20261122")})
    url, reason = ow.resolve(s, "6", "https://www.wroclaw.pl/open-data/x/GTFS.zip", TODAY)
    assert reason == "update" and url.endswith("/hdb/download/160/")


def test_rejects_non_numeric_dataset():
    with pytest.raises(ValueError):
        ow.file_ids(FakeSession([], {}), "../evil")


def test_pinned_id_only_matches_portal_download_urls():
    assert ow.pinned_id("https://open-data.cui.wroclaw.pl/hdb/download/160/") == 160
    assert ow.pinned_id("https://example.com/hdb/download/160/") is None
    assert ow.pinned_id(None) is None


# --- main

def _feeds_dir(tmp_path, url):
    doc = {"feeds": [{"id": "f-wro", "spec": "gtfs", "urls": {"static_current": url},
                      "tags": {ow.TAG: "6"}}]}
    (tmp_path / "x.dmfr.json").write_text(json.dumps(doc))
    return tmp_path


def _run_main(monkeypatch, tmp_path, url, session):
    monkeypatch.setattr(ow, "FEEDS_DIR", _feeds_dir(tmp_path, url))
    monkeypatch.setattr(ow.requests, "Session", lambda: session)
    monkeypatch.setattr("sys.argv", ["prog", "--today", TODAY.isoformat()])
    return ow.main()


def test_main_fails_when_feed_is_left_stale(monkeypatch, tmp_path):
    session = FakeSession([160], {160: gtfs_zip("20261101", "20261108")})
    session.headers = {}
    assert _run_main(monkeypatch, tmp_path, ow.DOWNLOAD_URL.format(file_id=160), session) == 1


def test_main_writes_update_and_succeeds(monkeypatch, tmp_path):
    session = FakeSession([160], {160: gtfs_zip("20261107", "20261122")})
    session.headers = {}
    assert _run_main(monkeypatch, tmp_path, ow.DOWNLOAD_URL.format(file_id=156), session) == 0
    doc = json.loads((tmp_path / "x.dmfr.json").read_text())
    assert doc["feeds"][0]["urls"]["static_current"] == ow.DOWNLOAD_URL.format(file_id=160)
