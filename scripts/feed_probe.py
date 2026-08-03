"""Validate GTFS URLs through transitland-lib and read the result.

Shared by `compare-feed-urls.py`, which renders a few URLs side by side, and
`probe-feed-urls.py`, which renders many one per row. Both need the same three
things -- run the validator, ask the archive whether it has seen this SHA1, and
pull the handful of fields a registration decision turns on -- so those live
here rather than in either script.

Everything is read through `transitland validate` rather than by opening the
zip directly. Hand-rolled zip reading gets the easy fields right and then
quietly disagrees with the platform on the ones that matter, calendar coverage
and service levels especially, because it does not apply the same rules.

Stdlib only, so a PEP 723 script can import it without declaring a dependency.
"""

import json
import subprocess
import urllib.error
import urllib.request
from datetime import date

TRANSITLAND_API_BASE = "https://transit.land/api/v2/rest"
VALIDATE_TIMEOUT = 180


def validate_feed(url: str, timeout: int = VALIDATE_TIMEOUT) -> dict:
    """Run `transitland validate` and return its report, or {"_error": ...}."""
    try:
        result = subprocess.run(
            ["transitland", "validate", "-o", "-",
             "--include-entities", "--include-service-levels", url],
            capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0 and not result.stdout.strip():
            return {"_error": result.stderr.strip() or "Command failed", "_url": url}
        data = json.loads(result.stdout)
        data["_url"] = url
        return data
    except subprocess.TimeoutExpired:
        return {"_error": f"Timeout after {timeout}s", "_url": url}
    except json.JSONDecodeError as e:
        return {"_error": f"Invalid JSON: {e}", "_url": url}
    except FileNotFoundError:
        return {"_error": "'transitland' command not found in PATH", "_url": url}


def lookup_feed_version(sha1: str, api_key: str) -> dict:
    """Ask the Transitland archive whether it already holds this exact file."""
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
        return {"feed_versions": []} if e.code == 404 else {"_error": f"HTTP {e.code}"}
    except Exception as e:
        return {"_error": str(e)}


def calendar_status(earliest: str, latest: str, today: date | None = None) -> tuple[str, int | None]:
    """Return (status, days_expired). days_expired is None unless expired.

    Expiry is the field a registration decision turns on most often, and the
    distinction worth keeping is recent versus long: a feed a month past its
    calendar is usually mid-refresh, one several years past has been abandoned.
    """
    today = today or date.today()
    if not earliest or not latest:
        return "unknown", None
    try:
        e, l = date.fromisoformat(earliest), date.fromisoformat(latest)
    except ValueError:
        return "unparseable", None
    if today < e:
        return f"future (starts in {(e - today).days}d)", None
    if today > l:
        return f"expired {(today - l).days}d", (today - l).days
    span = (l - e).days
    pct = int(100 * (today - e).days / span) if span > 0 else 0
    return f"active ({pct}% through)", None


def summarise(data: dict, today: date | None = None) -> dict:
    """Flatten a validation report into the fields a registration turns on."""
    if data.get("_error"):
        return {"url": data.get("_url", ""), "ok": False, "error": data["_error"]}

    d = data.get("details") or {}
    sha1 = d.get("sha1") or data.get("sha1") or ""
    agencies = [a.get("agency_name", "") for a in (d.get("agencies") or [])]
    routes = d.get("routes") or []

    # `details.files` carries a row count per file and is populated even when the
    # entity arrays are not. They do disagree: a feed with seven rows in
    # routes.txt and no errors can still come back with an empty details.routes,
    # which reads as "not a feed" and is how a real feed gets written off.
    rows = {f.get("name"): f.get("rows") for f in (d.get("files") or []) if f.get("name")}
    route_rows = rows.get("routes.txt")
    earliest, latest = d.get("earliest_calendar_date"), d.get("latest_calendar_date")
    status, expired_days = calendar_status(earliest, latest, today)

    fi = (d.get("feed_infos") or [{}])
    fi = fi[0] if fi else {}

    return {
        "url": data.get("_url", ""),
        "ok": True,
        "sha1": sha1,
        "agencies": agencies,
        "agency": agencies[0] if agencies else "",
        "routes": len(routes) if routes else (route_rows or 0),
        "routes_from_file_rows": bool(not routes and route_rows),
        "file_rows": rows,
        "stops": rows.get("stops.txt"),
        "trips": rows.get("trips.txt"),
        "earliest_calendar_date": earliest,
        "latest_calendar_date": latest,
        "calendar_status": status,
        "expired_days": expired_days,
        "feed_version": fi.get("feed_version"),
        "feed_publisher_name": fi.get("feed_publisher_name"),
        "errors": len(data.get("errors") or []),
        "warnings": len(data.get("warnings") or []),
    }


def archive_match(result: dict) -> dict | None:
    """The registered feed this exact file already belongs to, if any."""
    if not result or result.get("_error"):
        return None
    versions = result.get("feed_versions") or []
    if not versions:
        return None
    fv = versions[0]
    return {
        "feed_onestop_id": (fv.get("feed") or {}).get("onestop_id"),
        "fetched_at": fv.get("fetched_at"),
        "url": fv.get("url"),
    }
