"""Tests for the udata re-pinning helpers.

Covers the decisions that would otherwise pin the wrong file or strand a whole
run: which resources count as the GTFS zip, which of them is newest, whether a
resolve is allowed to move the pin backwards, and which failures are survivable.
All of it is pure, so nothing here touches the network.

Run from the repository root, as CI does, so uv resolves pyproject.toml:
    uv run --with pytest pytest scripts/ -q
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

# Any host used here has to be one the script is willing to contact.
HOST = "data.public.lu"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Records the URL it was asked for, so interpolation is actually tested."""

    def __init__(self, payload):
        self._payload = payload
        self.requested = None

    def get(self, url, timeout=None):
        self.requested = url
        return FakeResponse(self._payload)


def resources(*items):
    return FakeSession({"resources": list(items)})


def zip_res(url, modified=None, fmt="zip", **extra):
    res = {"url": url, "format": fmt}
    if modified is not None:
        res["last_modified"] = modified
    res.update(extra)
    return res


def test_queries_the_documented_dataset_endpoint():
    session = resources(zip_res("https://x/a.zip", "2026-08-13T00:00:00+00:00"))
    uu.newest_zip(session, HOST, "my-dataset")
    assert session.requested == f"https://{HOST}/api/1/datasets/my-dataset/"


