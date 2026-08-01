#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///

"""Scan feed sources and report findings worth acting on.

Sources are of two kinds. **State monitors** describe the current condition of
feeds already registered: a finding stays valid until someone fixes it, so it
should not be suppressed. **Discovery sources** propose candidates from outside
the registry and repeat on every run until a decision is recorded in
evaluations/, so suppression is what makes them converge. See
evaluations/README.md.

Sources available, all monitors so far and all read-only:

  fetch-errors    every feed the API reports as failing to fetch, grouped by
                  host. Not limited to tagged feeds.
  unstable-urls   feeds tagged unstable_url: staleness, calendar expiry and
                  fetch state.
  watch-pages     the public page on which an agency publishes its own feed
                  URL, as recorded in evaluations/. One request per page, no
                  following of links.

Two ideas keep the output usable.

**Cluster before reporting.** Many feeds failing on one host is usually one
event, not many problems.

**Judge transience from fetch history, not from feed version age.** Transitland
retains recent fetch attempts, so a failure streak is directly observable. Age
of the last feed version is a poor proxy: a feed can have produced one three
weeks ago and have failed every attempt since. A short streak following a
success is a network blip or a producer error the next version fixes, and is
held back. Cluster size and failure pattern together say which case you are in:
a cluster that all broke at once is a vendor outage, a cluster failing
throughout the retained history is a restructure or consolidation.

Needs TRANSITLAND_API_KEY except for watch-pages. This talks to the network, so
it is a reporting tool rather than something to gate CI on.

Usage:
  cd scripts && uv run scan-feed-sources.py
  cd scripts && uv run scan-feed-sources.py --source fetch-errors --min-cluster 5
  cd scripts && uv run scan-feed-sources.py --source all --out ../scan-output
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evaluations")
API = "https://transit.land/api/v2/query"

FETCH_WINDOW = 20

FEED_FIELDS = f"""
    id
    onestop_id
    urls {{ static_current realtime_vehicle_positions }}
    feed_state {{ feed_version {{ fetched_at latest_calendar_date }} }}
    feed_fetches(limit: {FETCH_WINDOW}) {{ fetched_at success response_code fetch_error }}
"""

LINK_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
ARCHIVE = re.compile(r"\.zip(\?|$)", re.I)
FEEDISH = re.compile(r"(gtfs|google_transit)", re.I)
NOISE = re.compile(r"(/wp-json/|/oembed|/feed/?$|\?replytocom=)", re.I)


# --------------------------------------------------------------------- shared

def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value):
    ts = parse_ts(value)
    return None if ts is None else (now() - ts).days


def fetch_all(key: str, where: str) -> list[dict]:
    """Page through every feed matching a GraphQL `where` clause."""
    query = f"query($after: Int) {{ feeds(limit: 1000, after: $after, {where}) {{{FEED_FIELDS}}} }}"
    rows, after = [], 0
    while True:
        r = requests.post(API, headers={"apikey": key},
                          json={"query": query, "variables": {"after": after}}, timeout=120)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise SystemExit(f"API error: {body['errors'][0]['message']}")
        feeds = body["data"]["feeds"]
        if not feeds:
            return rows
        rows.extend(feeds)
        after = feeds[-1]["id"]


def summarise(f: dict) -> dict:
    """Flatten one API feed record into the fields every source reports on."""
    fv = ((f.get("feed_state") or {}).get("feed_version")) or {}
    fetches = f.get("feed_fetches") or []
    last = fetches[0] if fetches else {}
    urls = f.get("urls") or {}
    url = urls.get("static_current") or urls.get("realtime_vehicle_positions") or ""

    latest_cal = fv.get("latest_calendar_date")
    expired = None
    if latest_cal:
        try:
            expired = datetime.fromisoformat(latest_cal).date() < now().date()
        except ValueError:
            pass

    reason = None
    if last and last.get("success") is False:
        code = last.get("response_code")
        if code is None:
            reason = "unreachable"
        elif code == 200:
            # 200 with an unusable body: a parked domain, an SPA catch-all,
            # or an error page served with the wrong status.
            reason = "200-not-a-feed"
        else:
            reason = f"http-{code}"

    # Fetch history is the honest transience signal. Age of the last feed
    # version is not: a feed can have produced one three weeks ago and have
    # failed every attempt since.
    streak = 0
    for attempt in fetches:
        if attempt.get("success"):
            break
        streak += 1
    successes = sum(1 for a in fetches if a.get("success"))
    if not fetches:
        pattern = "unknown"
    elif streak >= len(fetches):
        pattern = "always-failing"
    elif successes and streak <= 2:
        pattern = "just-broke" if streak else "recovered"
    elif successes:
        pattern = "intermittent"
    else:
        pattern = "always-failing"

    return {
        "onestop_id": f["onestop_id"],
        "url": url,
        "host": (urlparse(url).netloc or "(no url)").lower(),
        "days_since_new_version": days_since(fv.get("fetched_at")),
        "latest_calendar_date": latest_cal,
        "calendar_expired": expired,
        "fetch_failure": reason,
        "fail_streak": streak,
        "window": len(fetches),
        "window_successes": successes,
        "pattern": pattern,
    }


def cluster(rows: list[dict], min_cluster: int):
    """Split rows into host clusters and singletons, biggest cluster first."""
    by_host = collections.defaultdict(list)
    for r in rows:
        by_host[r["host"]].append(r)
    clusters = sorted((v for v in by_host.values() if len(v) >= min_cluster),
                      key=len, reverse=True)
    loose = [r for v in by_host.values() if len(v) < min_cluster for r in v]
    return clusters, sorted(loose, key=lambda r: -(r["days_since_new_version"] or 0))


def describe_cluster(rows: list[dict]) -> str:
    """Read a cluster's failure pattern to guess which kind of event it is."""
    streaks = [r["fail_streak"] for r in rows]
    kinds = collections.Counter(r["pattern"] for r in rows)
    if kinds.get("just-broke", 0) == len(rows):
        return f"every feed broke within the last {max(streaks)} fetch(es): likely a transient vendor outage"
    if kinds.get("always-failing", 0) == len(rows):
        return "failing throughout the retained history: a restructure, consolidation or dead host"
    if kinds.get("intermittent", 0) > len(rows) / 2:
        return "mostly intermittent: an unreliable host rather than a moved feed"
    return "mixed: " + ", ".join(f"{k}={v}" for k, v in kinds.most_common())


