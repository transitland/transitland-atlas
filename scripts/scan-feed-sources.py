#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///

"""Scan feed sources and report findings worth acting on.

Intended to grow a source per goal, of two kinds. **State monitors** describe
the current condition of feeds already registered — a finding stays valid
until someone fixes it, so it should not be suppressed. **Discovery sources**
propose candidates from outside the registry, such as NTD weblinks, and repeat
on every run until a decision is recorded in evaluations/, so suppression is
what makes them converge. See evaluations/README.md.

Two sources so far, both read-only and both monitors:

1. Asks the Transitland API how each `unstable_url`-tagged feed is doing —
   when it last produced a new feed version, whether its calendar has run out,
   and whether the most recent fetch succeeded. The API syncs from Atlas, so
   its feed list matches this repository.

2. Reads each `watch` page recorded in evaluations/ — the public page on which
   an agency publishes its own feed URL — and notes which feed links appear on
   it, so a changed URL can be spotted against the recorded `last_seen_url`.
   One request per page, no following of links.

Output is a human-readable summary, plus an optional JSON report and a copy of
each page consulted, for reference or for a later pass to read.

Needs TRANSITLAND_API_KEY. Unlike validate-evaluations.py this talks to the
network, so it is a reporting tool and not something to gate CI on.

Usage:
  cd scripts && uv run scan-feed-sources.py
  cd scripts && uv run scan-feed-sources.py --out ../scan-output --stale-days 365
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evaluations")
API = "https://transit.land/api/v2/query"

FEEDS_QUERY = """
query($after: Int) {
  feeds(limit: 1000, after: $after, where: {tags: {unstable_url: "true"}}) {
    id
    onestop_id
    urls { static_current }
    feed_state {
      feed_version { fetched_at sha1 earliest_calendar_date latest_calendar_date }
    }
    feed_fetches(limit: 1) { fetched_at success response_code fetch_error }
  }
}
"""

# Links that look like a feed, or a page that would lead to one.
LINK_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
ARCHIVE = re.compile(r"\.zip(\?|$)", re.I)
FEEDISH = re.compile(r"(gtfs|google_transit)", re.I)
# Site plumbing that matches "gtfs" only because the page path does.
NOISE = re.compile(r"(/wp-json/|/oembed|/feed/?$|\?replytocom=)", re.I)


def feed_links(html: str, page: str) -> list[str]:
    """Links on `page` that plausibly point at a feed, minus site plumbing."""
    found = set()
    for href in LINK_RE.findall(html):
        url = absolutize(href.split("#")[0], page)
        if not url or url.rstrip("/") == page.rstrip("/"):
            continue  # self-link
        if NOISE.search(url):
            continue
        if ARCHIVE.search(url) or FEEDISH.search(url):
            found.add(url)
    return sorted(found)


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value) -> int | None:
    ts = parse_ts(value)
    return None if ts is None else (now() - ts).days


def gql(key: str, variables=None):
    r = requests.post(
        API,
        headers={"apikey": key},
        json={"query": FEEDS_QUERY, "variables": variables or {}},
        timeout=90,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise SystemExit(f"API error: {body['errors'][0]['message']}")
    return body["data"]


# ------------------------------------------------------------------ pass one

def check_unstable_feeds(key: str, stale_days: int) -> list[dict]:
    rows, after = [], 0
    while True:
        feeds = gql(key, {"after": after}).get("feeds") or []
        if not feeds:
            break
        rows.extend(feeds)
        after = feeds[-1]["id"]

    today = now().date()
    out = []
    for f in rows:
        fv = ((f.get("feed_state") or {}).get("feed_version")) or {}
        fetches = f.get("feed_fetches") or []
        last_fetch = fetches[0] if fetches else {}
        age = days_since(fv.get("fetched_at"))

        latest_cal = fv.get("latest_calendar_date")
        expired = None
        if latest_cal:
            try:
                expired = datetime.fromisoformat(latest_cal).date() < today
            except ValueError:
                expired = None

        flags = []
        if last_fetch and last_fetch.get("success") is False:
            code = last_fetch.get("response_code")
            error = (last_fetch.get("fetch_error") or "").lower()
            if code is None:
                flags.append("fetch-unreachable")
            elif code == 200:
                # 200 with an unusable body: a parked domain, an SPA catch-all,
                # or an error page served with the wrong status.
                flags.append("fetch-200-not-a-feed")
            else:
                flags.append(f"fetch-http-{code}")
        if age is not None and age >= stale_days:
            flags.append(f"no-new-version-{age}d")
        if expired:
            flags.append(f"calendar-expired({latest_cal})")
        if not fv:
            flags.append("never-imported")

        out.append({
            "onestop_id": f["onestop_id"],
            "static_current": (f.get("urls") or {}).get("static_current"),
            "last_feed_version_at": fv.get("fetched_at"),
            "days_since_new_version": age,
            "latest_calendar_date": latest_cal,
            "calendar_expired": expired,
            "last_fetch": {
                "fetched_at": last_fetch.get("fetched_at"),
                "success": last_fetch.get("success"),
                "response_code": last_fetch.get("response_code"),
                "fetch_error": last_fetch.get("fetch_error"),
            } if last_fetch else None,
            "flags": flags,
        })
    return out


# ------------------------------------------------------------------ pass two

def load_watch_entries() -> list[dict]:
    entries = []
    for path in sorted(glob.glob(os.path.join(EVAL_DIR, "o-*.json"))):
        doc = json.load(open(path))
        for w in doc.get("watch") or []:
            entries.append({
                "operator_onestop_id": doc.get("operator_onestop_id"),
                "source_file": os.path.relpath(path, ROOT),
                **w,
            })
    return entries


def absolutize(href: str, page: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    from urllib.parse import urljoin
    return urljoin(page, href)


def check_watch_pages(entries: list[dict], out_dir: str | None) -> list[dict]:
    results = []
    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas-scan-feed-sources/1.0"

    for entry in entries:
        page = entry["page"]
        row = {
            "page": page,
            "operator_onestop_id": entry.get("operator_onestop_id"),
            "publishes": entry.get("publishes"),
            "last_seen_url": entry.get("last_seen_url"),
            "last_checked": entry.get("last_checked"),
            "note": entry.get("note"),
        }
        try:
            r = session.get(page, timeout=60)
            row["status"] = r.status_code
        except Exception as e:
            row["status"] = None
            row["error"] = f"{type(e).__name__}"
            results.append(row)
            continue

        if r.status_code != 200:
            results.append(row)
            continue

        all_links = {absolutize(h.split("#")[0], page) for h in LINK_RE.findall(r.text)}
        row["feed_links_found"] = feed_links(r.text, page)

        seen = entry.get("last_seen_url")
        if seen:
            row["last_seen_url_still_present"] = seen in all_links
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            slug = re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-")[:120]
            snap = os.path.join(out_dir, f"{slug}.html")
            with open(snap, "w", encoding="utf-8") as fh:
                fh.write(r.text)
            row["snapshot"] = os.path.relpath(snap, ROOT)
        results.append(row)
    return results


# ------------------------------------------------------------------- reports

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stale-days", type=int, default=180,
                    help="flag feeds with no new feed version in this many days (default 180)")
    ap.add_argument("--out", help="directory for the report and a copy of each page consulted")
    ap.add_argument("--skip-api", action="store_true", help="only check watch pages")
    ap.add_argument("--skip-pages", action="store_true", help="only query the API")
    args = ap.parse_args()

    key = os.environ.get("TRANSITLAND_API_KEY", "")
    if not args.skip_api and not key:
        print("ERROR: TRANSITLAND_API_KEY is not set (use --skip-api to check pages only)")
        return 2

    report = {"generated_at": now().isoformat(), "stale_days": args.stale_days}

    if not args.skip_api:
        feeds = check_unstable_feeds(key, args.stale_days)
        report["unstable_feeds"] = feeds
        flagged = [f for f in feeds if f["flags"]]
        flagged.sort(key=lambda f: (f["days_since_new_version"] is None, -(f["days_since_new_version"] or 0)))
        print(f"unstable_url feeds: {len(feeds)} total, {len(flagged)} flagged")

        def category(flag: str) -> str:
            for prefix in ("no-new-version", "calendar-expired"):
                if flag.startswith(prefix):
                    return prefix
            return flag

        import collections
        tally = collections.Counter(category(flag) for f in feeds for flag in f["flags"])
        print("  by category: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) + "\n")

        for f in flagged:
            age = f["days_since_new_version"]
            age_s = "never" if age is None else f"{age}d"
            print(f"  {f['onestop_id']:<52} last new version {age_s:>6}  {', '.join(f['flags'])}")
        if not flagged:
            print("  nothing flagged")

    if not args.skip_pages:
        entries = load_watch_entries()
        pages = check_watch_pages(entries, args.out)
        report["watch_pages"] = pages
        print(f"\nwatch pages: {len(pages)}\n")
        for p in pages:
            status = p.get("status")
            if status != 200:
                print(f"  {p['page']}  UNREACHABLE ({p.get('error') or status})")
                continue
            bits = [f"{len(p.get('feed_links_found') or [])} feed-shaped link(s)"]
            if p.get("last_seen_url") is not None:
                bits.append("last_seen_url present" if p.get("last_seen_url_still_present") else "LAST SEEN URL GONE")
            print(f"  {p['page']}  {status}  {'; '.join(bits)}")
            for link in (p.get("feed_links_found") or [])[:5]:
                print(f"      {link}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "report.json")
        with open(path, "w") as fh:
            json.dump(report, fh, indent=1)
        print(f"\nwrote {os.path.relpath(path, ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
