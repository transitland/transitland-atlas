"""Tests for pruning feeds that a Japanese source no longer lists.

Run: uv run --with pytest pytest external-data-for-reference/ -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jp_dmfr_prune import prune_feeds


def dmfr(*ids):
    feeds = []
    for fid in ids:
        feeds.append({"id": fid, "spec": "gtfs", "urls": {"static_current": fid}})
        feeds.append({"id": f"{fid}~rt", "spec": "gtfs-rt", "urls": {}})
    return {
        "feeds": feeds,
        "operators": [
            {"onestop_id": f"o-{fid}", "associated_feeds": [
                {"feed_onestop_id": fid}, {"feed_onestop_id": f"{fid}~rt"}]}
            for fid in ids],
    }


def test_removes_unlisted_feed_with_rt_and_operator():
    d = dmfr("f-a", "f-b", "f-c", "f-d", "f-e")
    removed = prune_feeds(d, lambda f: f["id"] != "f-a")
    assert removed == ["f-a", "f-a~rt"]
    assert "f-a" not in {f["id"] for f in d["feeds"]}
    assert "o-f-a" not in {o["onestop_id"] for o in d["operators"]}
    assert len(d["operators"]) == 4


def test_keeps_records_it_cannot_map():
    d = dmfr("f-a", "f-b")
    assert prune_feeds(d, lambda f: None) == []
    assert len(d["feeds"]) == 4


def test_refuses_mass_removal():
    d = dmfr("f-a", "f-b", "f-c", "f-d")
    assert prune_feeds(d, lambda f: f["id"] in ("f-a",)) == []
    assert len(d["feeds"]) == 8
