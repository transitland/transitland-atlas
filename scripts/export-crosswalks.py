#!/usr/bin/env -S uv run

"""
Export each ID crosswalk to a CSV under crosswalks/.

A crosswalk is a tag: `us_ntd_id` on an operator, `mdb_source_id` on a feed. The
tags are in the DMFR files here, so the export reads them from the registry
rather than from an API, and cannot disagree with them.

One row per (external id, Onestop ID) pair:

    external_id,onestop_id,entity,name,related

`related` holds the Onestop IDs on the other side of the association: a tagged
operator's feeds, or a tagged feed's operators. Several are joined with a
semicolon, the same separator split_ids already accepts in a tag, and one that
needs no CSV quoting because no Onestop ID contains it.

Rows are sorted, so a commit diff shows what changed rather than how the
registry happened to be walked.

The Wikidata export carries a trailing `wikipedia_url` from each entity's
English sitelink, which needs the network. A failed lookup exits non-zero
rather than writing blanks, which would be indistinguishable from an entity
with no article.

A Frictionless Data Package v2 descriptor is written alongside them at
crosswalks/datapackage.json, so the columns, their types and the primary key
are machine readable rather than only described in prose.

Requires the `transitland` binary on PATH, same as validate-feeds.py.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_registry  # noqa: E402

# Resolved from this file rather than the working directory, so the script runs
# from anywhere. validate-feeds.py carries the same note after being caught by
# the relative form.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A contract with shared/crosswalks.ts in www-transit-land-v2, which links
# crosswalks/<name>.csv for every registry it knows: a name that differs there
# is a broken download here. `entity` picks operators or feeds. `secondary` is a
# second tag in a trailing column, for a registry whose identifier is not one
# value (ODPT names a feed by operator and dataset).
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

# Joins several ids inside one cell. A comma would need the row quoted and can
# break a naive reader that splits on the delimiter; a semicolon needs neither,
# and split_ids already accepts it wherever a tag carries more than one id.
SEPARATOR = ";"

# wbgetentities takes at most 50 ids per call.
WIKIDATA_BATCH = 50

# Not str.isdigit(), which accepts non-ASCII digits and superscripts. One of
# those reaching the API is rejected as no-such-entity, which fails the batch.
QID_RE = re.compile(r"Q[1-9][0-9]*")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "transitland-atlas crosswalk export (https://github.com/transitland/transitland-atlas)"


def rows_for(db, registry):
    """The crosswalk's rows, sorted, one per (external id, Onestop ID)."""
    names = atlas_registry.operator_names(db) if registry["entity"] == "feed" else {}
    out = []
    for row, tags in atlas_registry.tagged_records(db, registry["entity"], registry["tag"]):
        onestop_id = row["onestop_id"]
        if registry["entity"] == "operator":
            related = atlas_registry.operator_feeds(db, onestop_id)
            name = row["name"] or ""
        else:
            related = atlas_registry.operators_of(db, onestop_id)
            # A feed rarely names itself, so borrow an associated operator's.
            name = row["name"] or next(
                (names[o] for o in sorted(related) if names.get(o)), "")
        secondary = ""
        if registry.get("secondary"):
            secondary = SEPARATOR.join(atlas_registry.split_ids(tags.get(registry["secondary"])))
        external_ids = atlas_registry.split_ids(tags[registry["tag"]])
        if not external_ids:
            print(f"warning: {onestop_id} has {registry['tag']} = "
                  f"{tags[registry['tag']]!r}, which holds no id", file=sys.stderr)
        for external_id in external_ids:
            if registry["tag"] == "us_ntd_id":
                external_id = atlas_registry.normalize_ntd_id(external_id) or external_id
            record = {
                "external_id": external_id,
                "onestop_id": onestop_id,
                "entity": registry["entity"],
                "name": name,
                "related": SEPARATOR.join(sorted(related)),
            }
            if registry.get("secondary"):
                record[registry["secondary"]] = secondary
            out.append(record)
    out.sort(key=lambda r: (r["external_id"], r["onestop_id"]))
    # Normalization can map two written forms onto one id, and the descriptor
    # declares (external_id, onestop_id) a primary key, so collapse the pair
    # rather than publish a file that contradicts its own schema.
    seen = set()
    unique_rows = []
    for row in out:
        key = (row["external_id"], row["onestop_id"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)
    return unique_rows


def wikipedia_urls(qids: list[str]) -> dict[str, str]:
    """English Wikipedia article URL per Q-item, from its enwiki sitelink.

    Entities with no English article are absent, and a missing key writes an
    empty cell. A failed request raises, so an outage does not silently empty
    the column.
    """
    out: dict[str, str] = {}
    unique = sorted({q for q in qids if QID_RE.fullmatch(q)})
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
        # wbgetentities answers 200 with a top-level `error` and no `entities`
        # at all when any one id in the batch is unresolvable, so urlopen does
        # not raise and a bare .get("entities") would drop fifty articles
        # without a word. One deleted Q-item would otherwise empty a batch and
        # commit the result.
        if "error" in data:
            raise RuntimeError(f"wikidata rejected a batch of {len(batch)}: "
                               f"{data['error'].get('info', data['error'])}")
        entities = data.get("entities")
        if entities is None:
            raise RuntimeError(f"wikidata returned no entities for a batch of {len(batch)}")
        missing = set(batch) - set(entities)
        if missing:
            raise RuntimeError(f"wikidata did not answer for {sorted(missing)[:5]}")
        for qid, entity in entities.items():
            title = ((entity or {}).get("sitelinks") or {}).get("enwiki", {}).get("title")
            if title:
                path = urllib.parse.quote(title.replace(" ", "_"), safe="/:,")
                out[qid] = f"https://en.wikipedia.org/wiki/{path}"
        if i + WIKIDATA_BATCH < len(unique):
            time.sleep(0.2)
    return out


# Frictionless Data Package v2. `profile` is the v1 property and is not used.
DATAPACKAGE_PROFILE = "https://datapackage.org/profiles/2.0/datapackage.json"

FIELD_DESCRIPTIONS = {
    "external_id": "The identifier this record carries in the external system.",
    "onestop_id": "The Transitland operator or feed the identifier belongs to.",
    "entity": "Which kind of Transitland record carries the tag.",
    "name": "Operator name, for readability. Not an identifier.",
    "related": ("Onestop IDs on the other side of the association, semicolon "
                "separated: a tagged operator's feeds, or a tagged feed's operators."),
    "wikipedia_url": "English Wikipedia article for the Wikidata entity, where it has one.",
}


def _field(name: str) -> dict:
    """One Table Schema field. Every column is a string; none is arithmetic."""
    field = {
        "name": name,
        "type": "string",
        "description": FIELD_DESCRIPTIONS.get(
            name, f"Value of the `{name}` tag on the same record."),
    }
    if name == "entity":
        field["constraints"] = {"enum": ["operator", "feed"]}
    if name == "wikipedia_url":
        field["format"] = "uri"
    return field


def columns_for(registry: dict) -> list[str]:
    """The CSV columns this registry produces, in order.

    Read by both the writer and the descriptor, so the file and the schema
    describing it cannot list different columns.
    """
    columns = list(COLUMNS)
    if registry.get("secondary"):
        columns.append(registry["secondary"])
    if registry.get("wikipedia"):
        columns.append("wikipedia_url")
    return columns


def datapackage(registries: list[dict]) -> dict:
    """The descriptor for crosswalks/, as a Data Package v2 dict.

    Deliberately carries no timestamp. The file is committed, and auto-pr.sh
    opens a PR only when something changed, so a `created` that moves every run
    would mean a daily pull request saying nothing.
    """
    resources = []
    for registry in registries:
        entity = registry["entity"]
        resources.append({
            "name": registry["name"],
            "path": f"{registry['name']}.csv",
            "format": "csv",
            "mediatype": "text/csv",
            "encoding": "utf-8",
            "description": (f"Transitland Onestop IDs crosswalked to `{registry['tag']}` "
                            f"values tagged on {entity} records."),
            "schema": {
                "fields": [_field(c) for c in columns_for(registry)],
                "primaryKey": ["external_id", "onestop_id"],
            },
        })
    return {
        "$schema": DATAPACKAGE_PROFILE,
        "name": "transitland-id-crosswalks",
        "id": "https://github.com/transitland/transitland-atlas",
        "title": "Transitland ID crosswalks",
        "description": ("Crosswalks between Transitland Onestop IDs and the identifiers the "
                        "same operators and feeds carry in other transit data systems. Built "
                        "from the tags on the DMFR records in this repository."),
        "homepage": "https://www.transit.land/data/id-crosswalks",
        "licenses": [{
            "name": "CC-BY-4.0",
            "path": "https://creativecommons.org/licenses/by/4.0/",
            "title": "Creative Commons Attribution 4.0",
        }],
        "resources": resources,
    }


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        # Without lineterminator the csv module writes CRLF, which would put
        # 3,800 lines of \r into an LF repository and leave a stray \r on the
        # last field of every row for anything not using a CSV reader.
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds-dir", default=os.path.join(REPO_ROOT, "feeds"))
    parser.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "crosswalks"))
    args = parser.parse_args()

    db = atlas_registry.load(args.feeds_dir)
    os.makedirs(args.out_dir, exist_ok=True)

    for registry in REGISTRIES:
        rows = rows_for(db, registry)
        if registry.get("wikipedia"):
            articles = wikipedia_urls([r["external_id"] for r in rows])
            for row in rows:
                row["wikipedia_url"] = articles.get(row["external_id"], "")
        path = os.path.join(args.out_dir, f"{registry['name']}.csv")
        write_csv(path, rows, columns_for(registry))
        print(f"{path}: {len(rows)} rows")

    descriptor = os.path.join(args.out_dir, "datapackage.json")
    with open(descriptor, "w", encoding="utf-8") as fh:
        json.dump(datapackage(REGISTRIES), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"{descriptor}: {len(REGISTRIES)} resources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
