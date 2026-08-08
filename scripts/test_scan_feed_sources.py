"""Tests for the pure helpers in scan-feed-sources.

Only the offline parts: extraction from a fetched body, and policy matching.
The sources themselves talk to the network and to the registry, and are
exercised by running them.

Imported by path because the script is named with hyphens and so is not a
importable module name.

Run: cd scripts && uv run --with pytest pytest -q
"""

import importlib.util
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("scan_feed_sources", HERE / "scan-feed-sources.py")
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


# --- feed-list extraction --------------------------------------------------
# A catalogue that yields nothing looks identical to a catalogue that has not
# changed, which is how a real source sat silently unwatched: MnDOT's hub
# contains no absolute URLs at all, so URL extraction returned an empty set for
# a perfectly healthy page.

def test_urls_extracted_from_html_anchors():
    body = '''<a href="https://example.com/gtfs.zip">feed</a>
               <a href="https://example.com/about.html">about</a>'''
    assert scan._feed_list_urls(body) == ["https://example.com/gtfs.zip"]


def test_urls_extracted_from_plain_text_and_csv():
    body = "agency,url\nExample,https://example.com/google_transit.zip\n"
    assert scan._feed_list_urls(body) == ["https://example.com/google_transit.zip"]


@pytest.mark.parametrize("url", [
    "https://example.com/logo.png",
    "https://example.com/app.js",
    "https://example.com/style.css",
    "https://example.com/timetable.pdf",
])
def test_assets_that_match_the_hint_are_still_excluded(url):
    # "download" and "gtfs" appear in plenty of asset paths.
    assert scan._feed_list_urls(f'<a href="{url}">x</a>') == []


def test_urls_are_deduplicated_and_sorted():
    body = ('https://b.example.com/gtfs.zip https://a.example.com/gtfs.zip '
            'https://b.example.com/gtfs.zip')
    assert scan._feed_list_urls(body) == [
        "https://a.example.com/gtfs.zip", "https://b.example.com/gtfs.zip"]


def test_trailing_punctuation_is_stripped_from_prose_urls():
    body = "The feed is at https://example.com/gtfs.zip, updated weekly."
    assert scan._feed_list_urls(body) == ["https://example.com/gtfs.zip"]


def test_json_values_extraction_finds_feeds_identified_by_id():
    # The MnDOT shape: identifiers, no URLs anywhere.
    body = ('[{"feed_id":"browncounty-mn-us","fileNames":["brown_county.zip"]},'
            '{"feed_id":"metro-mn-us","fileNames":["metro.zip"]}]')
    assert scan._feed_list_json_values(body) == [
        "brown_county.zip", "browncounty-mn-us", "metro-mn-us", "metro.zip"]


def test_json_values_walks_nested_structures():
    body = '{"a":{"b":["x",{"c":"y"}]},"d":"z"}'
    assert scan._feed_list_json_values(body) == ["x", "y", "z"]


def test_json_values_rejects_non_json_loudly():
    # Better to report a source as unreadable than to record an empty set and
    # report every feed as removed on the next run.
    with pytest.raises(ValueError):
        scan._feed_list_json_values("<html>not json</html>")


def test_url_extraction_returns_nothing_for_the_json_shape():
    # Pins the bug that motivated json-values mode: this page is healthy, and
    # URL extraction still finds nothing.
    body = '[{"feed_id":"browncounty-mn-us","fileNames":["brown_county.zip"]}]'
    assert scan._feed_list_urls(body) == []


# --- policy matching -------------------------------------------------------

def _policies():
    return [
        {"name": "by-operator", "operators": {"o-one"}, "url_prefixes": ()},
        {"name": "by-url", "operators": set(),
         "url_prefixes": ("https://api.example.org/transit/",)},
        # A policy that states a position rather than a place: it names no feeds
        # and no prefixes, so it must never match anything by accident.
        {"name": "position-only", "operators": set()},
    ]


def test_policy_matches_on_operator():
    assert scan.policy_for(_policies(), {"o-one"})["name"] == "by-operator"


def test_policy_matches_on_url_prefix():
    p = scan.policy_for(_policies(), set(), "https://api.example.org/transit/x/gtfs")
    assert p["name"] == "by-url"


def test_policy_url_match_ignores_scheme_and_host_case():
    p = scan.policy_for(_policies(), set(), "HTTP://API.Example.ORG/transit/x/gtfs")
    assert p["name"] == "by-url"


def test_policy_accepts_a_bare_string_of_urls():
    assert scan.policy_for(_policies(), set(), "https://elsewhere.example/x") is None


def test_policy_without_feeds_or_prefixes_never_matches():
    # position-only is last, so anything reaching it would return it.
    assert scan.policy_for(_policies(), {"o-unknown"}, ()) is None
    assert scan.policy_for(_policies(), set(), "https://nothing.example/") is None


def test_policy_returns_none_when_nothing_applies():
    assert scan.policy_for([], {"o-one"}, "https://example.com/x") is None
