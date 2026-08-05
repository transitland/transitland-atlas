#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# ///

"""Emit crosswalks between Onestop IDs and the external registries Atlas tags.

Transitland's own crosswalks are built from the API and served on the website,
e.g. transit.land/feeds/us-ntd-crosswalk. This is the local counterpart: it
reads feeds/ directly, so it works offline, on a branch, before anything is
synced, which is what makes it useful for debugging a crosswalk you are in the
middle of editing.

Every crosswalk here is the same shape, because every one of them is a tag:
some external id recorded on an operator or a feed. Adding a registry is one
row in REGISTRIES, not new code.

Multi-valued tags are split. An operator that absorbed several NTD reporters
carries them comma-separated, and each becomes its own row -- a crosswalk whose
key holds two ids joined by a comma cannot be joined against anything.

Two kinds of collision are reported rather than hidden, because both are real
and neither is visible from a single file:

  many-onestop   one external id on several Onestop IDs. Sometimes correct, as
                 with a reporter covering services we hold separately, and
                 sometimes an id copied onto the wrong operator.
  many-external  one Onestop ID carrying several ids from the same registry.
                 Normal after a merge, worth a look otherwise.

Usage:
  cd scripts && uv run export-crosswalk.py
  cd scripts && uv run export-crosswalk.py --registry us-ntd
  cd scripts && uv run export-crosswalk.py --registry us-ntd --csv > ntd.csv
  cd scripts && uv run export-crosswalk.py --out ../crosswalks
"""

import argparse
import collections
import csv
import difflib
import json
import os
import sys

import atlas_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# name -> where the id lives. `entity` is what the id identifies, which decides
# whether the tag is read from operators or from feeds. Order is display order.
REGISTRIES = [
    {"name": "us-ntd", "tag": "us_ntd_id", "entity": "operator",
     "about": "US National Transit Database reporter id"},
    {"name": "calitp-organization", "tag": "calitp_organization_id", "entity": "operator",
     "about": "California GTFS ingest pipeline organization record"},
    {"name": "calitp-dataset", "tag": "calitp_dataset_id", "entity": "feed",
     "about": "California GTFS ingest pipeline dataset record"},
    {"name": "wikidata", "tag": "wikidata_id", "entity": "operator",
     "about": "Wikidata entity"},
    {"name": "mobility-database", "tag": "mdb_source_id", "entity": "feed",
     "about": "Mobility Database source id"},
    {"name": "gtfs-data-exchange", "tag": "gtfs_data_exchange", "entity": "feed",
     "about": "GTFS Data Exchange slug, from the retired archive"},
    {"name": "odpt-dataset", "tag": "odpt_dataset_id", "entity": "feed",
     "about": "Japan Public Transportation Open Data Centre dataset"},
    {"name": "odpt-organization", "tag": "odpt_organization_id", "entity": "feed",
     "about": "Japan Public Transportation Open Data Centre organization"},
    {"name": "gtfs-data-jp", "tag": "gtfs_data_jp_organization_id", "entity": "operator",
     "about": "GTFS Data Repository Japan organization"},
    {"name": "es-nap", "tag": "es_nap_fichero_id", "entity": "feed",
     "about": "Spanish national access point file id"},
    {"name": "omd", "tag": "omd_provider_id", "entity": "operator",
     "about": "OpenMobilityData provider id"},
]
BY_NAME = {r["name"]: r for r in REGISTRIES}


def _tags(db, entity: str):
    """(onestop_id, name, tags dict) for every operator or feed carrying tags."""
    if entity == "operator":
        sql = ("SELECT onestop_id, name, operator_tags AS tags FROM current_operators "
               "WHERE operator_tags IS NOT NULL")
    else:
        sql = ("SELECT onestop_id, name, feed_tags AS tags FROM current_feeds "
               "WHERE feed_tags IS NOT NULL")
    for row in db.execute(sql):
        try:
            tags = json.loads(row["tags"])
        except (TypeError, ValueError):
            continue
        if isinstance(tags, dict):
            yield row["onestop_id"], (row["name"] or ""), tags


