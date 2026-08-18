#!/usr/bin/env -S uv run

"""
Re-pin feeds whose publisher mints a new udata resource per snapshot.

Portals running udata (data.public.lu, data.gouv.fr, data.gov.rs, ...) expose
each dataset as a list of resources. Most publishers update one resource in
place, so its /datasets/r/<uuid> permalink stays valid forever and nothing here
is needed. A few publish every snapshot as a *new* resource with its own URL,
which leaves `static_current` pointing at an archived file as soon as the next
one lands. Luxembourg does this weekly.

A feed opts in by carrying both of these tags:

    "tags": {
      "udata_host": "data.public.lu",
      "udata_dataset": "horaires-et-arrets-des-transport-publics-gtfs"
    }

For each tagged feed this queries https://<udata_host>/api/1/datasets/<slug>/,
picks the most recently modified zip resource, and rewrites `static_current` if
it has moved. `static_historic` is deliberately left alone -- Luxembourg alone
has 300+ superseded snapshots and listing them is not useful.

Exit status is 0 when at least one tagged feed resolved, so that one portal
being briefly unreachable does not block the auto-PR for the others. It is 1
only when every tagged feed failed, which means the portal API or this script
is broken rather than flaky.

Usage:
    uv run scripts/update-udata-urls.py [--dry-run] [--no-verify]
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"

DATASET_API = "https://{host}/api/1/datasets/{slug}/"
TIMEOUT = 60

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def tagged_feeds(feeds_dir):
    """Yield (path, document, feed) for every feed carrying the udata tags."""
    for path in sorted(feeds_dir.glob("*.dmfr.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("%s: not valid JSON (%s), skipping", path.name, e)
            continue
        for feed in doc.get("feeds", []):
            tags = feed.get("tags") or {}
            if tags.get("udata_host") and tags.get("udata_dataset"):
                yield path, doc, feed


def _modified_at(resource):
    """Sort key for a resource; missing or unparseable timestamps sort oldest.

    Everything returned here is timezone-aware. udata normally supplies an
    offset, but one resource without a usable timestamp would otherwise make
    the whole list uncomparable and take down the run.
    """
    for key in ("last_modified", "created_at"):
        value = resource.get(key)
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def newest_zip(session, host, slug):
    """Return the most recently modified zip resource for a udata dataset."""
    r = session.get(DATASET_API.format(host=host, slug=slug), timeout=TIMEOUT)
    r.raise_for_status()
    resources = r.json().get("resources") or []
    zips = [
        res
        for res in resources
        if (res.get("format") or "").lower() == "zip"
        or (res.get("url") or "").lower().endswith(".zip")
    ]
    if not zips:
        raise ValueError(f"no zip resources in {host}/{slug}")
    return max(zips, key=_modified_at)


def url_is_live(session, url):
    """HEAD the URL, falling back to a single-byte GET for servers that 405."""
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 405:
            r = session.get(
                url, timeout=TIMEOUT, allow_redirects=True, headers={"Range": "bytes=0-0"}
            )
        return r.status_code < 400
    except requests.RequestException as e:
        logger.warning("  could not reach %s (%s)", url, e)
        return False


def write_feeds_file(path, doc):
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument(
        "--no-verify", action="store_true", help="skip the reachability check"
    )
    args = ap.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = "transitland-atlas update-udata-urls"

    # Several feeds can share one file, so collect edits and write each file once.
    changed_docs = {}
    considered = resolved = updated = 0

    for path, doc, feed in tagged_feeds(FEEDS_DIR):
        considered += 1
        tags = feed["tags"]
        host, slug = tags["udata_host"], tags["udata_dataset"]
        feed_id = feed.get("id", "?")

        try:
            resource = newest_zip(session, host, slug)
        except (requests.RequestException, ValueError) as e:
            logger.error("%s: could not resolve %s/%s: %s", feed_id, host, slug, e)
            continue
        resolved += 1

        newest_url = resource["url"]
        current_url = (feed.get("urls") or {}).get("static_current")
        if newest_url == current_url:
            logger.info("%s: already current (%s)", feed_id, resource.get("title"))
            continue

        if not args.no_verify and not url_is_live(session, newest_url):
            logger.error("%s: newest resource is not reachable, leaving as-is", feed_id)
            continue

        logger.info("%s: %s -> %s", feed_id, current_url, newest_url)
        feed.setdefault("urls", {})["static_current"] = newest_url
        changed_docs[path] = doc
        updated += 1

    for path, doc in changed_docs.items():
        if args.dry_run:
            logger.info("would write %s", path.name)
        else:
            write_feeds_file(path, doc)
            logger.info("wrote %s", path.name)

    logger.info(
        "%d tagged feed(s), %d resolved, %d updated", considered, resolved, updated
    )
    if considered and not resolved:
        logger.error("no tagged feed could be resolved")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