def test_picks_most_recently_modified_zip():
    session = resources(
        zip_res("https://x/old.zip", "2026-08-06T07:13:23.250000+00:00"),
        zip_res("https://x/new.zip", "2026-08-13T04:48:27.681000+00:00"),
        zip_res("https://x/older.zip", "2026-07-30T04:52:21.334000+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/new.zip"


def test_ignores_non_zip_resources():
    """The Luxembourg dataset carries loose agency.txt/calendar.txt alongside
    the zips; picking one of those would pin a fragment as the whole feed."""
    session = resources(
        zip_res("https://x/agency.txt", "2026-08-20T00:00:00+00:00", fmt="txt"),
        zip_res("https://x/feed.zip", "2026-08-13T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/feed.zip"


def test_falls_back_to_url_suffix_when_format_is_unset():
    session = resources(zip_res("https://x/feed.zip", "2026-08-13T00:00:00+00:00", fmt=None))
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/feed.zip"


def test_raises_when_dataset_has_no_zip():
    session = resources(zip_res("https://x/a.txt", fmt="txt"))
    with pytest.raises(ValueError):
        uu.newest_zip(session, HOST, "s")


# --- host allowlist -------------------------------------------------------


def test_host_outside_the_allowlist_is_refused():
    """Feed files take fork pull requests, so the host they name decides where
    this job sends a request. Anything unrecognised is refused before that."""
    session = resources(zip_res("https://x/a.zip", "2026-08-13T00:00:00+00:00"))
    with pytest.raises(ValueError, match="not an allowed udata host"):
        uu.newest_zip(session, "attacker.example", "s")
    assert session.requested is None


def test_allowlist_covers_the_configured_feed():
    assert "data.public.lu" in uu.ALLOWED_HOSTS


# --- resources that would otherwise raise ---------------------------------


def test_resource_without_a_url_is_skipped():
    """A zip-formatted resource with no url passed the old filter and then blew
    up on lookup, taking every other feed in the run down with it."""
    session = resources(
        {"format": "zip", "last_modified": "2026-08-20T00:00:00+00:00"},
        {"format": "zip", "url": None, "last_modified": "2026-08-19T00:00:00+00:00"},
        zip_res("https://x/real.zip", "2026-08-13T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/real.zip"


def test_dataset_of_only_urlless_resources_raises():
    session = resources({"format": "zip"}, {"format": "zip", "url": ""})
    with pytest.raises(ValueError, match="no zip resources"):
        uu.newest_zip(session, HOST, "s")


def test_non_string_timestamp_does_not_crash():
    """A portal returning an epoch int used to raise AttributeError."""
    session = resources(
        zip_res("https://x/epoch.zip", 1755648000),
        zip_res("https://x/iso.zip", "2020-01-01T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/iso.zip"


def test_missing_timestamp_sorts_oldest():
    session = resources(
        zip_res("https://x/undated.zip"),
        zip_res("https://x/dated.zip", None, created_at="2020-01-01T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/dated.zip"


def test_timestamps_without_an_offset_are_comparable():
    session = resources(
        zip_res("https://x/naive.zip", "2026-08-20T00:00:00"),
        zip_res("https://x/aware.zip", "2026-08-13T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/naive.zip"


def test_unparseable_created_at_falls_back_to_last_modified():
    session = resources(
        zip_res("https://x/a.zip", "2026-06-01T00:00:00+00:00", created_at="not-a-date"),
        zip_res("https://x/b.zip", "2026-01-01T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/a.zip"


def test_created_at_used_when_last_modified_absent():
    session = resources(
        zip_res("https://x/a.zip", None, created_at="2026-01-01T00:00:00+00:00"),
        zip_res("https://x/b.zip", None, created_at="2026-06-01T00:00:00+00:00"),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/b.zip"


def test_created_at_outranks_a_bumped_last_modified():
    """The whole point of keying on created_at: a metadata edit on an old
    archive bumps last_modified but cannot make it the newest snapshot."""
    session = resources(
        zip_res(
            "https://x/2021-archive.zip",
            "2026-08-20T00:00:00+00:00",  # freshly retitled
            created_at="2021-07-21T00:00:00+00:00",
        ),
        zip_res(
            "https://x/current.zip",
            "2026-08-13T00:00:00+00:00",
            created_at="2026-08-13T00:00:00+00:00",
        ),
    )
    assert uu.newest_zip(session, HOST, "s")["url"] == "https://x/current.zip"


# --- the pin may not move backwards ---------------------------------------

OLD = zip_res("https://x/old.zip", None, created_at="2021-07-21T00:00:00+00:00")
NEW = zip_res("https://x/new.zip", None, created_at="2026-08-13T00:00:00+00:00")


def test_newer_resource_is_an_update():
    assert uu.pick_target([OLD, NEW], "https://x/old.zip") == (NEW, "update")


def test_already_pinned_to_newest_is_a_no_op():
    assert uu.pick_target([OLD, NEW], "https://x/new.zip") == (NEW, "current")


def test_a_metadata_touch_on_an_archive_leaves_the_pin_alone():
    """End to end for the downgrade case: the 2021 archive has the newest
    last_modified, but the pin does not move, because created_at decides."""
    touched = zip_res(
        "https://x/old.zip", "2026-08-20T00:00:00+00:00",
        created_at="2021-07-21T00:00:00+00:00",
    )
    current = zip_res(
        "https://x/new.zip", "2026-08-13T00:00:00+00:00",
        created_at="2026-08-13T00:00:00+00:00",
    )
    assert uu.pick_target([touched, current], "https://x/new.zip") == (current, "current")


def test_will_not_move_to_a_resource_no_newer_than_the_pin():
    """Backstop for ties: same publication instant, different URL, pin holds."""
    same = "2026-08-13T00:00:00+00:00"
    a = zip_res("https://x/a.zip", None, created_at=same)
    b = zip_res("https://x/b.zip", None, created_at=same)
    assert uu.pick_target([a, b], "https://x/b.zip")[1] == "not-newer"


def test_url_absent_from_the_dataset_is_allowed_to_move():
    """First adoption, or moving off an unrelated host, has nothing to compare
    against -- Luxembourg came from openov.lu this way."""
    assert uu.pick_target([OLD, NEW], "http://openov.lu/gtfs.zip") == (NEW, "update")


# --- feed discovery -------------------------------------------------------


@pytest.fixture
def feeds_dir(tmp_path):
    (tmp_path / "example.com.dmfr.json").write_text(json.dumps({"feeds": [
        {"id": "f-tagged", "tags": {"udata_host": HOST, "udata_dataset": "d"}},
        {"id": "f-host-only", "tags": {"udata_host": HOST}},
        {"id": "f-dataset-only", "tags": {"udata_dataset": "d"}},
        {"id": "f-untagged"},
    ]}))
    (tmp_path / "broken.dmfr.json").write_text("{not json")
    # Invalid UTF-8 raises UnicodeDecodeError, which is not a JSONDecodeError.
    (tmp_path / "mojibake.dmfr.json").write_bytes(b'{"feeds": [{"id": "\xff\xfe"}]}')
    return tmp_path


def test_only_fully_tagged_feeds_are_selected(feeds_dir):
    """Half-tagged feeds are skipped rather than guessed at, and neither an
    unparseable nor an undecodable file stops the scan."""
    found = [feed["id"] for _, _, feed in uu.tagged_feeds(feeds_dir)]
    assert found == ["f-tagged"]