def rows_for(db, registry: dict) -> list[dict]:
    """One row per (external id, Onestop ID) pair, sorted by external id."""
    out = []
    operator_names = {r["onestop_id"]: (r["name"] or "")
                      for r in db.execute("SELECT onestop_id, name FROM current_operators")}
    for onestop_id, name, tags in _tags(db, registry["entity"]):
        raw = tags.get(registry["tag"])
        if not raw:
            continue
        for part in str(raw).split(","):
            ext = part.strip()
            if not ext:
                continue
            if registry["tag"] == "us_ntd_id":
                ext = atlas_registry.normalise_ntd_id(ext)
            if registry["entity"] == "operator":
                related = sorted(atlas_registry.operator_feeds(db, onestop_id))
                label = name
            else:
                related = sorted(atlas_registry.operators_of(db, onestop_id))
                # Feeds usually carry no name of their own, so a feed-keyed
                # crosswalk with a blank name column is unreadable. Borrow the
                # operator's, which is what a person is looking for anyway.
                label = name or operator_names.get(related[0], "") if related else name
            out.append({
                "external_id": ext,
                "onestop_id": onestop_id,
                "entity": registry["entity"],
                "name": label,
                "related": " ".join(related),
            })
    return sorted(out, key=lambda r: (r["external_id"], r["onestop_id"]))


def collisions(rows: list[dict]) -> tuple[dict, dict]:
    by_ext = collections.defaultdict(set)
    by_ost = collections.defaultdict(set)
    for r in rows:
        by_ext[r["external_id"]].add(r["onestop_id"])
        by_ost[r["onestop_id"]].add(r["external_id"])
    return ({k: sorted(v) for k, v in by_ext.items() if len(v) > 1},
            {k: sorted(v) for k, v in by_ost.items() if len(v) > 1})


def write_csv(rows: list[dict], fh) -> None:
    w = csv.DictWriter(fh, fieldnames=["external_id", "onestop_id", "entity", "name", "related"],
                       lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


def unknown_tags(db) -> list[tuple[str, str, str]]:
    """Tags that look like a misspelling of one we crosswalk.

    Cheap to check and it has already earned its place: `mbd_source_id` sits in
    feeds/ next to the 18 correct `mdb_source_id`, and nothing else would notice.
    """
    known = {r["tag"] for r in REGISTRIES}
    seen = set()
    for entity in ("operator", "feed"):
        for _, _, tags in _tags(db, entity):
            seen |= {(entity, k) for k in tags}
    out = []
    for entity, tag in sorted(seen):
        if tag in known:
            continue
        for near in difflib.get_close_matches(tag, known, n=1, cutoff=0.85):
            out.append((entity, tag, near))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", action="append", choices=list(BY_NAME) + ["all"],
                    help="repeatable; defaults to all")
    ap.add_argument("--csv", action="store_true",
                    help="write CSV to stdout; requires exactly one --registry")
    ap.add_argument("--out", help="directory to write one CSV per registry into")
    ap.add_argument("--feeds", default=os.path.join(ROOT, "feeds"))
    args = ap.parse_args()

    wanted = ([BY_NAME[n] for n in args.registry] if args.registry and "all" not in args.registry
              else REGISTRIES)
    if args.csv and len(wanted) != 1:
        print("ERROR: --csv needs exactly one --registry", file=sys.stderr)
        return 2

    db = atlas_registry.load(args.feeds)

    if args.csv:
        write_csv(rows_for(db, wanted[0]), sys.stdout)
        return 0

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    print(f"{'registry':22s} {'entity':9s} {'pairs':>6s} {'ids':>6s} {'onestop':>8s}  collisions")
    for registry in wanted:
        rows = rows_for(db, registry)
        many_onestop, many_external = collisions(rows)
        note = ""
        if many_onestop:
            note += f"{len(many_onestop)} id(s) on several Onestop IDs  "
        if many_external:
            note += f"{len(many_external)} Onestop ID(s) with several ids"
        print(f"{registry['name']:22s} {registry['entity']:9s} {len(rows):6d} "
              f"{len({r['external_id'] for r in rows}):6d} "
              f"{len({r['onestop_id'] for r in rows}):8d}  {note}")
        if args.out and rows:
            path = os.path.join(args.out, f"{registry['name']}.csv")
            with open(path, "w") as fh:
                write_csv(rows, fh)

    if len(wanted) == 1:
        many_onestop, many_external = collisions(rows_for(db, wanted[0]))
        for label, coll in (("one id on several Onestop IDs", many_onestop),
                            ("one Onestop ID with several ids", many_external)):
            if coll:
                print(f"\n  {label} ({len(coll)}):")
                for k, v in sorted(coll.items())[:20]:
                    print(f"      {k:34s} {', '.join(v)}")

    odd = unknown_tags(db)
    if odd:
        print(f"\n{len(odd)} tag(s) that look like a misspelling of one we crosswalk:")
        for entity, tag, near in odd:
            print(f"      {entity} tag {tag!r} -- did you mean {near!r}?")

    if args.out:
        print(f"\nwrote {len(wanted)} file(s) to {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
