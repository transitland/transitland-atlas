#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///

"""Validate many candidate GTFS URLs and print one row each.

The companion to `compare-feed-urls.py`. That one answers "which of these two
or three URLs should we register", in depth, for a handful. This one answers
"which of these several dozen are worth looking at", shallowly, for a whole
discovery-source backlog.

Both go through `transitland validate` rather than reading the zip directly, so
calendar coverage and entity counts agree with what the platform will compute.

Reads URLs from arguments, from a file with --urls-from, or from a
scan-feed-sources.py report with --report, and sorts the output so the rows
needing a decision are together: failures first, then expired by how long, then
active.

Usage:
  cd scripts && uv run probe-feed-urls.py URL [URL ...]
  cd scripts && uv run probe-feed-urls.py --urls-from candidates.txt
  cd scripts && uv run probe-feed-urls.py --report ../scan-output/report.json \\
      --source ntd-weblinks --kind agency-not-in-atlas
"""

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import date

from rich.console import Console
from rich.table import Table

import feed_probe

console = Console()


def rows_from_report(path: str, source: str, kind: str | None) -> list[dict]:
    report = json.load(open(path))
    findings = (report.get(source) or {}).get("findings") or []
    if kind:
        findings = [f for f in findings if f.get("kind") == kind]
    return [{"url": f["weblink"], "label": f"{f.get('ntd_id','')} {f.get('name','')}".strip(),
             "state": f.get("state", "")} for f in findings]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*", help="candidate URLs")
    ap.add_argument("--urls-from", help="file with one URL per line")
    ap.add_argument("--report", help="a scan-feed-sources.py report.json")
    ap.add_argument("--source", default="ntd-weblinks", help="source within the report")
    ap.add_argument("--kind", help="only findings of this kind")
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent validations (default 6); each spawns transitland validate")
    ap.add_argument("--json-out", help="write the full result set here")
    args = ap.parse_args()

    items: list[dict] = [{"url": u, "label": "", "state": ""} for u in args.urls]
    if args.urls_from:
        items += [{"url": line.strip(), "label": "", "state": ""}
                  for line in open(args.urls_from) if line.strip()]
    if args.report:
        items += rows_from_report(args.report, args.source, args.kind)
    if not items:
        ap.error("no URLs given")

    key = os.environ.get("TRANSITLAND_API_KEY", "")
    if not key:
        console.print("[dim]TRANSITLAND_API_KEY unset: skipping the archive check.[/dim]")

    console.print(f"validating {len(items)} URL(s) with {args.workers} workers...\n")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(feed_probe.validate_feed, it["url"]): it for it in items}
        for fut in concurrent.futures.as_completed(futures):
            it = futures[fut]
            summary = feed_probe.summarise(fut.result())
            summary["label"], summary["state"] = it["label"], it["state"]
            if summary.get("ok") and key:
                summary["archive"] = feed_probe.archive_match(
                    feed_probe.lookup_feed_version(summary.get("sha1", ""), key))
            results.append(summary)

    # Failures first, then expired longest-first, then active. That is the order
    # a reviewer wants: the rows needing a decision are together at the top.
    def sort_key(r):
        if not r.get("ok"):
            return (0, 0, r.get("label", ""))
        if r.get("expired_days"):
            return (1, -r["expired_days"], r.get("label", ""))
        return (2, 0, r.get("label", ""))

    results.sort(key=sort_key)

    table = Table(show_lines=False)
    for col in ("agency / label", "st", "rt", "calendar", "status", "archive", "url"):
        table.add_column(col, overflow="fold")
    for r in results:
        if not r.get("ok"):
            table.add_row(r.get("label") or "?", r.get("state", ""), "-", "-",
                          f"[red]{r.get('error','')[:40]}[/red]", "-", r["url"][:52])
            continue
        arc = r.get("archive")
        style = "red" if r.get("expired_days") else ""
        cal = f"{r.get('earliest_calendar_date') or '?'} to {r.get('latest_calendar_date') or '?'}"
        table.add_row(
            (r.get("agency") or r.get("label") or "?")[:34],
            r.get("state", ""),
            str(r.get("routes", "")),
            cal,
            f"[{style}]{r['calendar_status']}[/{style}]" if style else r["calendar_status"],
            (arc or {}).get("feed_onestop_id", "") if arc else "",
            r["url"][:52])
    console.print(table)

    ok = [r for r in results if r.get("ok")]
    expired = [r for r in ok if r.get("expired_days")]
    console.print(f"\n{len(ok)}/{len(results)} validated; {len(expired)} expired; "
                  f"{sum(1 for r in ok if r.get('archive'))} already in the archive")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(results, fh, indent=1)
        console.print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
