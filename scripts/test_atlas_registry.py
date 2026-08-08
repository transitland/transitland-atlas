"""Tests for the registry loader and the classification rules that were wrong
often enough to be worth pinning down.

Run: cd scripts && uv run --with pytest pytest -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_registry  # noqa: E402


@pytest.fixture
def feeds_dir(tmp_path):
    (tmp_path / "example.com.dmfr.json").write_text(json.dumps({
        "feeds": [
            {"id": "f-one", "spec": "gtfs",
             "urls": {"static_current": "https://example.com/a.zip",
                      "static_historic": ["https://example.com/old.zip"]},
             "tags": {"unstable_url": "true"},
             "operators": [{"onestop_id": "o-one",
                            "associated_feeds": [{"feed_onestop_id": "f-one~rt"}]}]},
            {"id": "f-one~rt", "spec": "gtfs-rt",
             "urls": {"realtime_vehicle_positions": "https://example.com/vp.pb"}},
            {"id": "f-orphan", "spec": "gtfs",
             "urls": {"static_current": "https://example.com/b.zip"}},
        ]}))
    (tmp_path / "other.com.dmfr.json").write_text(json.dumps({
        "feeds": [{"id": "f-two", "spec": "gtfs",
                   "urls": {"static_current": "https://example.com/a.zip"}}],
        "operators": [{"onestop_id": "o-two",
                       "associated_feeds": [{"feed_onestop_id": "f-two"}]}]}))
    return str(tmp_path)


def test_loads_feeds(feeds_dir):
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.feed_exists(db, "f-one")
    assert not atlas_registry.feed_exists(db, "f-missing")
    assert atlas_registry.feed_count(db) == 4


def test_malformed_registry_fails_loudly(tmp_path):
    """transitland sync rejects the batch, which is better than silently skipping."""
    (tmp_path / "broken.dmfr.json").write_text("{not json")
    with pytest.raises(SystemExit):
        atlas_registry.load(str(tmp_path))


def test_missing_directory_fails_loudly(tmp_path):
    with pytest.raises(SystemExit):
        atlas_registry.load(str(tmp_path / "nope"))


def test_historic_urls_are_not_in_use(feeds_dir):
    """A URL kept in static_historic is deliberately retained, not registered."""
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.feeds_using(db, "https://example.com/old.zip") == set()


def test_same_url_can_belong_to_several_feeds(feeds_dir):
    """The case that broke the first contradiction check: one URL, two owners."""
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.feeds_using(db, "https://example.com/a.zip") == {"f-one", "f-two"}


def test_operator_links_cover_nested_and_associated(feeds_dir):
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.operator_feeds(db, "o-one") == {"f-one", "f-one~rt"}
    assert atlas_registry.operators_of(db, "f-one~rt") == {"o-one"}
    assert atlas_registry.operators_of(db, "f-orphan") == set()


def test_unstable_feeds_resolve_through_the_operator(feeds_dir):
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.unstable_feeds_of(db, "o-one") == {"f-one"}
    assert atlas_registry.unstable_feeds_of(db, "o-two") == set()


def test_file_of_feed_maps_to_its_dmfr(feeds_dir):
    """Secrets match on DMFR filename as well as feed_id."""
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.file_of_feed(db)["f-two"] == "other.com.dmfr.json"


def test_split_ids_accepts_either_separator():
    """A tag holding several external ids may use commas or semicolons.

    No external id we carry contains either character, so accepting both means a
    tag written with the wrong one still joins instead of silently matching
    nothing.
    """
    assert atlas_registry.split_ids("90270,90271") == ["90270", "90271"]
    assert atlas_registry.split_ids("90270;90271") == ["90270", "90271"]
    assert atlas_registry.split_ids("90270; 90271 , 90272") == ["90270", "90271", "90272"]
    assert atlas_registry.split_ids("90270") == ["90270"]
    # trailing separators and empty segments are dropped, not returned blank
    assert atlas_registry.split_ids("90270,,;90271;") == ["90270", "90271"]
    assert atlas_registry.split_ids("") == []
    assert atlas_registry.split_ids(None) == []
    assert atlas_registry.split_ids("   ") == []


# --- normalise_url ---------------------------------------------------------
# Every match decision in every discovery source runs through this, so the
# collapses it makes and the ones it refuses are both worth pinning.

@pytest.mark.parametrize("a,b", [
    ("https://example.com/a.zip", "http://example.com/a.zip"),          # scheme
    ("https://example.com/a.zip", "https://www.example.com/a.zip"),      # www.
    ("https://example.com/a/", "https://example.com/a"),                 # trailing slash
    ("https://EXAMPLE.com/A.zip", "https://example.com/a.zip"),          # host and path case
    ("  https://example.com/a.zip  ", "https://example.com/a.zip"),      # surrounding space
    ("example.com/a.zip", "https://example.com/a.zip"),                  # missing scheme
])
def test_normalise_url_collapses_differences_that_do_not_change_the_file(a, b):
    assert atlas_registry.normalise_url(a) == atlas_registry.normalise_url(b)


@pytest.mark.parametrize("a,b", [
    # A trailing dot is a different path, and a real submission once differed from
    # the registered URL by exactly this. Guessing here would merge two URLs that
    # a human needs to see separately.
    ("https://example.com/a.zip.", "https://example.com/a.zip"),
    ("https://example.com/a.zip?v=1", "https://example.com/a.zip"),      # query is significant
    ("https://example.com/a.zip", "https://example.com/b.zip"),
    ("https://example.com/a.zip", "https://other.com/a.zip"),
    # www. is stripped only as a prefix, never mid-host
    ("https://www.example.com/a", "https://example.www.com/a"),
])
def test_normalise_url_keeps_differences_that_might_matter(a, b):
    assert atlas_registry.normalise_url(a) != atlas_registry.normalise_url(b)


def test_normalise_url_handles_empty_input():
    assert atlas_registry.normalise_url("") == ""
    assert atlas_registry.normalise_url(None) == ""


# --- normalise_ntd_id ------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1", "00001"),
    ("90252", "90252"),
    (" 123 ", "00123"),
    ("", ""),
    ("R-12", "R-12"),      # not all digits, so left alone rather than padded
])
def test_normalise_ntd_id(raw, expected):
    assert atlas_registry.normalise_ntd_id(raw) == expected


def test_operator_feeds_can_filter_by_spec(feeds_dir):
    db = atlas_registry.load(feeds_dir)
    assert atlas_registry.operator_feeds(db, "o-one") == {"f-one", "f-one~rt"}
    assert atlas_registry.operator_feeds(db, "o-one", spec="gtfs-rt") == {"f-one~rt"}
    assert atlas_registry.operator_feeds(db, "o-one", spec="gtfs") == {"f-one"}
