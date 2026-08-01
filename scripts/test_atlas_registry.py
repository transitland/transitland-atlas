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
