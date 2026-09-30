#!/usr/bin/env -S uv run
"""
Compare the old and new static_current URL of every feed a PR repoints.

For each feed whose static_current differs between the base and head
revisions, validates both URLs with `transitland validate`, checksums their
contents with `transitland checksum`, and renders a side-by-side Markdown
comparison with a recommendation. The old URL is read from the base revision,
so this works whether or not the contributor moved it into static_historic.

Both revisions are read through git rather than the working tree, so the
workflow can run this from a trusted checkout of the base branch.

Advisory only: always exits 0 unless it is invoked incorrectly.

Usage:
    uv run scripts/compare-changed-feed-urls.py \\
        --base <rev> --head <rev> \\
        --summary-out reports/compare.md \\
        feeds/foo.dmfr.json feeds/bar.dmfr.json

Set TRANSITLAND_API_KEY to also report whether each URL's current file is
already in the Transitland archive.
"""

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

# validate-changed-feed-urls.py has a hyphenated name, so it cannot be
# imported by name. Its DMFR helpers (rename-aware feed lookup) are reused.
_spec = importlib.util.spec_from_file_location(
    "validate_changed_feed_urls",
    Path(__file__).resolve().parent / "validate-changed-feed-urls.py",
)
vcfu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcfu)

TRANSITLAND_API_BASE = "https://transit.land/api/v2/rest"
VALIDATE_TIMEOUT = 300

ROUTE_TYPE_NAMES = {
    0: "Tram", 1: "Subway/Metro", 2: "Rail", 3: "Bus", 4: "Ferry",
    5: "Cable tram", 6: "Aerial lift", 7: "Funicular", 11: "Trolleybus",
    12: "Monorail",
}


# --- Revisions -------------------------------------------------------------

def dmfr_at(rev: str, path: str) -> Optional[dict]:
    """Parsed DMFR file at a git revision, or None if absent or invalid."""
    show = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    if show.returncode != 0:
        return None
    try:
        return json.loads(show.stdout)
    except json.JSONDecodeError:
        return None


@dataclass
class Pair:
    feed_id: str
    old_url: str
    new_url: str
    auth_type: Optional[str]
    old_in_historic: bool


def repointed_feeds(base: Optional[dict], head: Optional[dict]) -> list[Pair]:
    """Feeds present in both revisions whose static_current URL changed."""
    pairs: list[Pair] = []
    if not head:
        return pairs
    for feed in head.get("feeds") or []:
        fid = feed.get("id")
        base_feed = vcfu.base_feed_for(base, feed)
        if not fid or not base_feed:
            continue
        urls = feed.get("urls") or {}
        new_url = urls.get("static_current")
        old_url = (base_feed.get("urls") or {}).get("static_current")
        if not isinstance(new_url, str) or not isinstance(old_url, str) or new_url == old_url:
            continue
        pairs.append(Pair(
            feed_id=fid,
            old_url=old_url,
            new_url=new_url,
            auth_type=(feed.get("authorization") or {}).get("type"),
            old_in_historic=old_url in (urls.get("static_historic") or []),
        ))
    return pairs


# --- Probing ---------------------------------------------------------------

def short_error(stderr: str) -> str:
    """The validator's error without the wrappers that repeat the URL.

    Go wraps errors outermost-first, so the full message echoes the URL once
    or twice before getting to the cause, e.g. "certificate is expired". The
    URL is already in the table.
    """
    msg = vcfu.trim(stderr).removeprefix("Error:").strip()
    msg = re.sub(r"could not open reader '[^']*':\s*", "", msg)
    msg = re.sub(r'\b(Get|Head) "[^"]*":\s*', "", msg)
    return msg[:160] or "command failed"


