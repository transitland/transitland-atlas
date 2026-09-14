"""Tests for the Onestop ID minted from a GBFS system's name.

These ids are re-derived on every run and are not maintained across upstream
renames — stability and supersedes lineage matter for GTFS static, somewhat
for GTFS Realtime, and not for GBFS — so there is nothing here about
preserving a previously published id.

Run: cd scripts && uv run --with pytest pytest -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load():
    """Import the pure part without running the module's network fetch."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "convert-gbfs-csv-to-dmfr.py")
    src = open(path, encoding="utf-8").read()
    head = src.split("r = requests.get(")[0]
    mod = {"__file__": path, "__name__": "gbfs_under_test"}
    exec(compile(head, path, "exec"), mod)
    return mod


gbfs = _load()
mint = gbfs["mint_id"]


def test_underscores_become_word_breaks():
    # Python counts '_' as a word character, so the previous \\W+ rule kept it
    # and published f-vag_rad~nuremberg~gbfs.
    assert mint("VAG_Rad", "Nuremberg") == "f-vag~rad~nuremberg~gbfs"


def test_trailing_punctuation_does_not_leave_an_empty_segment():
    # "Grünheide (Mark)" ended the name on punctuation, which split to an
    # empty word and published f-edeka~grünheide~mark~~gbfs.
    assert mint("EDEKA", "Grünheide (Mark)") == "f-edeka~grünheide~mark~gbfs"


def test_name_component_is_alphanumerics_and_tildes_only():
    for name, location in [("VAG_Rad", "Nuremberg"), ("movemix_bike", "Halle (DE)"),
                           ("Bay Wheels", "San Francisco, CA"), ("Dott", "Wołomin"),
                           ("BiziBizkaia 4.0", "Bilbao (BK)")]:
        osid = mint(name, location)
        assert all(c.isalnum() or c == "~" for c in osid[2:]), osid
        assert "~~" not in osid and not osid.endswith("~"), osid


def test_non_latin_names_are_kept():
    assert mint("Dott", "Wołomin") == "f-dott~wołomin~gbfs"


def test_duplicate_words_are_collapsed():
    assert mint("Lime", "Lime City") == "f-lime~city~gbfs"