# -------------------------------------------------------------------- sources

def source_fetch_errors(key: str, args) -> dict:
    rows = [summarise(f) for f in fetch_all(key, "where: {fetch_error: true}")]
    total = len(rows)
    transient = [r for r in rows
                 if r["pattern"] in ("just-broke", "recovered")
                 and r["fail_streak"] <= args.transient_streak]
    rows = [r for r in rows if r not in transient]
    clusters, loose = cluster(rows, args.min_cluster)

    print(f"fetch-errors: {total} feeds failing to fetch")
    print(f"  {len(transient)} broke within the last {args.transient_streak} fetch(es) after succeeding, "
          f"held back as transient")
    print(f"  {len(clusters)} host cluster(s) of {args.min_cluster}+, covering "
          f"{sum(len(c) for c in clusters)} feeds; {len(loose)} elsewhere\n")
    for c in clusters:
        codes = collections.Counter(r["fetch_failure"] for r in c)
        print(f"  {len(c):>4}  {c[0]['host']}")
        print(f"        {describe_cluster(c)}; {dict(codes)}")
    if loose:
        print(f"\n  individual failures, longest streak first:")
        for r in sorted(loose, key=lambda r: (-r["fail_streak"], r["onestop_id"]))[:12]:
            print(f"        {r['fail_streak']:>2}/{r['window']:<2} {r['pattern']:<15} "
                  f"{r['onestop_id'][:42]:44s} {r['fetch_failure']}")
    return {"total": total, "held_back_transient": transient,
            "clusters": [{"host": c[0]["host"], "count": len(c),
                          "reading": describe_cluster(c), "feeds": c} for c in clusters],
            "singletons": loose}


