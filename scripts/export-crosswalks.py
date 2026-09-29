#!/usr/bin/env -S uv run

"""
Export each ID crosswalk to a CSV under crosswalks/.

A crosswalk is a tag: `us_ntd_id` on an operator, `mdb_source_id` on a feed,
and so on. Those tags are already here, in the DMFR files, so the export reads
them from the registry rather than from an API -- this repository is the source
of what the crosswalk says, and a file built from it cannot disagree with it.

Output is one row per (external id, Onestop ID) pair:

    external_id,onestop_id,entity,name,related

`related` holds the Onestop IDs on the other side of the association -- a
tagged operator's feeds, or a tagged feed's operators -- space separated.

Rows are sorted, so a commit diff shows what changed about the crosswalk rather
than how the registry happened to be walked.

The Wikidata export carries a trailing `wikipedia_url` resolved from each
entity's English sitelink, which needs the network. A failed lookup exits
non-zero rather than writing a column of blanks, because a blank column is
indistinguishable from an entity that genuinely has no article.

Requires the `transitland` binary on PATH, same as validate-feeds.py.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_registry  # noqa: E402

# The crosswalks, and which tag each one publishes.
#
# This list is a contract with www-transit-land-v2's shared/crosswalks.ts: the
# website links to crosswalks/<name>.csv for every registry it knows, so a name
# here that differs from a name there is a broken download button. `entity`
# decides whether the tag is read from operators or feeds, and `secondary` is a
# second tag carried in a trailing column, for a registry whose identifier is
# not one value (ODPT names a feed by operator *and* dataset).
REGISTRIES = [
    {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator"},
    {"name": "wikidata", "tag": "wikidata_id", "entity": "operator", "wikipedia": True},
    {"name": "calitp-organization", "tag": "calitp_organization_id", "entity": "operator"},
    {"name": "calitp-dataset", "tag": "calitp_dataset_id", "entity": "feed"},
    {"name": "mobility-database", "tag": "mdb_source_id", "entity": "feed"},
    {"name": "gtfs-data-exchange", "tag": "gtfs_data_exchange", "entity": "feed"},
    {"name": "odpt", "tag": "odpt_organization_id", "entity": "feed",
     "secondary": "odpt_dataset_id"},
    {"name": "gtfs-data-jp", "tag": "gtfs_data_jp_organization_id", "entity": "operator"},
    {"name": "es-nap", "tag": "es_nap_fichero_id", "entity": "feed"},
    {"name": "omd", "tag": "omd_provider_id", "entity": "operator"},
]

COLUMNS = ["external_id", "onestop_id", "entity", "name", "related"]

# wbgetentities takes at most 50 ids per call.
WIKIDATA_BATCH = 50
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "transitland-atlas crosswalk export (https://github.com/transitland/transitland-atlas)"


def _tagged(db, registry):
    """Every record carrying this registry's tag, with what a row needs."""
    if registry["entity"] == "operator":
        sql = ("SELECT onestop_id, name, operator_tags AS tags "
               "FROM current_operators WHERE operator_tags IS NOT NULL")
    else:
        sql = ("SELECT onestop_id, name, feed_tags AS tags "
               "FROM current_feeds WHERE feed_tags IS NOT NULL")
    for row in db.execute(sql):
        try:
            tags = json.loads(row["tags"])
        except (TypeError, ValueError):
            continue
        if isinstance(tags, dict) and tags.get(registry["tag"]):
            yield row, tags


def rows_for(db, registry):
    """The crosswalk's rows, sorted, one per (external id, Onestop ID)."""
    out = []
    for row, tags in _tagged(db, registry):
        onestop_id = row["onestop_id"]
        if registry["entity"] == "operator":
            related = atlas_registry.operator_feeds(db, onestop_id)
            name = row["name"] or ""
        else:
            related = atlas_registry.operators_of(db, onestop_id)
            # A feed rarely names itself, so borrow an associated operator's:
            # a crosswalk with a blank name column is unreadable.
            name = row["name"] or _first_operator_name(db, related)
        secondary = ""
        if registry.get("secondary"):
            secondary = " ".join(atlas_registry.split_ids(tags.get(registry["secondary"])))
        for external_id in atlas_registry.split_ids(tags[registry["tag"]]):
            if registry["tag"] == "us_ntd_id":
                external_id = atlas_registry.normalize_ntd_id(external_id) or external_id
            record = {
                "external_id": external_id,
                "onestop_id": onestop_id,
                "entity": registry["entity"],
                "name": name,
                "related": " ".join(sorted(related)),
            }
            if registry.get("secondary"):
                record[registry["secondary"]] = secondary
            out.append(record)
    out.sort(key=lambda r: (r["external_id"], r["onestop_id"]))
    return out


def _first_operator_name(db, onestop_ids) -> str:
    for osid in sorted(onestop_ids):
        row = db.execute("SELECT name FROM current_operators WHERE onestop_id = ?",
                         (osid,)).fetchone()
        if row and row["name"]:
            return row["name"]
    return ""


def wikipedia_urls(qids: list[str]) -> dict[str, str]:
    """English Wikipedia article URL per Q-item, from its enwiki sitelink.

    Entities with no English article are simply absent, which is what the
    caller wants: a missing key writes an empty cell. A failed request raises,
    so a transient outage does not silently empty the whole column.
    """
    out: dict[str, str] = {}
    unique = sorted({q for q in qids if q.startswith("Q") and q[1:].isdigit()})
    for i in range(0, len(unique), WIKIDATA_BATCH):
        batch = unique[i:i + WIKIDATA_BATCH]
        params = urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "sitelinks", "sitefilter": "enwiki",
            "format": "json", "formatversion": "2",
        })
        req = urllib.request.Request(f"{WIKIDATA_API}?{params}",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        for qid, entity in (data.get("entities") or {}).items():
            title = ((entity or {}).get("sitelinks") or {}).get("enwiki", {}).get("title")
            if title:
                path = urllib.parse.quote(title.replace(" ", "_"), safe="/:,")
                out[qid] = f"https://en.wikipedia.org/wiki/{path}"
        if i + WIKIDATA_BATCH < len(unique):
            time.sleep(0.2)
    return out


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds-dir", default="feeds")
    parser.add_argument("--out-dir", default="crosswalks")
    parser.add_argument("--skip-wikipedia", action="store_true",
                        help="leave wikipedia_url empty; for offline runs")
    args = parser.parse_args()

    db = atlas_registry.load(args.feeds_dir)
    os.makedirs(args.out_dir, exist_ok=True)

    for registry in REGISTRIES:
        rows = rows_for(db, registry)
        columns = list(COLUMNS)
        if registry.get("secondary"):
            columns.append(registry["secondary"])
        if registry.get("wikipedia") and not args.skip_wikipedia:
            articles = wikipedia_urls([r["external_id"] for r in rows])
            for row in rows:
                row["wikipedia_url"] = articles.get(row["external_id"], "")
            columns.append("wikipedia_url")
        elif registry.get("wikipedia"):
            columns.append("wikipedia_url")
        path = os.path.join(args.out_dir, f"{registry['name']}.csv")
        write_csv(path, rows, columns)
        print(f"{path}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
