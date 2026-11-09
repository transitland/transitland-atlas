#!/usr/bin/env -S uv run

"""
Re-pin feeds published on the OpenData Wrocław portal (open-data.cui.wroclaw.pl).

Wrocław publishes its public transport GTFS as a series of files in one
dataset. Every upload is a new file with its own numeric id, served at
/hdb/download/<id>/, and each covers only about two weeks of service. There is
no "latest" link, so a pinned URL goes stale within a fortnight.

A feed opts in with one tag naming the portal dataset:

    "tags": {
      "opendata_wroclaw_dataset": "6"
    }

For each tagged feed this lists the dataset's files from
https://api.open-data.cui.wroclaw.pl/od2/<dataset>/ and walks them newest first
(the ids are assigned in upload order), downloading each until it finds one to
pin.

"Newest" is not enough on its own. Around holidays the city uploads short
files covering a day or two -- 30 October only, then 31 October to 1 November
-- after the full fortnightly file, and pinning one of those would leave the
feed a day from expiry. So the file chosen is the newest one that is a GTFS
archive whose service covers today and runs at least MIN_DAYS_AHEAD days
beyond it. If no file qualifies, the newest one covering today is used, and if
none covers today the pin is left alone.

Two rules keep a bad resolve from being committed unattended:

  * The host is fixed here rather than read from the feed. Feed files accept
    pull requests from forks, so a tag naming a host would let a contributor
    choose where this job sends requests and whose answer it writes back.

  * The pin never moves to a lower file id than the one already pinned. Ids
    only grow, so a lower id is an older upload, and the comparison is not
    fooled by the portal re-listing or re-dating an old file.

Exit status is 0 when at least one tagged feed resolved, and 1 only when every
tagged feed failed, which means the portal or this script is broken rather
than flaky.

Usage:
    uv run scripts/update-opendata-wroclaw-urls.py [--dry-run] [--today YYYY-MM-DD]
"""

import argparse
import csv
import io
import json
import logging
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"