def source_unstable_urls(key: str, args) -> dict:
    """Feeds whose URL is known to move: is it still fetching, and still current?

    Deliberately does not flag "no new version in N days". A feed can go a year
    without being republished simply because the operator did not change the
    schedule, which is indistinguishable from a dead feed by that measure and
    was about 70% of what it reported. What matters is whether the feed still
    fetches, and whether the data it serves has run out.
    """
    rows = [summarise(f) for f in fetch_all(key, 'where: {tags: {unstable_url: "true"}}')]
    failing = [r for r in rows if r["fetch_failure"]]
    expired = [r for r in rows if not r["fetch_failure"] and r["calendar_expired"]]
    healthy = [r for r in rows if not r["fetch_failure"] and not r["calendar_expired"]]

    print(f"unstable-urls: {len(rows)} tagged feeds")
    print(f"  {len(healthy)} fetching with a current calendar, not reported")
    print(f"  {len(expired)} fetching but serving an expired calendar")
    print(f"  {len(failing)} failing to fetch\n")

    if expired:
        print("  serving an expired calendar, oldest first:")
        for r in sorted(expired, key=lambda r: r["latest_calendar_date"] or "")[:15]:
            print(f"      cal_end {r['latest_calendar_date']}  {r['onestop_id'][:48]}")
    if failing:
        print("\n  failing to fetch, longest streak first:")
        for r in sorted(failing, key=lambda r: -r["fail_streak"])[:15]:
            print(f"      {r['fail_streak']:>2}/{r['window']:<2} {r['pattern']:<15} "
                  f"{r['onestop_id'][:42]:44s} {r['fetch_failure']}")
    return {"total": len(rows), "expired": expired, "failing": failing,
            "healthy_count": len(healthy)}


def source_watch_pages(key, args) -> dict:
    entries = []
    for path in sorted(glob.glob(os.path.join(EVAL_DIR, "o-*.json"))):
        doc = json.load(open(path))
        for w in doc.get("watch") or []:
            entries.append({"operator_onestop_id": doc.get("operator_onestop_id"), **w})

    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas-scan-feed-sources/1.0"
    results = []
    print(f"watch-pages: {len(entries)} page(s)\n")
    for entry in entries:
        page = entry["page"]
        row = {k: entry.get(k) for k in ("page", "operator_onestop_id", "publishes",
                                         "last_seen_url", "last_checked", "note")}
        try:
            r = session.get(page, timeout=60)
            row["status"] = r.status_code
        except Exception as e:
            row["status"], row["error"] = None, type(e).__name__
            print(f"  {page}  UNREACHABLE ({row['error']})")
            results.append(row)
            continue
        if r.status_code != 200:
            print(f"  {page}  HTTP {r.status_code}")
            results.append(row)
            continue

        all_links = {urljoin(page, h.split("#")[0]) for h in LINK_RE.findall(r.text)}
        found = sorted(u for u in all_links
                       if u.rstrip("/") != page.rstrip("/") and not NOISE.search(u)
                       and (ARCHIVE.search(u) or FEEDISH.search(u)))
        row["feed_links_found"] = found
        bits = [f"{len(found)} feed-shaped link(s)"]
        if entry.get("last_seen_url"):
            present = entry["last_seen_url"] in all_links
            row["last_seen_url_still_present"] = present
            bits.append("last_seen_url present" if present else "LAST SEEN URL GONE")
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            slug = re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-")[:120]
            with open(os.path.join(args.out, f"{slug}.html"), "w", encoding="utf-8") as fh:
                fh.write(r.text)
        print(f"  {page}  200  {'; '.join(bits)}")
        for link in found[:5]:
            print(f"      {link}")
        results.append(row)
    return {"pages": results}


SOURCES = {
    "fetch-errors": (source_fetch_errors, True),
    "unstable-urls": (source_unstable_urls, True),
    "watch-pages": (source_watch_pages, False),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", choices=list(SOURCES) + ["all"],
                    help="repeatable; defaults to all")
    ap.add_argument("--min-cluster", type=int, default=5,
                    help="host cluster size to report as one finding (default 5)")
    ap.add_argument("--transient-streak", type=int, default=2,
                    help="hold back failures with a streak this short that follow a success (default 2)")
    ap.add_argument("--out", help="directory for the report and a copy of each page consulted")
    args = ap.parse_args()

    wanted = list(SOURCES) if (not args.source or "all" in args.source) else args.source
    key = os.environ.get("TRANSITLAND_API_KEY", "")
    if any(SOURCES[s][1] for s in wanted) and not key:
        print("ERROR: TRANSITLAND_API_KEY is not set")
        return 2

    report = {"generated_at": now().isoformat()}
    for name in wanted:
        fn, _ = SOURCES[name]
        report[name] = fn(key, args)
        print()

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "report.json")
        with open(path, "w") as fh:
            json.dump(report, fh, indent=1)
        print(f"wrote {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
