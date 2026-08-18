"""Tests for the udata re-pinning helpers.

Covers the two decisions that would silently pin the wrong file: which
resources count as the GTFS zip, and which of them is newest. Both are pure,
so nothing here touches the network.

Run: cd scripts && uv run --with pytest pytest -q
"""

import importlib.util
import json
import os

import pytest

# The script is named with hyphens like the other executables in this
# directory, so it has to be loaded by path rather than imported.
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update-udata-urls.py")
_spec = importlib.util.spec_from_file_location("update_udata_urls", _PATH)
uu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uu)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def get(self, url, timeout=None):
        return FakeResponse(self._payload)


def test_picks_most_recently_modified_zip():
    session = FakeSession({"resources": [
        {"url": "https://x/old.zip", "format": "zip",
         "last_modified": "2026-08-06T07:13:23.250000+00:00"},
        {"url": "https://x/new.zip", "format": "zip",
         "last_modified": "2026-08-13T04:48:27.681000+00:00"},
        {"url": "https://x/older.zip", "format": "zip",
         "last_modified": "2026-07-30T04:52:21.334000+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/new.zip"


def test_ignores_non_zip_resources():
    """The Luxembourg dataset carries loose agency.txt/calendar.txt alongside
    the zips; picking one of those would pin a fragment as the whole feed."""
    session = FakeSession({"resources": [
        {"url": "https://x/agency.txt", "format": "txt",
         "last_modified": "2026-08-20T00:00:00+00:00"},
        {"url": "https://x/feed.zip", "format": "zip",
         "last_modified": "2026-08-13T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/feed.zip"


def test_falls_back_to_url_suffix_when_format_is_unset():
    session = FakeSession({"resources": [
        {"url": "https://x/feed.zip", "format": None,
         "last_modified": "2026-08-13T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/feed.zip"


def test_raises_when_dataset_has_no_zip():
    session = FakeSession({"resources": [{"url": "https://x/a.txt", "format": "txt"}]})
    with pytest.raises(ValueError):
        uu.newest_zip(session, "h", "s")


def test_missing_timestamp_sorts_oldest():
    session = FakeSession({"resources": [
        {"url": "https://x/undated.zip", "format": "zip"},
        {"url": "https://x/dated.zip", "format": "zip",
         "created_at": "2020-01-01T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/dated.zip"


def test_timestamps_without_an_offset_are_comparable():
    """A portal that returns naive timestamps must not make the list
    uncomparable against the offset-bearing ones."""
    session = FakeSession({"resources": [
        {"url": "https://x/naive.zip", "format": "zip",
         "last_modified": "2026-08-20T00:00:00"},
        {"url": "https://x/aware.zip", "format": "zip",
         "last_modified": "2026-08-13T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/naive.zip"


def test_unparseable_last_modified_falls_back_to_created_at():
    session = FakeSession({"resources": [
        {"url": "https://x/a.zip", "format": "zip",
         "last_modified": "not-a-date", "created_at": "2026-06-01T00:00:00+00:00"},
        {"url": "https://x/b.zip", "format": "zip",
         "last_modified": "2026-01-01T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/a.zip"


def test_created_at_used_when_last_modified_absent():
    session = FakeSession({"resources": [
        {"url": "https://x/a.zip", "format": "zip",
         "created_at": "2026-01-01T00:00:00+00:00"},
        {"url": "https://x/b.zip", "format": "zip",
         "created_at": "2026-06-01T00:00:00+00:00"},
    ]})
    assert uu.newest_zip(session, "h", "s")["url"] == "https://x/b.zip"


@pytest.fixture
def feeds_dir(tmp_path):
    (tmp_path / "example.com.dmfr.json").write_text(json.dumps({"feeds": [
        {"id": "f-tagged", "tags": {"udata_host": "h", "udata_dataset": "d"}},
        {"id": "f-host-only", "tags": {"udata_host": "h"}},
        {"id": "f-dataset-only", "tags": {"udata_dataset": "d"}},
        {"id": "f-untagged"},
    ]}))
    (tmp_path / "broken.dmfr.json").write_text("{not json")
    return tmp_path


def test_only_fully_tagged_feeds_are_selected(feeds_dir):
    """Half-tagged feeds are skipped rather than guessed at, and one unparseable
    file does not stop the scan."""
    found = [feed["id"] for _, _, feed in uu.tagged_feeds(feeds_dir)]
    assert found == ["f-tagged"]
