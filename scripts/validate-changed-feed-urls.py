#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate static and RT URLs that are new or changed in a PR's DMFR files.

For each changed DMFR file, computes the per-URL diff against the base ref
and runs `transitland validate` on changed static URLs and
`transitland rt-convert` on changed RT URLs. Emits a Markdown summary
(per-feed <details>/<summary> blocks) and writes per-URL JSON reports to
the reports directory. Exits non-zero when any URL fails the minimum bar:

  Static: feed fetches, parses, and has >=1 agency record.
  RT:     response is a valid GTFS-RT message (has a header).

Auth-protected URLs are skipped (validated by tlv2 with private keys).

Usage:
    uv run scripts/validate-changed-feed-urls.py \\
        --base-ref main \\
        --reports-dir reports \\
        --summary-out reports/summary.md \\
        feeds/foo.dmfr.json feeds/bar.dmfr.json
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# short-name -> DMFR url key
RT_KINDS: dict[str, str] = {
    "vp": "realtime_vehicle_positions",
    "tu": "realtime_trip_updates",
    "alerts": "realtime_alerts",
}

UrlTuple = tuple[str, str, str, str]  # (file, feed_id, url_type, url)


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", s.replace("/", "_").replace(":", "_"))


def trim(s: str, n: int = 300) -> str:
    for line in s.splitlines():
        if line.startswith("Error:"):
            return " ".join(line.split())[:n]
    return " ".join(s.split())[:n]


def parse_dmfr_file(path: Path) -> Optional[dict]:
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def base_dmfr(file_path: str, base_ref: str) -> Optional[dict]:
    """Return the parsed base version of file_path, or None if unavailable."""
    spec = f"origin/{base_ref}:{file_path}"
    if subprocess.run(["git", "cat-file", "-e", spec], capture_output=True).returncode != 0:
        return None
    show = subprocess.run(["git", "show", spec], capture_output=True, text=True)
    if show.returncode != 0:
        return None
    try:
        return json.loads(show.stdout)
    except json.JSONDecodeError:
        return None


def url_tuples(dmfr: dict, file_path: str) -> set[UrlTuple]:
    out: set[UrlTuple] = set()
    for feed in dmfr.get("feeds") or []:
        fid = feed.get("id")
        if not fid:
            continue
        for k, v in (feed.get("urls") or {}).items():
            if isinstance(v, str):
                out.add((file_path, fid, k, v))
    return out


def feed_by_id(dmfr: Optional[dict], feed_id: str) -> Optional[dict]:
    if not dmfr:
        return None
    for feed in dmfr.get("feeds") or []:
        if feed.get("id") == feed_id:
            return feed
    return None


def load_operator_associated_feed_ids(feeds_dir: Path) -> set[str]:
    """Return the set of feed_onestop_ids referenced by any operator's
    associated_feeds[] across every *.dmfr.json file under feeds_dir.

    Reads from the working tree, so it captures both pre-existing
    associations and ones added in the same PR (regardless of which dmfr
    file they live in).
    """
    associated: set[str] = set()
    if not feeds_dir.is_dir():
        return associated
    for p in feeds_dir.glob("*.dmfr.json"):
        try:
            with p.open() as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for op in data.get("operators") or []:
            for af in op.get("associated_feeds") or []:
                fid = af.get("feed_onestop_id")
                if fid:
                    associated.add(fid)
    return associated


@dataclass
class Outcome:
    bullet: str
    blocker: bool = False
    skipped: bool = False
    passed: bool = False


