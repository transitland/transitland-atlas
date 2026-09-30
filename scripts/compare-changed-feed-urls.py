#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Compare the old and new static_current URL of every feed a PR repoints.

For each feed whose static_current differs between the base and head
revisions, validates both URLs with `transitland validate`, checksums their
contents with `transitland checksum`, and renders a side-by-side Markdown
comparison with a recommendation. The old URL is read from the base revision,
so this works whether or not the contributor moved it into static_historic.

Both revisions are read through git rather than the working tree, so the
workflow can run this from a trusted checkout of the base branch.

Runs in two stages:

  probe   fetches and validates the URLs and prints JSON. Needs no secrets.
  render  turns that JSON into Markdown. With TRANSITLAND_API_KEY set, it
          also reports whether each file is already in the Transitland
          archive.

Standard library only: the empty inline dependency list above makes `uv run`
skip syncing the project environment. Advisory: always exits 0 unless
invoked incorrectly.

Usage:
    uv run scripts/compare-changed-feed-urls.py probe --base <rev> --head <rev> \\
        feeds/foo.dmfr.json feeds/bar.dmfr.json > probes.json
    uv run scripts/compare-changed-feed-urls.py render probes.json > compare.md
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
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# validate-changed-feed-urls.py has a hyphenated name, so it cannot be
# imported by name. Its DMFR helpers and pass rule are shared with it.
_spec = importlib.util.spec_from_file_location(
    "validate_changed_feed_urls",
    Path(__file__).resolve().parent / "validate-changed-feed-urls.py",
)
vcfu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcfu)

TRANSITLAND_API_BASE = "https://transit.land/api/v2/rest"
TIMEOUT = 300
SHA1_RE = re.compile(r"[0-9a-f]{40}")

ROUTE_TYPE_NAMES = {
    0: "Tram", 1: "Subway/Metro", 2: "Rail", 3: "Bus", 4: "Ferry",
    5: "Cable tram", 6: "Aerial lift", 7: "Funicular", 11: "Trolleybus",
    12: "Monorail",
}


# --- Revisions -------------------------------------------------------------

@dataclass
class Pair:
    feed_id: str
    old_url: str
    new_url: str
    auth_type: Optional[str]


