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

Sources available, all read-only:

  fetch-errors    MONITOR. Every feed the API reports as failing to fetch,
                  grouped by host. Not limited to tagged feeds.
  unstable-urls   MONITOR. Feeds tagged unstable_url: staleness, calendar
                  expiry and fetch state.
  watch-pages     MONITOR. The public page on which an agency publishes its own
                  feed URL, as recorded in evaluations/. One request per page,
                  no following of links.
  expired-calendars MONITOR. Every static feed serving a calendar that has run
                  out, tagged or not. Catches registrations that fetch fine
                  forever while the publisher has moved on.
  ntd-weblinks    DISCOVERY. Each US NTD reporter's own declared GTFS URL,
                  checked against the registry by URL and by us_ntd_id.
                  Suppressed by decisions in evaluations/.

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

import atlas_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evaluations")
API = "https://transit.land/api/v2/query"

# The NTD GTFS weblinks release, refreshed monthly. The copy under
# external-data-for-reference/ is the 2023 annual release and does not move.
NTD_WEBLINKS = "https://data.transportation.gov/resource/2u7n-ub22.csv"

FETCH_WINDOW = 20

FEED_FIELDS = f"""
    id
    onestop_id
    urls {{ static_current realtime_vehicle_positions }}
    feed_state {{ feed_version {{ fetched_at latest_calendar_date }} }}
    authorization {{ type param_name info_url }}
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

    auth = f.get("authorization") or {}
    return {
        "onestop_id": f["onestop_id"],
        "url": url,
        "auth_type": auth.get("type"),
        "auth_param_name": auth.get("param_name"),
        "auth_info_url": auth.get("info_url"),
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



def check_credentials(rows: list[dict], secrets_path: str) -> list[dict]:
    """Try each credentialed feed with a supplied secret, to tell an expired or
    missing credential apart from a feed that has genuinely moved.

    Reads a transitland-lib secrets file (a list of {feed_id, key, username,
    password, url_type, replace_url}). Secret values are never printed and
    never written to the report; only the outcome is.
    """
    try:
        raw = json.load(open(os.path.expanduser(secrets_path)))
    except Exception as e:
        print(f"  could not read secrets file: {type(e).__name__}")
        return rows
    secrets = raw.get("secrets", raw) if isinstance(raw, dict) else raw

    # transitland-lib matches a secret by feed_id or by DMFR filename, and most
    # entries use the filename, so map feeds to their file.
    file_of = atlas_registry.file_of_feed(atlas_registry.load(os.path.join(ROOT, "feeds")))

    by_feed, by_file = {}, {}
    for sec in secrets or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("feed_id"):
            by_feed.setdefault(sec["feed_id"], sec)
        if sec.get("filename"):
            by_file.setdefault(os.path.basename(sec["filename"]), sec)

    def secret_for(onestop_id):
        sec = by_feed.get(onestop_id)
        if sec:
            return sec
        return by_file.get(file_of.get(onestop_id, ""))

    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas-scan-feed-sources/1.0"
    out = []
    for r in rows:
        sec = secret_for(r["onestop_id"])
        # A secret can be scoped to one URL type; skip it if it does not apply.
        if sec and sec.get("url_type") and sec["url_type"] not in ("static_current",
                                                                  "realtime_vehicle_positions"):
            sec = None
        if not sec:
            out.append({**r, "credential": "none-supplied"})
            continue
        url, params, headers, auth = r["url"], {}, {}, None
        kind = r["auth_type"]
        name = r.get("auth_param_name") or "api_key"
        if sec.get("replace_url"):
            url = sec["replace_url"]
        elif kind == "query_param":
            params[name] = sec.get("key", "")
        elif kind == "header":
            headers[name] = sec.get("key", "")
        elif kind == "basic_auth":
            auth = (sec.get("username", ""), sec.get("password", ""))
        try:
            resp = session.get(url, params=params, headers=headers, auth=auth, timeout=45)
            ok = resp.status_code == 200 and len(resp.content) > 100
            out.append({**r, "credential": "works" if ok else f"rejected-{resp.status_code}"})
        except Exception as e:
            out.append({**r, "credential": f"unreachable-{type(e).__name__}"})
    return out


# -------------------------------------------------------------------- sources

def source_fetch_errors(key: str, args) -> dict:
    rows = [summarise(f) for f in fetch_all(key, "where: {fetch_error: true}")]
    total = len(rows)
    # Feeds that declare credentials fail for a different reason and need a
    # different response: obtain or renew a token, not find a new URL. These
    # are prod's own fetch attempts, so a failure here means the credential is
    # missing or expired rather than merely absent from this script.
    needs_auth = [r for r in rows if r["auth_type"]]
    rows = [r for r in rows if not r["auth_type"]]
    transient = [r for r in rows
                 if r["pattern"] in ("just-broke", "recovered")
                 and r["fail_streak"] <= args.transient_streak]
    rows = [r for r in rows if r not in transient]
    clusters, loose = cluster(rows, args.min_cluster)

    print(f"fetch-errors: {total} feeds failing to fetch")
    print(f"  {len(needs_auth)} declare credentials, reported separately below")
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
    if needs_auth and args.secrets:
        needs_auth = check_credentials(needs_auth, args.secrets)
        tally = collections.Counter(r["credential"] for r in needs_auth)
        print(f"\n  credential check against the supplied secrets file: {dict(tally)}")
        broken = [r for r in needs_auth if r["credential"].startswith("rejected")
                  or r["credential"].startswith("unreachable")]
        for r in broken[:12]:
            print(f"      {r['credential']:<24} {r['onestop_id'][:44]}")
    if needs_auth:
        by_info = collections.Counter(r["auth_info_url"] or "(no info_url)" for r in needs_auth)
        print(f"\n  credentialed feeds failing, grouped by where to request access:")
        for info, n in by_info.most_common():
            print(f"      {n:>3}  {info[:76]}")

    return {"total": total, "needs_auth": needs_auth, "held_back_transient": transient,
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


LIGHT_FIELDS = """
    id
    onestop_id
    urls { static_current }
    feed_state { feed_version { fetched_at latest_calendar_date } }
