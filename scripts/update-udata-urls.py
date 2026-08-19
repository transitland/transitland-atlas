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
picks the most recently published zip resource, and rewrites `static_current`
if it has moved. `static_historic` is deliberately left alone -- Luxembourg
alone has 300+ superseded snapshots and listing them is not useful.

Two rules keep a bad resolve from being committed unattended:

  * `udata_host` must be in ALLOWED_HOSTS. Feed files accept pull requests from
    forks, so the host is contributor-controlled input that decides where this
    job sends a request and whose answer it writes back. Adding a portal is
    deliberately a change to this file, which forks cannot touch.

  * Recency is taken from `created_at`, never from `last_modified`, and the
    chosen resource may not be older than the one already pinned. udata bumps
    `last_modified` on any metadata edit, so a retitle or reharvest of a
    years-old archive would otherwise win and silently downgrade the feed --
    and nothing downstream would notice, because a stale GTFS still fetches,
    parses, and has agencies.

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

# udata portals this job is allowed to contact. See the note above: this list
# is the reason a feed file cannot redirect the job somewhere arbitrary.
ALLOWED_HOSTS = frozenset(
    {
        "data.public.lu",
        "data.gouv.fr",
        "www.data.gouv.fr",
        "transport.data.gouv.fr",
        "data.gov.rs",
    }
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def tagged_feeds(feeds_dir):
    """Yield (path, document, feed) for every feed carrying the udata tags.

    A file that cannot be read or parsed is skipped rather than allowed to end
    the scan; one bad file should not strand every other feed.
    """
    for path in sorted(Path(feeds_dir).glob("*.dmfr.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # ValueError covers JSONDecodeError and UnicodeDecodeError alike.
            logger.warning("%s: could not read (%s), skipping", path.name, e)
            continue
        for feed in doc.get("feeds", []):
            tags = feed.get("tags") or {}
            if tags.get("udata_host") and tags.get("udata_dataset"):
                yield path, doc, feed


def _published_at(resource):
    """Recency key for a resource; missing or unusable timestamps sort oldest.

    `created_at` is checked before `last_modified` on purpose. For a publisher
    that mints one resource per snapshot, creation time is when that snapshot
    appeared and nothing later moves it, whereas `last_modified` is bumped by
    any metadata edit -- a retitle or reharvest of a years-old archive would
    otherwise make it the "newest" resource and downgrade the feed.

    Everything returned here is timezone-aware. udata normally supplies an
    offset, but one resource without a usable timestamp would otherwise make
    the whole list uncomparable and take down the run.
    """
    for key in ("created_at", "last_modified"):
        value = resource.get(key)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def zip_resources(session, host, slug):
    """Return the dataset's zip resources, each guaranteed to carry a URL."""
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"{host} is not an allowed udata host")
    r = session.get(DATASET_API.format(host=host, slug=slug), timeout=TIMEOUT)
    r.raise_for_status()
    resources = r.json().get("resources") or []
    zips = [
        res
        for res in resources
        # A resource with no URL cannot be pinned, whatever its format says.
        if (res.get("url") or "").strip()
        and (
            (res.get("format") or "").lower() == "zip"
            or res["url"].lower().endswith(".zip")
        )
    ]
    if not zips:
        raise ValueError(f"no zip resources in {host}/{slug}")
    return zips


def newest_zip(session, host, slug):
    """Return the most recently modified zip resource for a udata dataset."""
    return max(zip_resources(session, host, slug), key=_published_at)


def pick_target(zips, current_url):
    """Choose the resource to pin.

    Returns (resource, reason), where reason is one of:
      "current"   -- the newest resource is already pinned
      "not-newer" -- the newest resource is not newer than what is pinned, so
                     the existing pin stands (see the module docstring)
      "update"    -- the newest resource should replace the current pin

    A `current_url` absent from the dataset means the feed is adopting the
    portal for the first time, or moving off some other host, so there is
    nothing to compare against and the move is allowed.
    """
    newest = max(zips, key=_published_at)
    if newest["url"] == current_url:
        return newest, "current"
    pinned = next((res for res in zips if res["url"] == current_url), None)
    if pinned is not None and _published_at(newest) <= _published_at(pinned):
        return newest, "not-newer"
    return newest, "update"


def url_is_live(session, url):
    """HEAD the URL, falling back to a single-byte GET for servers that 405."""
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 405:
            # Streamed: Range is only a hint, and a server that ignores it
            # would otherwise hand us the whole archive to read a status code.
            r = session.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=True,
                stream=True,
                headers={"Range": "bytes=0-0"},
            )
            try:
                return r.status_code < 400
            finally:
                r.close()
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
            zips = zip_resources(session, host, slug)
        except Exception as e:
            # Deliberately broad: one malformed resource or unexpected payload
            # shape should cost this feed, not every other feed in the run.
            logger.error("%s: could not resolve %s/%s: %s", feed_id, host, slug, e)
            continue
        resolved += 1

        current_url = (feed.get("urls") or {}).get("static_current")
        resource, reason = pick_target(zips, current_url)
        newest_url = resource["url"]

        if reason == "current":
            logger.info("%s: already current (%s)", feed_id, resource.get("title"))
            continue
        if reason == "not-newer":
            logger.error(
                "%s: newest resource %s is not newer than the pinned one, leaving as-is",
                feed_id,
                newest_url,
            )
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
