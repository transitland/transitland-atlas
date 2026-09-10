"""Tests for the helpers in validate-changed-feed-urls.py that were wrong often
enough to be worth pinning down.

Run: cd scripts && uv run --with pytest pytest -q
"""

import importlib.util
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The script has a hyphenated name, so it cannot be imported by name.
_spec = importlib.util.spec_from_file_location(
    "validate_changed_feed_urls",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "validate-changed-feed-urls.py"),
)
vcfu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcfu)


def test_associated_feed_ids_reads_both_operator_shapes(tmp_path):
    """Operators live either at registry level or nested in a feed, and both
    associate RT feeds. Reading only the registry-level array made this advise
    that correctly associated RT feeds were unassociated.
    """
    (tmp_path / "nested.com.dmfr.json").write_text(json.dumps({
        "feeds": [
            {"id": "f-nested", "spec": "gtfs",
             "urls": {"static_current": "https://example.com/a.zip"},
             "operators": [{"onestop_id": "o-nested",
                            "associated_feeds": [
                                {"feed_onestop_id": "f-nested"},
                                {"feed_onestop_id": "f-nested~rt"}]}]},
            {"id": "f-nested~rt", "spec": "gtfs-rt",
             "urls": {"realtime_vehicle_positions": "https://example.com/vp.pb"}},
        ]}))
    (tmp_path / "toplevel.com.dmfr.json").write_text(json.dumps({
        "feeds": [{"id": "f-top", "spec": "gtfs",
                   "urls": {"static_current": "https://example.com/b.zip"}},
                  {"id": "f-top~rt", "spec": "gtfs-rt",
                   "urls": {"realtime_alerts": "https://example.com/al.pb"}}],
        "operators": [{"onestop_id": "o-top",
                       "associated_feeds": [{"feed_onestop_id": "f-top~rt"}]}]}))

    got = vcfu.load_operator_associated_feed_ids(pathlib.Path(tmp_path))
    assert got == {"f-nested", "f-nested~rt", "f-top~rt"}


def test_associated_feed_ids_tolerates_missing_and_malformed(tmp_path):
    (tmp_path / "empty.com.dmfr.json").write_text(json.dumps({"feeds": []}))
    (tmp_path / "broken.com.dmfr.json").write_text("{not json")
    (tmp_path / "no-ops.com.dmfr.json").write_text(json.dumps({
        "feeds": [{"id": "f-x", "spec": "gtfs", "urls": {}}]}))

    assert vcfu.load_operator_associated_feed_ids(pathlib.Path(tmp_path)) == set()
    assert vcfu.load_operator_associated_feed_ids(pathlib.Path(tmp_path / "nope")) == set()
