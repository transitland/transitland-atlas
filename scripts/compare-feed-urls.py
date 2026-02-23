#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["rich"]
# ///
"""
Compare two or more GTFS feed URLs to assess which is more recent/active,
or whether they represent meaningfully different feeds.

Usage:
    uv run scripts/compare-feed-urls.py <url1> <url2> [url3 ...]
"""

import os
import sys
import json
import subprocess
import concurrent.futures
import urllib.request
import urllib.error
from datetime import date
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel


def validate_feed(url: str) -> dict:
    try:
        result = subprocess.run(
            [
                "transitland", "validate",
                "-o", "-",
                "--include-entities",
                "--include-service-levels",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0 and not result.stdout.strip():
            return {"_error": result.stderr.strip() or "Command failed", "_url": url}
        data = json.loads(result.stdout)
        data["_url"] = url
        return data
    except subprocess.TimeoutExpired:
        return {"_error": "Timeout after 180s", "_url": url}
    except json.JSONDecodeError as e:
        return {"_error": f"Invalid JSON: {e}", "_url": url}
    except FileNotFoundError:
        return {"_error": "'transitland' command not found in PATH", "_url": url}


ROUTE_TYPE_NAMES = {
    0: "Tram/Streetcar",
    1: "Subway/Metro",
    2: "Rail",
    3: "Bus",
    4: "Ferry",
    5: "Cable tram",
    6: "Gondola/Aerial lift",
    7: "Funicular",
    11: "Trolleybus",
    12: "Monorail",
}

TRANSITLAND_API_BASE = "https://transit.land/api/v2/rest"


def lookup_feed_version(sha1: str, api_key: str) -> dict:
    """Query the Transitland REST API for a feed version by SHA1."""
    if not sha1 or sha1 == "N/A":
        return {"_error": "no SHA1"}
    if not api_key:
        return {"_error": "no API key"}
    url = f"{TRANSITLAND_API_BASE}/feed_versions/{sha1}?apikey={api_key}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"feed_versions": []}
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:
        return {"_error": str(e)}


def status_cell(data: dict) -> Text:
    err = data.get("_error")
    ok = not err and data.get("success", False)
    label = "success" if ok else (err or "failed")
    return Text(label, style="green" if ok else "red")


def sha1_cell(sha1: str, all_same: bool) -> Text:
    short = sha1[:16] + "..." if len(sha1) > 16 else sha1
    if all_same:
        return Text(short + "  [IDENTICAL]", style="green")
    return Text(short)


def archive_cell(result: dict) -> Text:
    err = result.get("_error")
    if err == "no API key":
        return Text("(set TRANSITLAND_API_KEY)", style="dim")
    if err == "no SHA1":
        return Text("N/A", style="dim")
    if err:
        return Text(f"error: {err}", style="red")
    fvs = result.get("feed_versions") or []
    if not fvs:
        return Text("not in archive", style="yellow")
    fv = fvs[0]
    feed = fv.get("feed") or {}
    onestop_id = feed.get("onestop_id", "?")
    fetched_at = (fv.get("fetched_at") or "?").split("T")[0]
    earliest = fv.get("earliest_calendar_date", "?")
    latest = fv.get("latest_calendar_date", "?")
    archived_url = fv.get("url", "")
    lines = [
        f"feed: {onestop_id}",
        f"fetched: {fetched_at}",
        f"calendar: {earliest} to {latest}",
    ]
    if archived_url:
        lines.append(f"archived from: {archived_url}")
    return Text("\n".join(lines), style="green")


def calendar_cell(data: dict) -> str:
    d = data.get("details") or {}
    earliest = d.get("earliest_calendar_date")
    latest = d.get("latest_calendar_date")
    if not earliest or not latest:
        return "N/A"
    today = date.today()
    try:
        e = date.fromisoformat(earliest)
        l = date.fromisoformat(latest)
        span = (l - e).days
        if today < e:
            status = f"future (starts in {(e - today).days}d)"
        elif today > l:
            status = f"EXPIRED ({(today - l).days}d ago)"
        else:
            pct = int(100 * (today - e).days / span) if span > 0 else 0
            status = f"active ({pct}% through)"
        return f"{earliest} to {latest}\n({span} days, {status})"
    except ValueError:
        return f"{earliest} to {latest}"


def agencies_cell(data: dict) -> str:
    agencies = (data.get("details") or {}).get("agencies") or []
    if not agencies:
        return "(none)"
    names = sorted(a.get("agency_name", "?") for a in agencies)
    count = len(names)
    if count > 8:
        return f"{count} agencies:\n" + "\n".join(names[:8]) + f"\n... (+{count - 8} more)"
    return f"{count} {'agency' if count == 1 else 'agencies'}:\n" + "\n".join(names)


def routes_cell(data: dict) -> str:
    routes = (data.get("details") or {}).get("routes")
    if routes is None:
        return "N/A"
    if not routes:
        return "0 routes"
    counts: dict[str, int] = {}
    for r in routes:
        rt = r.get("route_type")
        name = ROUTE_TYPE_NAMES.get(rt, f"Type {rt}")
        counts[name] = counts.get(name, 0) + 1
    lines = [f"Total: {len(routes)}"]
    for name, count in sorted(counts.items()):
        lines.append(f"  {name}: {count}")
    return "\n".join(lines)


def feed_info_cell(data: dict) -> str:
    fis = (data.get("details") or {}).get("feed_infos") or []
    if not fis:
        return "(no feed_info.txt)"
    fi = fis[0]
    fields = [
        ("feed_publisher_name", "Publisher"),
        ("feed_version", "Version"),
        ("feed_start_date", "Start date"),
        ("feed_end_date", "End date"),
        ("feed_lang", "Language"),
        ("feed_contact_email", "Contact"),
        ("feed_contact_url", "Contact URL"),
    ]
    parts = [f"{label}: {fi[key]}" for key, label in fields if fi.get(key)]
    return "\n".join(parts) if parts else "(empty feed_info.txt)"


def count_cell(data: dict, key: str) -> str:
    items = (data.get("details") or {}).get(key)
    if items is None:
        return "N/A"
    return str(len(items))


def errors_cell(data: dict) -> str:
    errors = data.get("errors") or []
    warnings = data.get("warnings") or []
    if not errors and not warnings:
        return "none"
    parts = []
    if errors:
        parts.append(f"{len(errors)} error(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    return ", ".join(parts)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    urls = sys.argv[1:]
    n = len(urls)
    console = Console()

    url_lines = "\n".join(f"[bold]URL {i + 1}:[/bold] {url}" for i, url in enumerate(urls))
    console.print(Panel(url_lines, title="[bold blue]GTFS Feed Comparison[/bold blue]"))

    api_key = os.environ.get("TRANSITLAND_API_KEY", "")
    if not api_key:
        console.print("[dim]Tip: set TRANSITLAND_API_KEY to also check the Transitland archive.[/dim]\n")

    console.print("[dim]Validating feeds in parallel (this may take a minute)...[/dim]\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n * 2) as executor:
        val_futs = [executor.submit(validate_feed, url) for url in urls]
        results = [f.result() for f in val_futs]
        sha1s = [(r.get("details") or {}).get("sha1", "N/A") for r in results]
        arc_futs = [executor.submit(lookup_feed_version, sha1, api_key) for sha1 in sha1s]
        arc_results = [f.result() for f in arc_futs]

    table = Table(show_lines=True, expand=True, title="Feed Comparison")
    table.add_column("Metric", style="bold", min_width=16, no_wrap=True)
    for i in range(n):
        table.add_column(f"URL {i + 1}", ratio=1)

    # --- Status ---
    table.add_row("Status", *[status_cell(r) for r in results])

    # --- SHA1 ---
    valid_sha1s = [s for s in sha1s if s not in ("N/A", None)]
    all_same = len(valid_sha1s) == n and len(set(valid_sha1s)) == 1
    table.add_row("SHA1", *[sha1_cell(s, all_same) for s in sha1s])

    # --- Transitland archive lookup ---
    table.add_row("In TL archive", *[archive_cell(a) for a in arc_results])

    # --- Calendar coverage ---
    table.add_row("Calendar\ncoverage", *[calendar_cell(r) for r in results])

    # --- Agencies ---
    table.add_row("Agencies", *[agencies_cell(r) for r in results])

    # --- Routes ---
    table.add_row("Routes", *[routes_cell(r) for r in results])

    # --- Stops & Trips counts ---
    table.add_row("Stops", *[count_cell(r, "stops") for r in results])
    table.add_row("Trips", *[count_cell(r, "trips") for r in results])

    # --- Feed info ---
    table.add_row("feed_info.txt", *[feed_info_cell(r) for r in results])

    # --- Validation errors/warnings ---
    table.add_row("Errors/warnings", *[errors_cell(r) for r in results])

    console.print(table)

    # --- Verdict ---
    ok_flags = [not r.get("_error") and r.get("success", False) for r in results]

    if all_same:
        console.print(Panel(
            "[bold green]All URLs serve IDENTICAL content (same SHA1 hash).[/bold green]",
            title="Verdict",
        ))
    elif not all(ok_flags):
        failed = [f"URL {i + 1}" for i, ok in enumerate(ok_flags) if not ok]
        console.print(Panel(
            f"[bold red]{', '.join(failed)} failed validation. Check status row above.[/bold red]",
            title="Verdict",
        ))
    else:
        latest_dates = [(r.get("details") or {}).get("latest_calendar_date", "") for r in results]
        best_date = max(d for d in latest_dates if d)
        best_idxs = [i for i, d in enumerate(latest_dates) if d == best_date]
        if len(set(d for d in latest_dates if d)) > 1:
            best_labels = " and ".join(f"URL {i + 1}" for i in best_idxs)
            other_dates = sorted(set(d for d in latest_dates if d and d != best_date), reverse=True)
            console.print(Panel(
                f"[yellow]Feeds differ.[/yellow] [bold]{best_labels}[/bold] has the most recent "
                f"calendar coverage ({best_date} vs {', '.join(other_dates)}).\n"
                "Review agencies, routes, and feed_info above to assess if they represent different services.",
                title="Verdict",
            ))
        else:
            console.print(Panel(
                "[yellow]Feeds differ.[/yellow] Review the table above to assess which is more recent or active.",
                title="Verdict",
            ))


if __name__ == "__main__":
    main()
