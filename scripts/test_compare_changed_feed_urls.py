"""Tests for the pure helpers in compare-changed-feed-urls.py.

Run: uv run --with pytest pytest scripts/test_compare_changed_feed_urls.py -q
"""

import importlib.util
import json
import os
from datetime import date

# The script has a hyphenated name, so it cannot be imported by name.
_spec = importlib.util.spec_from_file_location(
    "compare_changed_feed_urls",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "compare-changed-feed-urls.py"),
)
ccfu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccfu)

TODAY = date(2026, 9, 29)


def feed(fid, url, **extra):
    return {"id": fid, "spec": "gtfs", "urls": {"static_current": url}, **extra}


def test_repointed_feeds_finds_only_changed_urls():
    base = {"feeds": [feed("f-a", "https://old/a.zip"), feed("f-b", "https://same/b.zip")]}
    head = {"feeds": [feed("f-a", "https://new/a.zip"), feed("f-b", "https://same/b.zip")]}
    got = ccfu.repointed_feeds(base, head)
    assert [(p.feed_id, p.old_url, p.new_url) for p in got] == [
        ("f-a", "https://old/a.zip", "https://new/a.zip")]


def test_repointed_feeds_ignores_new_feeds_and_new_files():
    head = {"feeds": [feed("f-new", "https://new/a.zip")]}
    assert ccfu.repointed_feeds({"feeds": []}, head) == []
    assert ccfu.repointed_feeds(None, head) == []


def test_repointed_feeds_follows_rename():
    base = {"feeds": [feed("f-old-id", "https://old/a.zip")]}
    head = {"feeds": [feed("f-new-id", "https://new/a.zip", supersedes_ids=["f-old-id"])]}
    (p,) = ccfu.repointed_feeds(base, head)
    assert (p.feed_id, p.old_url) == ("f-new-id", "https://old/a.zip")


def test_repointed_feeds_carries_auth():
    base = {"feeds": [feed("f-a", "https://old/a.zip")]}
    head = {"feeds": [feed("f-a", "https://new/a.zip", authorization={"type": "query_param"})]}
    (p,) = ccfu.repointed_feeds(base, head)
    assert p.auth_type == "query_param"


def probe(**kw):
    defaults = dict(url="u", ok=True, dir_sha1="d", sha1="s", agencies=["A"],
                    routes=10, earliest="2026-01-01", latest="2026-12-31")
    return ccfu.Probe(**{**defaults, **kw})


def test_recommend_branches():
    ok = probe()
    bad = probe(ok=False, error="boom")
    assert "Neither" in ccfu.recommend(bad, bad, TODAY)[0]
    assert "Keep the current" in ccfu.recommend(ok, bad, TODAY)[0]
    assert "Switch to the proposed" in ccfu.recommend(bad, ok, TODAY)[0]
    assert "identical" in ccfu.recommend(ok, probe(), TODAY)[0]
    assert "repackaged" in ccfu.recommend(ok, probe(sha1="other"), TODAY)[0]
    newer = probe(dir_sha1="x", latest="2027-06-01")
    assert "newer" in ccfu.recommend(ok, newer, TODAY)[0]
    assert "older" in ccfu.recommend(newer, ok, TODAY)[0]


def test_recommend_notes():
    old = probe(routes=40)
    new = probe(dir_sha1="x", agencies=["B"], routes=5, latest="2026-09-01")
    _, notes = ccfu.recommend(old, new, TODAY)
    text = " ".join(notes)
    assert "No agency names in common" in text
    assert "Route count drops" in text
    assert "expired" in text


def test_md_escapes_untrusted_cell_text():
    s = ccfu.md("a|b<script>`x`\nnext")
    assert "|" not in s.replace("\\|", "")
    assert "<" not in s and "`" not in s and "\n" not in s


def test_short_error_keeps_cause():
    stderr = ("Error: could not open reader 'https://x/a.zip': Get \"https://x/a.zip\": "
              "tls: failed to verify certificate: x509: certificate has expired")
    assert ccfu.short_error(stderr) == "tls: failed to verify certificate: x509: certificate has expired"
    assert ccfu.short_error("Error: could not open reader 'https://x/f': file does not exist") == "file does not exist"
    assert ccfu.short_error("") == "command failed"


def test_probe_json_round_trip():
    """render rebuilds probes with Probe(**d), so every field must survive JSON."""
    p = probe(route_types={"Bus": 3, "Tram": 1})
    assert ccfu.Probe(**json.loads(json.dumps(ccfu.asdict(p)))) == p


def test_lookup_archive_refuses_malformed_sha1(monkeypatch):
    """The SHA1 comes from untrusted probe output and goes into a request
    that carries the API key, so anything but 40 hex digits is dropped
    before any request is made."""
    def boom(*a, **kw):
        raise AssertionError("request made")
    monkeypatch.setattr(ccfu.urllib.request, "urlopen", boom)
    for bad in ("", "abc", "../../x", "0" * 40 + "?x=1", "G" * 40):
        assert ccfu.lookup_archive(bad, "key") is None
    assert ccfu.lookup_archive("0" * 40, "") is None


def test_summarize_uses_the_shared_pass_rule():
    """Both workflows judge a URL by vcfu.static_failure, so they can't disagree."""
    no_agency = {"success": True, "details": {"files": [{"name": "stops.txt", "rows": 3}],
                                              "agencies": [{"agency_name": "A"}]}}
    p = ccfu.summarize("u", no_agency, None)
    assert (p.ok, p.error) == (False, "no agency records")
    good = {"success": True, "details": {"files": [{"name": "agency.txt", "rows": 1},
                                                   {"name": "routes.txt", "rows": 7}]}}
    p = ccfu.summarize("u", good, None)
    assert (p.ok, p.routes, p.route_types) == (True, 7, {})