TAG = "opendata_wroclaw_dataset"
LIST_API = "https://api.open-data.cui.wroclaw.pl/od2/{dataset}/"
DOWNLOAD_URL = "https://open-data.cui.wroclaw.pl/hdb/download/{file_id}/"
DOWNLOAD_RE = re.compile(r"^https://open-data\.cui\.wroclaw\.pl/hdb/download/(\d+)/$")
TIMEOUT = 120
MIN_DAYS_AHEAD = 7
# Files are about 12 MB; stop walking back after this many so a run in which
# nothing qualifies does not download the whole archive.
MAX_CANDIDATES = 8
REQUIRED_FILES = ("agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def tagged_feeds(feeds_dir):
    """Yield (path, document, feed) for every feed carrying the dataset tag."""
    for path in sorted(Path(feeds_dir).glob("*.dmfr.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("%s: could not read (%s), skipping", path.name, e)
            continue
        for feed in doc.get("feeds", []):
            if (feed.get("tags") or {}).get(TAG):
                yield path, doc, feed


def file_ids(session, dataset):
    """Return the dataset's file ids, newest (highest) first."""
    if not re.fullmatch(r"\d+", str(dataset)):
        raise ValueError(f"dataset id {dataset!r} is not numeric")
    r = session.get(LIST_API.format(dataset=dataset), timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    ids = {int(x) for x in payload.get("pliki") or [] if str(x).isdigit()}
    if not ids:
        raise ValueError(f"dataset {dataset} lists no files")
    return sorted(ids, reverse=True)


def pinned_id(url):
    """The file id in a pinned download URL, or None if it is not one."""
    m = DOWNLOAD_RE.match(url or "")
    return int(m.group(1)) if m else None


def _parse_date(value):
    return datetime.strptime(value.strip(), "%Y%m%d").date()


def service_window(data):
    """Return (first, last) service dates of a GTFS zip, or None if it is not GTFS.

    The window is the span of calendar.txt and the added dates in
    calendar_dates.txt. That is coarser than resolving every service day, but
    the question here is only whether a file is current, not which days run.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = set(z.namelist())
    if not all(n in names for n in REQUIRED_FILES):
        return None

    def rows(name):
        if name not in names:
            return []
        with z.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))

    dates = []
    try:
        for r in rows("calendar.txt"):
            dates += [_parse_date(r["start_date"]), _parse_date(r["end_date"])]
        for r in rows("calendar_dates.txt"):
            if (r.get("exception_type") or "").strip() == "1":
                dates.append(_parse_date(r["date"]))
    except (KeyError, ValueError):
        return None
    if not dates:
        return None
    return min(dates), max(dates)


def choose(candidates, today, min_days_ahead=MIN_DAYS_AHEAD):
    """Pick the file to pin from [(file_id, window)], newest first.

    Returns the chosen file id, or None when no file covers today. A file
    whose window is None (not GTFS, or unreadable) is never chosen.
    """
    horizon = today + timedelta(days=min_days_ahead)
    covering = [(fid, w) for fid, w in candidates if w and w[0] <= today <= w[1]]
    for fid, (_, last) in covering:
        if last >= horizon:
            return fid
    return covering[0][0] if covering else None


def fetch(session, file_id):
    r = session.get(DOWNLOAD_URL.format(file_id=file_id), timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def resolve(session, dataset, current_url, today):
    """Return (url, reason) for one feed; reason is "current", "update" or a refusal."""
    ids = file_ids(session, dataset)
    current = pinned_id(current_url)
    candidates = []
    for fid in ids[:MAX_CANDIDATES]:
        if current is not None and fid < current:
            break  # never walk back past the pinned upload
        try:
            window = service_window(fetch(session, fid))
        except requests.RequestException as e:
            logger.warning("  file %s: could not download (%s)", fid, e)
            window = None
        logger.info("  file %s: service %s", fid, window)
        candidates.append((fid, window))
        horizon = today + timedelta(days=MIN_DAYS_AHEAD)
        if window and window[0] <= today <= window[1] and window[1] >= horizon:
            break  # the newest qualifying file; nothing older can beat it

    chosen = choose(candidates, today)
    if chosen is None:
        return None, "no file covers today"
    url = DOWNLOAD_URL.format(file_id=chosen)
    if url == current_url:
        return url, "current"
    return url, "update"


def write_feeds_file(path, doc):
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--today", type=date.fromisoformat,
                    default=datetime.now(timezone.utc).date(),
                    help="date to judge coverage against (default: today, UTC)")
    args = ap.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas update-opendata-wroclaw-urls"

    changed_docs = {}
    considered = resolved = updated = 0
    for path, doc, feed in tagged_feeds(FEEDS_DIR):
        considered += 1
        feed_id = feed.get("id", "?")
        dataset = feed["tags"][TAG]
        current_url = (feed.get("urls") or {}).get("static_current")
        logger.info("%s: dataset %s, pinned %s", feed_id, dataset, current_url)
        try:
            url, reason = resolve(session, dataset, current_url, args.today)
        except Exception as e:
            # Deliberately broad: one bad payload should cost this feed only.
            logger.error("%s: could not resolve dataset %s: %s", feed_id, dataset, e)
            continue
        resolved += 1
        if reason == "current":
            logger.info("%s: already current", feed_id)
            continue
        if reason != "update":
            logger.error("%s: %s, leaving as-is", feed_id, reason)
            continue
        logger.info("%s: %s -> %s", feed_id, current_url, url)
        feed.setdefault("urls", {})["static_current"] = url
        changed_docs[path] = doc
        updated += 1

    for path, doc in changed_docs.items():
        if args.dry_run:
            logger.info("would write %s", path.name)
        else:
            write_feeds_file(path, doc)
            logger.info("wrote %s", path.name)

    logger.info("%d tagged feed(s), %d resolved, %d updated", considered, resolved, updated)
    if considered and not resolved:
        logger.error("no tagged feed could be resolved")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