def validate_feed(url: str) -> dict:
    """`transitland validate` report for url, or {"_error": ...}."""
    try:
        res = subprocess.run(
            ["transitland", "validate", "-o", "-",
             "--include-entities", "--include-service-levels", url],
            capture_output=True, text=True, timeout=VALIDATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"_error": f"timed out after {VALIDATE_TIMEOUT}s"}
    except FileNotFoundError:
        return {"_error": "'transitland' command not found in PATH"}
    if res.returncode != 0 and not res.stdout.strip():
        return {"_error": short_error(res.stderr)}
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        return {"_error": f"could not read validation report: {e}"}


def dir_sha1(url: str) -> Optional[str]:
    """SHA1 of the feed's CSV contents, independent of zip packaging.

    The zip SHA1 changes whenever a server rebuilds the archive, even with
    identical data, so this is the one that answers "is this the same feed".
    """
    try:
        res = subprocess.run(["transitland", "checksum", "--raw-dir-sha1", url],
                             capture_output=True, text=True, timeout=VALIDATE_TIMEOUT)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = res.stdout.strip() if res.returncode == 0 else ""
    return out or None


def lookup_archive(sha1: Optional[str], api_key: str) -> Optional[dict]:
    """The archived feed version with this zip SHA1, {} if none, None if unknown."""
    if not sha1 or not api_key:
        return None
    req = urllib.request.Request(
        f"{TRANSITLAND_API_BASE}/feed_versions/{sha1}?apikey={api_key}",
        headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            fvs = json.loads(resp.read()).get("feed_versions") or []
    except urllib.error.HTTPError as e:
        return {} if e.code == 404 else None
    except Exception:
        return None
    if not fvs:
        return {}
    fv = fvs[0]
    return {
        "feed_onestop_id": (fv.get("feed") or {}).get("onestop_id"),
        "fetched_at": (fv.get("fetched_at") or "").split("T")[0],
    }


@dataclass
class Probe:
    url: str
    ok: bool
    error: str = ""
    sha1: str = ""
    dir_sha1: Optional[str] = None
    archive: Optional[dict] = None
    agencies: tuple[str, ...] = ()
    routes: int = 0
    route_types: tuple[tuple[str, int], ...] = ()
    stops: Optional[int] = None
    trips: Optional[int] = None
    earliest: Optional[str] = None
    latest: Optional[str] = None
    feed_version: Optional[str] = None
    publisher: Optional[str] = None
    errors: int = 0
    warnings: int = 0


def summarize(url: str, report: dict, dsha1: Optional[str]) -> Probe:
    if report.get("_error"):
        return Probe(url=url, ok=False, error=report["_error"], dir_sha1=dsha1)
    d = report.get("details") or {}
    rows = {f.get("name"): f.get("rows") for f in (d.get("files") or []) if f.get("name")}
    agencies = tuple(sorted(a.get("agency_name") or "?" for a in (d.get("agencies") or [])))
    routes = d.get("routes") or []
    types: dict[str, int] = {}
    for r in routes:
        name = ROUTE_TYPE_NAMES.get(r.get("route_type"), f"Type {r.get('route_type')}")
        types[name] = types.get(name, 0) + 1
    fi = (d.get("feed_infos") or [{}])[0] or {}
    ok = bool(report.get("success")) and (bool(agencies) or (rows.get("agency.txt") or 0) > 0)
    return Probe(
        url=url,
        ok=ok,
        error="" if ok else (report.get("failure_reason") or "no agency records"),
        sha1=d.get("sha1") or report.get("sha1") or "",
        dir_sha1=dsha1,
        agencies=agencies,
        # The entity arrays can come back empty for a feed that has rows, so
        # fall back to the per-file row count rather than reporting zero.
        routes=len(routes) or (rows.get("routes.txt") or 0),
        route_types=tuple(sorted(types.items())),
        stops=rows.get("stops.txt"),
        trips=rows.get("trips.txt"),
        earliest=d.get("earliest_calendar_date"),
        latest=d.get("latest_calendar_date"),
        feed_version=fi.get("feed_version"),
        publisher=fi.get("feed_publisher_name"),
        errors=len(report.get("errors") or []),
        warnings=len(report.get("warnings") or []),
    )


def probe(url: str, api_key: str) -> Probe:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        rep_f = ex.submit(validate_feed, url)
        dir_f = ex.submit(dir_sha1, url)
        p = summarize(url, rep_f.result(), dir_f.result())
    p.archive = lookup_archive(p.sha1, api_key)
    return p


# --- Rendering -------------------------------------------------------------

def md(s: str) -> str:
    """Make untrusted text safe inside a Markdown table cell."""
    return (s.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;")
             .replace(">", "&gt;").replace("\n", " ").replace("`", "'"))


def code(s: str) -> str:
    return f"<code>{md(s)}</code>"


def calendar_status(earliest: Optional[str], latest: Optional[str],
                    today: date) -> str:
    if not earliest or not latest:
        return "—"
    try:
        e, l = date.fromisoformat(earliest), date.fromisoformat(latest)
    except ValueError:
        return f"{md(earliest)} → {md(latest)}"
    if today < e:
        status = f"starts in {(e - today).days}d"
    elif today > l:
        status = f"**expired {(today - l).days}d ago**"
    else:
        span = (l - e).days
        status = f"active, {int(100 * (today - e).days / span) if span else 0}% through"
    return f"{earliest} → {latest}<br>({status})"


def archive_cell(p: Probe, has_key: bool) -> str:
    if not has_key or not p.sha1 or p.archive is None:
        return "—"
    if not p.archive:
        return "not yet archived"
    fid = p.archive.get("feed_onestop_id") or "?"
    return f"yes: {code(fid)}, fetched {md(p.archive.get('fetched_at') or '?')}"


def list_cell(items: tuple[str, ...], limit: int = 6) -> str:
    if not items:
        return "—"
    shown = [md(i) for i in items[:limit]]
    if len(items) > limit:
        shown.append(f"…+{len(items) - limit} more")
    return f"{len(items)}: " + "<br>".join(shown)


def num(n: Optional[int]) -> str:
    return "—" if n is None else f"{n:,}"


def delta(old: Optional[int], new: Optional[int]) -> str:
    if old is None or new is None or old == new:
        return num(new)
    sign = "+" if new > old else "−"
    pct = f", {sign}{abs(new - old) * 100 // old}%" if old else ""
    return f"{num(new)} ({sign}{abs(new - old):,}{pct})"


def recommend(old: Probe, new: Probe, today: date) -> tuple[str, list[str]]:
    """(headline, supporting notes) for the pair."""
    notes: list[str] = []
    if not new.ok and not old.ok:
        return "⚠️ **Neither URL validated.** A maintainer will need to check this by hand.", notes
    if not new.ok:
        return ("❌ **Keep the current URL.** The proposed URL did not validate, "
                "but the current one still does."), notes
    if not old.ok:
        return ("✅ **Switch to the proposed URL.** The current URL no longer "
                "validates; the proposed one does."), notes
    if old.dir_sha1 and old.dir_sha1 == new.dir_sha1:
        same = "byte-for-byte identical" if old.sha1 == new.sha1 else "the same feed data, repackaged"
        return f"✅ **Safe to switch.** Both URLs currently serve {same}.", notes

    headline = "🔎 **Feeds differ; review the details below.**"
    if old.latest and new.latest:
        if new.latest > old.latest:
            headline = (f"✅ **Proposed URL looks newer.** Its service runs through "
                        f"{new.latest}, vs {old.latest} for the current URL.")
        elif new.latest < old.latest:
            headline = (f"⚠️ **Proposed URL looks older.** Its service ends "
                        f"{new.latest}, vs {old.latest} for the current URL.")
        else:
            headline = ("🔎 **Feeds differ, but cover the same dates.** "
                        "Review the details below.")

    if old.agencies and new.agencies and not set(old.agencies) & set(new.agencies):
        notes.append("No agency names in common. Confirm that this is the same "
                     "service and not a different feed that belongs in its own record.")
    if old.routes and new.routes < old.routes // 2:
        notes.append(f"Route count drops from {old.routes:,} to {new.routes:,}. "
                     "Check that the proposed feed is complete.")
    if new.latest:
        try:
            if date.fromisoformat(new.latest) < today:
                notes.append("The proposed feed's service calendar has already expired.")
        except ValueError:
            pass
    return headline, notes


def render_pair(pair: Pair, old: Probe, new: Probe, has_key: bool, today: date) -> str:
    headline, notes = recommend(old, new, today)

    def status(p: Probe) -> str:
        return "✅ valid" if p.ok else f"❌ {md(p.error)}"

    def fv(p: Probe) -> str:
        parts = [md(x) for x in (p.publisher, p.feed_version) if x]
        return "<br>".join(parts) or "—"

    rows = [
        ("URL", code(old.url), code(new.url)),
        ("Status", status(old), status(new)),
    ]
    if old.ok or new.ok:
        rows += [
            ("Service dates", calendar_status(old.earliest, old.latest, today),
             calendar_status(new.earliest, new.latest, today)),
            ("Agencies", list_cell(old.agencies), list_cell(new.agencies)),
            ("Routes", num(old.routes) if old.ok else "—",
             delta(old.routes if old.ok else None, new.routes) if new.ok else "—"),
            ("Route types",
             "<br>".join(f"{md(k)}: {v}" for k, v in old.route_types) or "—",
             "<br>".join(f"{md(k)}: {v}" for k, v in new.route_types) or "—"),
            ("Stops", num(old.stops), delta(old.stops, new.stops)),
            ("Trips", num(old.trips), delta(old.trips, new.trips)),
            ("feed_info", fv(old), fv(new)),
            ("Validation", f"{old.errors} errors, {old.warnings} warnings" if old.ok else "—",
             f"{new.errors} errors, {new.warnings} warnings" if new.ok else "—"),
            ("Contents SHA1", code(old.dir_sha1[:12]) if old.dir_sha1 else "—",
             code(new.dir_sha1[:12]) if new.dir_sha1 else "—"),
        ]
        if has_key:
            rows.append(("In Transitland", archive_cell(old, has_key), archive_cell(new, has_key)))

    table = ["| | Current | Proposed |", "|---|---|---|"]
    table += [f"| **{a}** | {b} | {c} |" for a, b, c in rows]

    out = [f"### {code(pair.feed_id)}", "", headline, ""]
    out += [f"- {n}" for n in notes]
    if not pair.old_in_historic:
        out.append("- 💡 The current URL is not in `urls.static_historic[]`; "
                   "consider moving it there.")
    if out[-1] != "":
        out.append("")
    out += ["<details><summary>Side-by-side comparison</summary>", "", *table, "",
            "</details>", ""]
    return "\n".join(out)


def render_skipped(pair: Pair) -> str:
    return (f"### {code(pair.feed_id)}\n\n⚪ Skipped: this feed requires "
            f"authorization (`{md(pair.auth_type or '')}`), so the URLs can't be "
            f"fetched here.\n")


# --- Main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Git revision of the base branch.")
    parser.add_argument("--head", required=True, help="Git revision of the PR head.")
    parser.add_argument("--summary-out", type=Path, default=None,
                        help="Write Markdown here; defaults to stdout. Empty if nothing was repointed.")
    parser.add_argument("files", nargs="*", help="Changed DMFR file paths.")
    args = parser.parse_args()

    api_key = os.environ.get("TRANSITLAND_API_KEY", "")
    today = date.today()

    pairs: list[Pair] = []
    for fp in args.files:
        pairs += repointed_feeds(dmfr_at(args.base, fp), dmfr_at(args.head, fp))

    urls = sorted({u for p in pairs if not p.auth_type for u in (p.old_url, p.new_url)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        probes = dict(zip(urls, ex.map(lambda u: probe(u, api_key), urls)))

    sections = [
        render_skipped(p) if p.auth_type
        else render_pair(p, probes[p.old_url], probes[p.new_url], bool(api_key), today)
        for p in pairs
    ]
    summary = "\n".join(sections)
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(summary)
    else:
        sys.stdout.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