"""


def source_expired_calendars(key: str, args) -> dict:
    """Every feed serving a calendar that has run out, whether tagged or not.

    The unstable-urls source asks this of feeds tagged unstable_url, which is a
    small and self-selected set. A feed does not need that tag to quietly stop
    being current: a registration can keep fetching a file the publisher
    replaced elsewhere, returning 200 forever while its calendar runs out. That
    is invisible to a fetch-error check by construction, and it is how the
    largest shared host in the NTD release accumulated dozens of stale
    registrations.

    Reports calendar expiry only, never "no new version in N days". Those are
    not the same thing: an operator who has not changed the schedule in a year
    is indistinguishable from a dead feed by republication age, and that
    conflation was about 70% of what an earlier version of this reported.
    """
    query = f"query($after: Int) {{ feeds(limit: 1000, after: $after, where: {{spec: GTFS}}) {{{LIGHT_FIELDS}}} }}"
    rows, after = [], 0
    while True:
        r = requests.post(API, headers={"apikey": key},
                          json={"query": query, "variables": {"after": after}}, timeout=180)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise SystemExit(f"API error: {body['errors'][0]['message']}")
        page = body["data"]["feeds"]
        if not page:
            break
        rows.extend(page)
        after = page[-1]["id"]

    today = now().date()
    expired = []
    for f in rows:
        fv = ((f.get("feed_state") or {}).get("feed_version")) or {}
        latest = fv.get("latest_calendar_date")
        if not latest:
            continue
        try:
            end = datetime.fromisoformat(latest).date()
        except ValueError:
            continue
        if end >= today:
            continue
        url = (f.get("urls") or {}).get("static_current") or ""
        expired.append({
            "onestop_id": f["onestop_id"], "url": url,
            "host": (urlparse(url).netloc or "(no url)").lower(),
            "latest_calendar_date": latest, "expired_days": (today - end).days,
            "last_version_fetched_at": fv.get("fetched_at"),
        })

    print(f"expired-calendars: {len(rows)} static feeds with a known calendar; "
          f"{len(expired)} serving an expired one\n")
    by_host = collections.Counter(e["host"] for e in expired)
    print("  by host, worst clusters first:")
    for host, n in by_host.most_common(12):
        print(f"      {n:4d}  {host}")
    print("\n  longest expired:")
    for e in sorted(expired, key=lambda r: -r["expired_days"])[:15]:
        print(f"      {e['expired_days']:5d}d  cal_end {e['latest_calendar_date']}  {e['onestop_id'][:46]}")
    return {"checked": len(rows), "expired": expired,
            "by_host": dict(by_host.most_common())}


def load_decisions() -> dict[str, dict[str, dict]]:
    """Index every recorded candidate by subject, then by normalised URL.

    Subject keys are whichever of operator / feed / us_ntd_id the file carries,
    so a discovery source can look up a decision by whatever identifier it has.
    """
    out: dict[str, dict[str, dict]] = {}
    for path in sorted(glob.glob(os.path.join(EVAL_DIR, "*.json"))):
        if os.path.basename(path) == "schema.json":
            continue
        try:
            doc = json.load(open(path))
        except ValueError:
            continue
        subjects = [doc[k] for k in ("operator_onestop_id", "feed_onestop_id", "us_ntd_id")
                    if doc.get(k)]
        for cand in doc.get("candidates") or []:
            if not cand.get("url"):
                continue
            # A subject-scoped decision does not depend on which URL is offered,
            # so it is filed under a wildcard and matches whatever the source
            # declares next time. Everything else is filed under its own URL and
            # correctly re-opens when that changes.
            key = "*" if cand.get("scope") == "subject" else atlas_registry.normalise_url(cand["url"])
            for subject in subjects:
                out.setdefault(subject, {})[key] = cand
    return out


def source_ntd_weblinks(key, args) -> dict:
    """Each NTD reporter's own declared GTFS URL, checked against the registry.

    A discovery source, so recorded decisions suppress findings. Suppression is
    keyed on the agency and only holds while the declared URL is the one that
    was decided on: across releases 98% of NTD ids persist but only 43% of URLs
    do, so a decision tied to a URL alone would expire more often than not.
    """
    import csv
    import io

    db = atlas_registry.load(os.path.join(ROOT, "feeds"))
    active = atlas_registry.active_urls(db)
    historic = atlas_registry.historic_urls(db)
    by_ntd = atlas_registry.operators_by_ntd_id(db)
    decisions = load_decisions()

    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas-scan-feed-sources/1.0"
    r = session.get(NTD_WEBLINKS, params={"$limit": 50000}, timeout=180)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))

    # One row per agency and mode; the weblink is an agency-level attribute.
    agencies: dict[str, dict] = {}
    for row in rows:
        nid = atlas_registry.normalise_ntd_id(row.get("ntd_id", ""))
        if not nid:
            continue
        a = agencies.setdefault(nid, {
            "ntd_id": nid, "name": (row.get("agency_name") or "").strip(),
            "state": row.get("state", ""), "weblink": (row.get("weblink") or "").strip(),
            "modified": row.get("new_modified_date", ""), "modes": set(),
        })
        a["modes"].add(row.get("mode", ""))

    findings, suppressed, counts = [], [], collections.Counter()
    for nid, a in sorted(agencies.items()):
        url = a["weblink"]
        if not url:
            counts["no-weblink-declared"] += 1
            continue
        norm = atlas_registry.normalise_url(url)
        operators = sorted(by_ntd.get(nid, ()))

        if norm in active:
            counts["url-registered"] += 1
            continue
        if norm in historic:
            # We superseded this URL deliberately; NTD is behind us.
            kind = "declares-a-url-we-superseded"
        elif operators:
            kind = "agency-held-from-a-different-url"
        else:
            kind = "agency-not-in-atlas"
        counts[kind] += 1

        row = {"ntd_id": nid, "name": a["name"], "state": a["state"], "weblink": url,
               "kind": kind, "operators": operators, "modes": sorted(m for m in a["modes"] if m),
               "ntd_modified": (a["modified"] or "")[:10],
               "atlas_feeds": sorted(active.get(norm, set()) | historic.get(norm, set()))}

        prior = None
        for subject in operators + [nid]:
            bysubj = decisions.get(subject) or {}
            prior = bysubj.get(norm) or bysubj.get("*")
            if prior:
                break
        if prior:
            row["decision"] = prior.get("decision")
            row["decided_on"] = prior.get("decided_on")
            suppressed.append(row)
        else:
            findings.append(row)

    print(f"ntd-weblinks: {len(agencies)} agencies in the release\n")
    for k, n in counts.most_common():
        print(f"  {k:34s} {n:5d}")
    print(f"\n  suppressed by evaluations/         {len(suppressed):5d}")
    print(f"  reported                          {len(findings):5d}")

    for kind in ("declares-a-url-we-superseded", "agency-held-from-a-different-url",
                 "agency-not-in-atlas"):
        group = [f for f in findings if f["kind"] == kind]
        if not group:
            continue
        print(f"\n  {kind} ({len(group)}):")
        by_host = collections.Counter(urlparse(f["weblink"]).netloc.lower() for f in group)
        for host, n in by_host.most_common(8):
            print(f"      {n:4d}  {host}")
        for f in group[:5]:
            print(f"      {f['ntd_id']} {f['name'][:34]:36s} {f['state']:3s} {f['weblink'][:64]}")

    if args.resolve and findings:
        # A declared URL is sometimes a wrapper rather than the feed: agencies
        # paste the click-tracking link out of a vendor's notification email.
        # Following it can reveal a URL already registered, or a usable one that
        # no amount of string matching would have found.
        print(f"\n  resolving {len(findings)} declared URL(s)...")
        resolved = 0
        for f in findings:
            try:
                r = session.get(f["weblink"], timeout=60, allow_redirects=True,
                                stream=True)
                final = r.url
                head = r.raw.read(2, decode_content=True)
                r.close()
            except Exception as e:
                f["resolve_error"] = type(e).__name__
                continue
            f["http_status"] = r.status_code
            f["looks_like_zip"] = head == b"PK"
            if atlas_registry.normalise_url(final) != atlas_registry.normalise_url(f["weblink"]):
                f["resolved_url"] = final
                f["resolved_feeds"] = sorted(active.get(atlas_registry.normalise_url(final), set()))
                resolved += 1
        wrapped = [f for f in findings if f.get("resolved_url")]
        already = [f for f in wrapped if f.get("resolved_feeds")]
        print(f"      {resolved} redirected elsewhere; {len(already)} of those land on a URL already registered")
        for f in wrapped[:10]:
            tag = f"-> {', '.join(f['resolved_feeds'])}" if f.get("resolved_feeds") else \
                  ("zip" if f.get("looks_like_zip") else "not a zip")
            print(f"      {f['ntd_id']} {f['name'][:28]:30s} {f['resolved_url'][:58]}  {tag}")

    # An evaluation only earns its keep by suppressing something. One whose URL
    # matches nothing the source declares is inert, and silently so: it may have
    # been mistyped, or the agency may have moved on. Both are worth surfacing,
    # because neither is visible from the file itself.
    declared = {atlas_registry.normalise_url(a["weblink"]) for a in agencies.values() if a["weblink"]}
    inert = []
    for subject, cands in decisions.items():
        for norm, cand in cands.items():
            if cand.get("found_via", "").startswith("US NTD") and norm not in declared:
                inert.append((subject, cand.get("url", "")))
    if inert:
        print(f"\n  recorded against this source but matching nothing it declares ({len(inert)}):")
        for subject, url in inert[:10]:
            print(f"      {subject[:40]:42s} {url[:60]}")
        print("      either the URL was recorded wrong, or the agency has moved on")

    bad = atlas_registry.malformed_ntd_ids(db)
    if bad:
        print(f"\n  operators whose us_ntd_id will not join a raw NTD extract ({len(bad)}):")
        for oid, raw in bad[:10]:
            print(f"      {oid:52s} {raw}")

    return {"counts": dict(counts), "findings": findings, "suppressed": suppressed,
            "malformed_ntd_ids": [{"operator": o, "us_ntd_id": v} for o, v in bad]}


SOURCES = {
    "fetch-errors": (source_fetch_errors, True),
    "unstable-urls": (source_unstable_urls, True),
    "watch-pages": (source_watch_pages, False),
    "ntd-weblinks": (source_ntd_weblinks, False),
    "expired-calendars": (source_expired_calendars, True),
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
    ap.add_argument("--secrets", help="optional transitland-lib secrets file, used only to test whether "
                    "a credentialed feed's token still works; values are never printed or written")
    ap.add_argument("--resolve", action="store_true",
                    help="for discovery sources, follow each unmatched URL to see whether it is a "
                    "wrapper around one already registered. One request per finding, so off by default")
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
