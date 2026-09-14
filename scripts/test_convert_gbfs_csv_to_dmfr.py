"""Tests for the GBFS converter's Onestop ID generation.

Run: cd scripts && uv run --with pytest pytest -q
"""

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load():
    """Import the script without running its network fetch at import time."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "convert-gbfs-csv-to-dmfr.py")
    src = open(path, encoding="utf-8").read()
    # everything above the requests.get call is the pure part under test
    head = src.split('r = requests.get(')[0]
    mod = {"__file__": path, "__name__": "gbfs_under_test"}
    exec(compile(head, path, "exec"), mod)
    return mod


gbfs = _load()


def test_underscores_become_word_breaks():
    # Python counts '_' as a word character, so the previous \\W+ rule kept it
    # and published f-vag_rad~nuremberg~gbfs.
    assert gbfs["mint_id"]("VAG_Rad", "Nuremberg") == "f-vag~rad~nuremberg~gbfs"


def test_name_component_is_alphanumerics_and_tildes_only():
    for name, location in [("VAG_Rad", "Nuremberg"), ("movemix_bike", "Halle (DE)"),
                           ("Bay Wheels", "San Francisco, CA"), ("Dott", "Wołomin")]:
        osid = gbfs["mint_id"](name, location)
        body = osid[2:]  # drop the 'f-' prefix
        assert all(c.isalnum() or c == "~" for c in body), osid
        assert "~~" not in osid, osid


def test_non_latin_names_are_kept():
    assert gbfs["mint_id"]("Dott", "Wołomin") == "f-dott~wołomin~gbfs"


def test_duplicate_words_are_collapsed():
    # The converter drops repeats so "Lime Lime City" does not stutter.
    assert gbfs["mint_id"]("Lime", "Lime City") == "f-lime~city~gbfs"


def test_published_ids_are_read_by_url(tmp_path):
    committed = tmp_path / "nabsa.dmfr.json"
    committed.write_text(json.dumps({"feeds": [
        {"id": "f-vag_rad~nuremberg~gbfs", "spec": "gbfs",
         "urls": {"gbfs_auto_discovery": "https://example.com/gbfs.json"}},
    ]}))
    assert gbfs["existing_ids_by_url"](str(committed)) == {
        "https://example.com/gbfs.json": "f-vag_rad~nuremberg~gbfs"}


def test_missing_or_broken_committed_file_is_not_fatal(tmp_path):
    assert gbfs["existing_ids_by_url"](str(tmp_path / "nope.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert gbfs["existing_ids_by_url"](str(broken)) == {}
