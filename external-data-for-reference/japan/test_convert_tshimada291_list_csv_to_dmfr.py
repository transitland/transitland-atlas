"""Tests for the Onestop ID minted from a feed's Japanese label.

Run: uv run --with pytest pytest external-data-for-reference/ -q
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "convert_tshimada291", os.path.join(_HERE, "convert-tshimada291-list-csv-to-dmfr.py"))
conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conv)


def mint(label, existing=None):
    # create_dmfr_record appends to the dmfr it is given and returns nothing.
    dmfr = {"feeds": []} if existing is None else existing
    conv.create_dmfr_record("https://example.com/a.zip", label, "CC BY 4.0", dmfr)
    return dmfr["feeds"][-1]["id"]


def test_name_component_is_alphanumerics_and_tildes_only():
    """The scheme allows no other punctuation.

    Enumerating characters to strip missed the fullwidth forms: ～ (U+FF5E) is
    a different character from the 〜 (U+301C) that was handled, so ＊, ． and
    ［］ all reached published ids.
    """
    for label in ["おのみちバス20250924～", "さかわ・おち花＊花ループバス", "仙台市営バス2026.3.2～",
                  "境港市 循環バス～202503", "［HODaP］南富良野町コミュニティ",
                  "下津井電鉄株式会社路線バス GTFS-RUライセンス版"]:
        osid = mint(label)
        assert all(c.isalnum() or c == "~" for c in osid[2:]), osid
        assert "~~" not in osid and not osid.endswith("~"), osid


def test_hyphen_in_label_does_not_become_a_geohash_segment():
    # f-下津井電鉄株式会社路線バス~gtfs-ruライセンス版 parses as geohash 'gtfs' + name,
    # because a Onestop ID splits on hyphens.
    osid = mint("下津井電鉄株式会社路線バス GTFS-RUライセンス版")
    assert osid.count("-") == 1, osid


def test_bracketed_annotations_are_dropped_in_both_widths():
    assert mint("[gtfs-data] 普通のバス") == "f-普通のバス"
    assert mint("［HODaP］南富良野町コミュニティ") == "f-南富良野町コミュニティ"


def test_japanese_characters_are_preserved():
    assert mint("境港市 循環バス") == "f-境港市~循環バス"


def test_collisions_get_a_numeric_suffix():
    existing = {"feeds": [{"id": "f-普通のバス"}]}
    assert mint("普通のバス", existing) == "f-普通のバス~1"
