"""Tests for export-crosswalks.py that need no registry and no network."""

import importlib.util
import os
import sqlite3

SPEC = importlib.util.spec_from_file_location(
    "export_crosswalks",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "export-crosswalks.py"))
export_crosswalks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_crosswalks)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE current_operators (id INTEGER PRIMARY KEY, onestop_id TEXT,
            name TEXT, operator_tags TEXT);
        CREATE TABLE current_feeds (id INTEGER PRIMARY KEY, onestop_id TEXT,
            name TEXT, spec TEXT, feed_tags TEXT);
        CREATE TABLE current_operators_in_feed (id INTEGER PRIMARY KEY,
            operator_id INTEGER, feed_id INTEGER);
    """)
    return db


def test_operator_rows_carry_their_feeds():
    db = _db()
    db.execute("INSERT INTO current_operators VALUES (1, 'o-9q9-bart', 'Bay Area Rapid Transit', ?)",
               ('{"us_ntd_id": "90003"}',))
    db.execute("INSERT INTO current_feeds VALUES (1, 'f-9q9-bart', NULL, 'gtfs', NULL)")
    db.execute("INSERT INTO current_operators_in_feed VALUES (1, 1, 1)")
    rows = export_crosswalks.rows_for(db, {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator"})
    assert rows == [{
        "external_id": "90003", "onestop_id": "o-9q9-bart", "entity": "operator",
        "name": "Bay Area Rapid Transit", "related": "f-9q9-bart",
    }]


def test_ntd_ids_are_zero_padded_the_way_ntd_publishes_them():
    # Atlas has been inconsistent about padding, and the website shows the
    # padded form, so the export has to agree with what NTD itself writes.
    db = _db()
    db.execute("INSERT INTO current_operators VALUES (1, 'o-9px-coos', 'Coos County', ?)",
               ('{"us_ntd_id": "307"}',))
    rows = export_crosswalks.rows_for(db, {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator"})
    assert rows[0]["external_id"] == "00307"


def test_one_row_per_id_when_a_tag_holds_several():
    db = _db()
    db.execute("INSERT INTO current_operators VALUES (1, 'o-merged', 'Merged', ?)",
               ('{"us_ntd_id": "00001,00002"}',))
    rows = export_crosswalks.rows_for(db, {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator"})
    assert [r["external_id"] for r in rows] == ["00001", "00002"]


def test_feed_rows_borrow_an_operator_name():
    db = _db()
    db.execute("INSERT INTO current_feeds VALUES (1, 'f-x~rt', NULL, 'gtfs-rt', ?)",
               ('{"odpt_organization_id": "AkihaBusService", "odpt_dataset_id": "AllLines"}',))
    db.execute("INSERT INTO current_operators VALUES (1, 'o-x', 'Akiha Bus Service', NULL)")
    db.execute("INSERT INTO current_operators_in_feed VALUES (1, 1, 1)")
    rows = export_crosswalks.rows_for(db, {"name": "odpt", "tag": "odpt_organization_id",
                                           "entity": "feed", "secondary": "odpt_dataset_id"})
    assert rows[0]["name"] == "Akiha Bus Service"
    assert rows[0]["related"] == "o-x"
    assert rows[0]["odpt_dataset_id"] == "AllLines"


def test_rows_are_sorted_so_a_diff_shows_what_changed():
    db = _db()
    for i, (osid, ntd) in enumerate([("o-b", "00002"), ("o-a", "00001")], start=1):
        db.execute("INSERT INTO current_operators VALUES (?, ?, 'x', ?)",
                   (i, osid, '{"us_ntd_id": "%s"}' % ntd))
    rows = export_crosswalks.rows_for(db, {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator"})
    assert [r["external_id"] for r in rows] == ["00001", "00002"]


def test_every_registry_name_is_url_safe():
    # www-transit-land-v2 links to crosswalks/<name>.csv for each of these.
    for registry in export_crosswalks.REGISTRIES:
        assert registry["name"].replace("-", "").isalnum()
        assert registry["name"].islower()