def obj(d: dict, key: str) -> dict:
    """d[key] if it is an object, else {}; tolerates malformed DMFR records."""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def repointed_feeds(base: Optional[dict], head: Optional[dict]) -> list[Pair]:
    """Feeds present in both revisions whose static_current URL changed."""
    pairs: list[Pair] = []
    for feed in (head or {}).get("feeds") or []:
        if not isinstance(feed, dict):
            continue
        fid = feed.get("id")
        base_feed = vcfu.base_feed_for(base, feed)
        if not fid or not base_feed:
            continue
        new_url = obj(feed, "urls").get("static_current")
        old_url = obj(base_feed, "urls").get("static_current")
        if not isinstance(new_url, str) or not isinstance(old_url, str) or new_url == old_url:
            continue
        # Either version needing a key means one of the URLs can't be fetched here.
        auth = obj(feed, "authorization").get("type") or obj(base_feed, "authorization").get("type")
        pairs.append(Pair(fid, old_url, new_url, auth))
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
        res = subprocess.run(["transitland", "validate", "-o", "-", "--include-entities", url],
                             capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"_error": f"timed out after {TIMEOUT}s"}
    except (OSError, ValueError) as e:  # e.g. binary missing, NUL byte in the URL
        return {"_error": f"could not run the validator: {e}"}
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
    `transitland validate` computes it but does not report it, so this
    downloads the feed a second time.
    """
    try:
        res = subprocess.run(["transitland", "checksum", "--raw-dir-sha1", url],
                             capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return (res.stdout.strip() if res.returncode == 0 else "") or None


@dataclass
class Probe:
    url: str
    ok: bool
    error: str = ""
    sha1: str = ""
    dir_sha1: Optional[str] = None
    agencies: list[str] = field(default_factory=list)
    routes: Optional[int] = None
    route_types: dict[str, int] = field(default_factory=dict)
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
    failure = vcfu.static_failure(report)
    if failure:
        return Probe(url=url, ok=False, error=failure, dir_sha1=dsha1)
    d = report.get("details") or {}
    rows = {f.get("name"): f.get("rows") for f in d.get("files") or []}
    types: dict[str, int] = {}
    for r in d.get("routes") or []:
        name = ROUTE_TYPE_NAMES.get(r.get("route_type"), f"Type {r.get('route_type')}")
        types[name] = types.get(name, 0) + 1
    fi = (d.get("feed_infos") or [{}])[0] or {}
    return Probe(
        url=url,
        ok=True,
        sha1=d.get("sha1") or "",
        dir_sha1=dsha1,
        agencies=sorted(a.get("agency_name") or "?" for a in d.get("agencies") or []),
        # Counts come from file rows: the entity arrays can come back empty
        # for a feed that has rows, so they are used only for the breakdown.
        routes=rows.get("routes.txt"),
        route_types=dict(sorted(types.items())),
        stops=rows.get("stops.txt"),
        trips=rows.get("trips.txt"),
        earliest=d.get("earliest_calendar_date"),
        latest=d.get("latest_calendar_date"),
        feed_version=fi.get("feed_version"),
        publisher=fi.get("feed_publisher_name"),
        errors=len(report.get("errors") or []),
        warnings=len(report.get("warnings") or []),
    )


def lookup_archive(sha1: str, api_key: str) -> Optional[dict]:
    """The archived feed version with this zip SHA1, {} if none, None if unknown."""
    if not api_key or not SHA1_RE.fullmatch(sha1):
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
    return {
        "feed_onestop_id": (fvs[0].get("feed") or {}).get("onestop_id"),
        "fetched_at": (fvs[0].get("fetched_at") or "").split("T")[0],
    }


# --- Rendering -------------------------------------------------------------

# Characters with meaning to GitHub Markdown, as HTML entities. Backslash
# escapes are not enough: they leave #123 references working. `:` and `.`
# stop bare URLs from being autolinked. @ and # get a zero-width space after
# them, so text from a feed can't mention people or link issues.
MD_ENTITIES = {c: f"&#{ord(c)};" for c in "\\`*_~[]!|<>:."} | {
    "&": "&amp;", "@": "@&#8203;", "#": "#&#8203;"}


def md(s: str) -> str:
    """Render s as literal text in a Markdown table cell."""
    return "".join(MD_ENTITIES.get(c, c) for c in " ".join(s.split()))


def code(s: str) -> str:
    """s as an inline code span: literal, and never autolinked or mentioned.

    Entities would show up verbatim in here, so only the characters a code
    span can't hold are changed.
    """
    return "`" + " ".join(s.split()).replace("`", "'").replace("|", "\\|") + "`"


def parse_date(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def calendar_cell(p: Probe, today: date) -> str:
    e, l = parse_date(p.earliest), parse_date(p.latest)
    if not e or not l:
        return "—"
    if today < e:
        status = f"starts in {(e - today).days}d"
    elif today > l:
        status = f"**expired {(today - l).days}d ago**"
    else:
        span = (l - e).days
        status = f"active, {100 * (today - e).days // span if span else 0}% through"
    return f"{e} → {l}<br>({status})"


def archive_cell(entry: Optional[dict]) -> str:
    if entry is None:
        return "—"
    if not entry:
        return "no"
    return (f"yes: {code(entry.get('feed_onestop_id') or '?')}, "
            f"fetched {md(entry.get('fetched_at') or '?')}")


def lines_cell(items: list[str], limit: int = 6) -> str:
    shown = [md(i) for i in items[:limit]]
    if len(items) > limit:
        shown.append(f"…+{len(items) - limit} more")
    return "<br>".join(shown) or "—"


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
    if not new.ok and not old.ok:
        return "⚠️ **Neither URL validated.** A maintainer will need to check this by hand.", []
    if not new.ok:
        return ("❌ **Keep the current URL.** The proposed URL did not validate, "
                "but the current one still does."), []

    # About the proposed feed alone, so they apply whatever the verdict.
    calendar_notes = []
    new_start, new_end = parse_date(new.earliest), parse_date(new.latest)
    if new_end and new_end < today:
        calendar_notes.append("The proposed feed's service calendar has already expired.")
    elif new_start and new_start > today:
        calendar_notes.append(f"The proposed feed's service doesn't start until {new_start}. "
                              "Switching now may leave a gap in current service.")

    if not old.ok:
        return ("✅ **Switch to the proposed URL.** The current URL no longer "
                "validates; the proposed one does."), calendar_notes
    if old.dir_sha1 and old.dir_sha1 == new.dir_sha1:
        same = "byte-for-byte identical" if old.sha1 == new.sha1 else "the same feed data, repackaged"
        return f"✅ **Safe to switch.** Both URLs currently serve {same}.", calendar_notes

    old_end = parse_date(old.latest)
    if not old_end or not new_end:
        headline = "🔎 **Feeds differ; review the details below.**"
    elif new_end > old_end:
        headline = (f"✅ **Proposed URL looks newer.** Its service runs through "
                    f"{new_end}, vs {old_end} for the current URL.")
    elif new_end < old_end:
        headline = (f"⚠️ **Proposed URL looks older.** Its service ends "
                    f"{new_end}, vs {old_end} for the current URL.")
    else:
        headline = "🔎 **Feeds differ, but cover the same dates.** Review the details below."

    notes = []
    if old.agencies and new.agencies and not set(old.agencies) & set(new.agencies):
        notes.append("No agency names in common. Confirm that this is the same "
                     "service and not a different feed that belongs in its own record.")
    if old.routes and new.routes is not None and new.routes < old.routes // 2:
        notes.append(f"Route count drops from {old.routes:,} to {new.routes:,}. "
                     "Check that the proposed feed is complete.")
    return headline, notes + calendar_notes


def render_pair(pair: Pair, old: Probe, new: Probe, today: date,
                archive: Optional[dict[str, Optional[dict]]]) -> str:
    headline, notes = recommend(old, new, today)

    def side(p: Probe) -> list[str]:
        """Cells that describe one URL on its own, in row order."""
        return [
            code(p.url),
            "✅ valid" if p.ok else f"❌ {md(p.error)}",
            calendar_cell(p, today),
            f"{len(p.agencies)}: {lines_cell(p.agencies)}" if p.agencies else "—",
            lines_cell([f"{k}: {v}" for k, v in p.route_types.items()]),
            lines_cell([x for x in (p.publisher, p.feed_version) if x]),
            f"{p.errors} error groups, {p.warnings} warning groups" if p.ok else "—",
            code(p.dir_sha1[:12]) if p.dir_sha1 else "—",
        ] + ([archive_cell(archive.get(p.sha1))] if archive is not None else [])

    labels = ["URL", "Status", "Service dates", "Agencies", "Route types",
              "feed_info", "Validation", "Contents SHA1"]
    if archive is not None:
        # Keyed by zip SHA1, so a server that rebuilds its zip on each request
        # shows "no" even when Transitland has the same data.
        labels.append("This exact zip in Transitland")
    rows = list(zip(labels, side(old), side(new)))
    if not old.ok and not new.ok:
        rows = rows[:2]
    else:
        rows[4:4] = [("Routes", num(old.routes), delta(old.routes, new.routes)),
                     ("Stops", num(old.stops), delta(old.stops, new.stops)),
                     ("Trips", num(old.trips), delta(old.trips, new.trips))]

    table = ["| | Current | Proposed |", "|---|---|---|"]
    table += [f"| **{a}** | {b} | {c} |" for a, b, c in rows]
    bullets = "".join(f"- {n}\n" for n in notes)
    return (f"### {code(pair.feed_id)}\n\n{headline}\n\n"
            + (f"{bullets}\n" if bullets else "")
            + "<details><summary>Side-by-side comparison</summary>\n\n"
            + "\n".join(table) + "\n\n</details>\n")


def render_skipped(pair: Pair) -> str:
    return (f"### {code(pair.feed_id)}\n\n⚪ Skipped: this feed requires "
            f"authorization (`{md(pair.auth_type or '')}`), so the URLs can't be "
            f"fetched here.\n")


# --- Main ------------------------------------------------------------------

def cmd_probe(args: argparse.Namespace) -> None:
    pairs: list[Pair] = []
    for fp in args.files:
        pairs += repointed_feeds(vcfu.dmfr_at(args.base, fp), vcfu.dmfr_at(args.head, fp))

    urls = sorted({u for p in pairs if not p.auth_type for u in (p.old_url, p.new_url)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        reports = ex.map(validate_feed, urls)
        dir_sha1s = ex.map(dir_sha1, urls)
        probes = {u: summarize(u, r, d) for u, r, d in zip(urls, reports, dir_sha1s)}

    json.dump({"pairs": [asdict(p) for p in pairs],
               "probes": {u: asdict(p) for u, p in probes.items()}},
              sys.stdout, indent=1)


def cmd_render(args: argparse.Namespace) -> None:
    api_key = os.environ.get("TRANSITLAND_API_KEY", "")
    data = json.loads(args.probes.read_text())
    pairs = [Pair(**p) for p in data["pairs"]]
    probes = {u: Probe(**p) for u, p in data["probes"].items()}

    archive = None
    if api_key:
        sha1s = sorted({p.sha1 for p in probes.values() if p.sha1})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            archive = dict(zip(sha1s, ex.map(lambda s: lookup_archive(s, api_key), sha1s)))

    today = date.today()
    sys.stdout.write("\n".join(
        render_skipped(p) if p.auth_type
        else render_pair(p, probes[p.old_url], probes[p.new_url], today, archive)
        for p in pairs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="Fetch and validate old and new URLs; print JSON.")
    p.add_argument("--base", required=True, help="Git revision of the base branch.")
    p.add_argument("--head", required=True, help="Git revision of the PR head.")
    p.add_argument("files", nargs="*", help="Changed DMFR file paths.")
    p.set_defaults(func=cmd_probe)

    r = sub.add_parser("render", help="Print probe JSON as Markdown; empty if nothing was repointed.")
    r.add_argument("probes", type=Path, help="JSON printed by the probe command.")
    r.set_defaults(func=cmd_render)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
