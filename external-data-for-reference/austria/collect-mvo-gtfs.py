#!/usr/bin/env -S uv run
"""
Sync the Austrian GTFS feeds published by Mobilitätsverbünde Österreich (MVO).

MVO (data.mobilitaetsverbuende.at) is where Austria's national access point
(mobilitydata.gv.at) points for GTFS. Its dataset catalog is public, although
downloading a file needs a registered account; Transitland fetches copies from
gtfs-source-feeds.transit.land/at-mvo-<dataset_id>.zip.

This script will:
1. Read the MVO catalog and keep the active datasets tagged GTFS
2. Update the feeds tagged at_mvo_dataset_id in
   feeds/gtfs-source-feeds.transit.land.dmfr.json to match: add new datasets,
   refresh existing records, and remove records whose dataset is gone

That file is shared with other feeds. Only feeds tagged at_mvo_dataset_id are
managed here, and an existing record keeps its Onestop ID, name, operators and
any other hand-curated fields. A new dataset gets a generated Onestop ID and
name, to be reviewed in the auto-PR.

Usage:
    uv run collect-mvo-gtfs.py
"""

import copy
import json
import logging
import re
import unicodedata
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
DMFR_FILE = REPO_ROOT / "feeds" / "gtfs-source-feeds.transit.land.dmfr.json"
MANAGED_TAG = "at_mvo_dataset_id"
FEED_URL_TEMPLATE = "https://gtfs-source-feeds.transit.land/at-mvo-{dataset_id}.zip"
CATALOG_URL = "https://data.mobilitaetsverbuende.at/api/public/v1/data-sets"
# More removals than this in one run stops the script instead of writing.
MAX_REMOVED_FEEDS = 2

# GTFS datasets MVO lists that are deliberately not registered. Without this
# list each run would add them back.
EXCLUDED_DATASET_IDS = {
    "70": "Rail snapshot taken at the timetable change; 66 is the same data kept current",
    "72": "Steiermark in nine parts without ÖBB; 53 is the whole Verbund",
    "73": "Salzburg GTFS-Flex variant; the plain feed (54) is registered",
    "74": "Linz AG GTFS-Flex variant; the plain feed (69) is registered",
    "75": "Vorarlberg GTFS-Flex variant; the plain feed (58) is registered",
    "77": "OÖVV GTFS-Flex variant; the plain feed (56) is registered",
    "78": "Kärntner Linien GTFS-Flex variant; the plain feed (55) is registered",
    "79": "Steiermark GTFS-Flex variant; the plain feed (53) is registered",
    "80": "VOR GTFS-Flex variant; the plain feed (52) is registered",
}

LICENSE = {
    "url": "https://mobilitaetsverbuende.atlassian.net/wiki/spaces/DBP/pages/185237505/Terms+of+Use",
    "use_without_attribution": "no",
    "create_derived_product": "yes",
    "redistribution_allowed": "yes",
    "commercial_use_allowed": "yes",
    "attribution_text": "Data source: Mobilitätsverbünde Österreich OG - data.mobilitaetsverbuende.at",
}
AUTHORIZATION = {"type": "query_param", "param_name": "token"}


def get_gtfs_datasets():
    r = requests.get(CATALOG_URL, params={"tagFilterModeInclusive": "true"}, timeout=60)
    r.raise_for_status()
    return [
        ds
        for ds in r.json()
        if ds.get("active") and any(t.get("valueDe") == "GTFS" for t in ds.get("tags", []))
    ]


def feed_name(dataset):
    """'Fahrplandaten Verkehrsverbund Tirol (GTFS)' -> 'Verkehrsverbund Tirol'."""
    name = re.sub(r"^Fahrplandaten\s+", "", dataset["nameDe"])
    return re.sub(r"\s*\(GTFS\)", "", name).strip()


def onestop_id(name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "~", ascii_name.lower()).strip("~")
    return f"f-{slug}~at"


def create_dmfr_feed(dataset):
    name = feed_name(dataset)
    return {
        "id": onestop_id(name),
        "name": name,
        "spec": "gtfs",
        "urls": {"static_current": FEED_URL_TEMPLATE.format(dataset_id=dataset["id"])},
        "license": copy.deepcopy(LICENSE),
        "authorization": copy.deepcopy(AUTHORIZATION),
        "tags": {MANAGED_TAG: dataset["id"]},
    }


def save_dmfr_file(new_feeds_by_id):
    with open(DMFR_FILE, encoding="utf-8") as f:
        dmfr = json.load(f)

    managed, others = {}, []
    for feed in dmfr.get("feeds", []):
        dataset_id = feed.get("tags", {}).get(MANAGED_TAG)
        if dataset_id:
            managed[dataset_id] = feed
        else:
            others.append(feed)

    removed = [f for dataset_id, f in managed.items() if dataset_id not in new_feeds_by_id]
    for feed in removed:
        logger.warning(f"Removing {feed['id']} (dataset {feed['tags'][MANAGED_TAG]}): no longer in the MVO catalog")
    if len(removed) > MAX_REMOVED_FEEDS:
        logger.error(f"{len(removed)} managed feeds would be removed (limit {MAX_REMOVED_FEEDS}); aborting without writing")
        raise SystemExit(1)

    updated = []
    for dataset_id, existing in managed.items():
        if dataset_id not in new_feeds_by_id:
            continue
        new = new_feeds_by_id.pop(dataset_id)
        merged = copy.deepcopy(existing)
        merged.setdefault("urls", {})["static_current"] = new["urls"]["static_current"]
        merged["license"] = new["license"]
        merged["authorization"] = new["authorization"]
        merged.setdefault("tags", {}).update(new["tags"])
        updated.append(merged)

    taken = {f["id"] for f in updated} | {f["id"] for f in others}
    added = 0
    for dataset_id, feed in new_feeds_by_id.items():
        if feed["id"] in taken:
            logger.error(f"Skipping new dataset {dataset_id}: Onestop ID {feed['id']} already exists in {DMFR_FILE.name}")
            continue
        logger.info(f"Adding {feed['id']} (dataset {dataset_id}): {feed['name']}")
        taken.add(feed["id"])
        updated.append(feed)
        added += 1

    dmfr["feeds"] = updated + others
    with open(DMFR_FILE, "w", encoding="utf-8") as f:
        json.dump(dmfr, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info(f"{len(updated) - added} existing, {added} new, {len(removed)} removed MVO feeds")


def main():
    datasets = get_gtfs_datasets()
    # An empty listing is an API change or outage, not every feed retiring at once.
    if not datasets:
        logger.error("MVO catalog returned no GTFS datasets; aborting without writing")
        raise SystemExit(1)
    feeds = {}
    for ds in datasets:
        if ds["id"] in EXCLUDED_DATASET_IDS:
            logger.info(f"Skipping excluded dataset {ds['id']}: {EXCLUDED_DATASET_IDS[ds['id']]}")
            continue
        feeds[ds["id"]] = create_dmfr_feed(ds)
    save_dmfr_file(feeds)


if __name__ == "__main__":
    main()