def run_validate_static(url: str, report_path: Path) -> Outcome:
    err_path = report_path.with_suffix(".err")
    try:
        res = subprocess.run(
            ["transitland", "validate", "--include-service-levels", "-o", str(report_path), url],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return Outcome(
            bullet=f"- ❌ static — `{url}` — timed out after 300s",
            blocker=True,
        )
    if res.returncode != 0:
        err_path.write_text(res.stderr)
        return Outcome(
            bullet=f"- ❌ static — could not fetch or parse `{url}` — {trim(res.stderr)}",
            blocker=True,
        )

    try:
        data = json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return Outcome(
            bullet=f"- ❌ static — `{url}` — could not parse validate output: {e}",
            blocker=True,
        )

    files = data.get("details", {}).get("files") or []
    by_name = {f.get("name"): f for f in files}
    agencies = (by_name.get("agency.txt") or {}).get("rows", 0)
    routes = (by_name.get("routes.txt") or {}).get("rows", 0)
    stops = (by_name.get("stops.txt") or {}).get("rows", 0)
    file_count = len(files)
    earliest = data.get("details", {}).get("earliest_calendar_date") or "?"
    latest = data.get("details", {}).get("latest_calendar_date") or "?"
    success = data.get("success")
    errors = data.get("errors") or {}
    warnings = data.get("warnings") or {}

    if not success or agencies < 1:
        reason = data.get("failure_reason") or "no agency records"
        return Outcome(
            bullet=f"- ❌ static — `{url}` — {reason}",
            blocker=True,
        )

    body = (
        f"- ✅ static — `{url}` — {agencies} agencies, {routes} routes, "
        f"{stops} stops, {file_count} files, service {earliest} → {latest}"
    )
    if errors or warnings:
        body += (
            f"\n  - {len(errors)} error groups, {len(warnings)} warning groups "
            f"(informational; tlv2 tracks full validation history):"
        )
        for v in sorted(errors.values(), key=lambda v: -v.get("count", 0))[:5]:
            body += f"\n    - error · {v.get('filename')} · {v.get('error_type')} × {v.get('count')}"
        for v in sorted(warnings.values(), key=lambda v: -v.get("count", 0))[:5]:
            body += f"\n    - warning · {v.get('filename')} · {v.get('error_type')} × {v.get('count')}"
    return Outcome(bullet=body, passed=True)


def run_rt_convert(short: str, url: str, report_path: Path) -> Outcome:
    err_path = report_path.with_suffix(".err")
    try:
        res = subprocess.run(
            ["transitland", "rt-convert", "-f", "json", "-o", str(report_path), url],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return Outcome(
            bullet=f"- ❌ rt:{short} — `{url}` — timed out after 120s",
            blocker=True,
        )
    if res.returncode != 0:
        err_path.write_text(res.stderr)
        return Outcome(
            bullet=f"- ❌ rt:{short} — could not fetch or parse `{url}` — {trim(res.stderr)}",
            blocker=True,
        )

    try:
        data = json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return Outcome(
            bullet=f"- ❌ rt:{short} — `{url}` — could not parse rt-convert output: {e}",
            blocker=True,
        )

    header = data.get("header")
    if not header:
        return Outcome(
            bullet=f"- ❌ rt:{short} — `{url}` — parsed but missing header",
            blocker=True,
        )
    rt_ver = header.get("gtfs_realtime_version") or "?"
    ts = header.get("timestamp") or "?"
    entity_count = len(data.get("entity") or [])
    return Outcome(
        bullet=(
            f"- ✅ rt:{short} — `{url}` — RT v{rt_ver}, "
            f"{entity_count} entities, header timestamp {ts}"
        ),
        passed=True,
    )


def advisory_static_history(head_feed: dict, base_feed: Optional[dict]) -> Optional[Outcome]:
    """If static_current changed and the previous URL isn't in static_historic,
    advise the contributor to preserve URL history per Atlas convention."""
    if not base_feed:
        return None
    head_url = (head_feed.get("urls") or {}).get("static_current")
    base_url = (base_feed.get("urls") or {}).get("static_current")
    if not head_url or not base_url or head_url == base_url:
        return None
    historic = (head_feed.get("urls") or {}).get("static_historic") or []
    if base_url in historic:
        return None
    return Outcome(
        bullet=(
            f"- 💡 The previous `static_current` value `{base_url}` is not in "
            f"`urls.static_historic[]`. Atlas convention is to move the prior "
            f"URL into the historic list when updating `static_current`, so "
            f"editors can see how the feed has evolved over time. Informational "
            f"only — Transitland records each fetch URL regardless."
        ),
    )


def advisory_rt_operator_association(
    head_feed: dict,
    base_feed: Optional[dict],
    operator_feed_ids: set[str],
) -> Optional[Outcome]:
    """If a new gtfs-rt feed isn't referenced by any operator's
    associated_feeds, advise — Transitland fetches RT feeds via operator
    associations, so an unassociated RT feed will not be fetched."""
    if base_feed is not None:
        return None  # not a new feed
    if (head_feed.get("spec") or "").lower() != "gtfs-rt":
        return None
    fid = head_feed.get("id")
    if not fid or fid in operator_feed_ids:
        return None
    return Outcome(
        bullet=(
            f"- 💡 New RT feed `{fid}` is not referenced by any operator's "
            f"`associated_feeds[].feed_onestop_id`. Transitland fetches RT "
            f"feeds via operator associations; without this link the feed "
            f"will not be fetched."
        ),
    )


def render_feed_block(fid: str, outcomes: list[Outcome]) -> str:
    blocker = any(o.blocker for o in outcomes)
    passed = any(o.passed for o in outcomes)
    if blocker:
        icon, open_attr = "❌", " open"
    elif not passed:
        icon, open_attr = "⚪", ""
    else:
        icon, open_attr = "✅", ""
    bullets = "\n".join(o.bullet for o in outcomes)
    return (
        f"\n<details{open_attr}>\n"
        f"<summary>{icon} <code>{fid}</code></summary>\n\n"
        f"{bullets}\n\n"
        f"</details>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", required=True, help="Base ref to diff against (e.g. main).")
    parser.add_argument("--reports-dir", type=Path, required=True, help="Directory for per-URL JSON reports.")
    parser.add_argument("--summary-out", type=Path, default=None, help="Write Markdown summary here; defaults to stdout.")
    parser.add_argument("--feeds-dir", type=Path, default=Path("feeds"),
                        help="Directory holding all *.dmfr.json files (used for operator-association advisory).")
    parser.add_argument("files", nargs="*", help="Changed DMFR file paths.")
    args = parser.parse_args()

    args.reports_dir.mkdir(parents=True, exist_ok=True)

    # Pre-load each file's head and base versions once so passes share them.
    head_dmfrs: dict[str, Optional[dict]] = {}
    base_dmfrs: dict[str, Optional[dict]] = {}
    for fp in args.files:
        head_dmfrs[fp] = parse_dmfr_file(Path(fp))
        base_dmfrs[fp] = base_dmfr(fp, args.base_ref) if head_dmfrs[fp] is not None else None

    # Operator->feed associations across the whole working tree (head state).
    operator_feed_ids = load_operator_associated_feed_ids(args.feeds_dir)

    # 1. Compute URL tuples that are new or changed (in head, not in base).
    changed: set[UrlTuple] = set()
    for fp in args.files:
        head = head_dmfrs.get(fp)
        if head is None:
            continue
        head_set = url_tuples(head, fp)
        base = base_dmfrs.get(fp)
        base_set = url_tuples(base, fp) if base else set()
        changed |= (head_set - base_set)

    # 2. Per file, per feed: validate URL types whose tuple is in `changed`,
    #    accumulate per-feed outcomes, append style advisories, render a
    #    <details> block.
    summary_parts: list[str] = []
    any_blocker = False

    for fp in args.files:
        head = head_dmfrs.get(fp)
        if head is None:
            continue
        base = base_dmfrs.get(fp)
        for feed in head.get("feeds") or []:
            fid = feed.get("id")
            if not fid:
                continue
            auth_type = (feed.get("authorization") or {}).get("type")
            urls = feed.get("urls") or {}
            fbase = slugify(fid)

            outcomes: list[Outcome] = []

            static_url = urls.get("static_current")
            if isinstance(static_url, str) and (fp, fid, "static_current", static_url) in changed:
                if auth_type:
                    outcomes.append(Outcome(
                        bullet=f"- ⚪ static — skipped (auth: `{auth_type}`); validated by tlv2 — `{static_url}`",
                        skipped=True,
                    ))
                else:
                    outcomes.append(run_validate_static(static_url, args.reports_dir / f"{fbase}.static.json"))

            for short, dmfr_key in RT_KINDS.items():
                rt_url = urls.get(dmfr_key)
                if not isinstance(rt_url, str):
                    continue
                if (fp, fid, dmfr_key, rt_url) not in changed:
                    continue
                if auth_type:
                    outcomes.append(Outcome(
                        bullet=f"- ⚪ rt:{short} — skipped (auth: `{auth_type}`); validated by tlv2 — `{rt_url}`",
                        skipped=True,
                    ))
                else:
                    outcomes.append(run_rt_convert(short, rt_url, args.reports_dir / f"{fbase}.rt-{short}.json"))

            if not outcomes:
                continue

            # Style advisories — appended after URL outcomes, scoped to feeds
            # that already have some change in this PR.
            base_feed = feed_by_id(base, fid)
            for advisory in (
                advisory_static_history(feed, base_feed),
                advisory_rt_operator_association(feed, base_feed, operator_feed_ids),
            ):
                if advisory:
                    outcomes.append(advisory)

            any_blocker = any_blocker or any(o.blocker for o in outcomes)
            summary_parts.append(render_feed_block(fid, outcomes))

    summary_md = "".join(summary_parts)
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(summary_md)
    else:
        sys.stdout.write(summary_md)

    return 1 if any_blocker else 0


if __name__ == "__main__":
    sys.exit(main())
