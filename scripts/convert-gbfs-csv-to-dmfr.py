#!/usr/bin/env -S uv run


import csv
import json
import os
import re
import requests
from collections import OrderedDict

# Onestop IDs are immutable, but this file is regenerated wholesale on every
# run and the id is derived from the system's name. Without an anchor, an
# upstream rename in systems.csv silently renames the feed. The auto-discovery
# URL is that anchor: a system keeps whatever id it was first published with.
HERE = os.path.dirname(os.path.abspath(__file__))
COMMITTED = os.path.join(os.path.dirname(HERE), "feeds", "nabsa.github.com.dmfr.json")


def existing_ids_by_url(path):
    try:
        with open(path, encoding="utf-8") as f:
            committed = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for feed in committed.get("feeds", []):
        url = (feed.get("urls") or {}).get("gbfs_auto_discovery")
        if url and feed.get("id"):
            out[url.strip()] = feed["id"]
    return out


def mint_id(name, location):
    """A Onestop ID name component: alphanumerics from any script, '~' between.

    The scheme allows no other punctuation. `\\W` was used here before, which
    keeps underscores because Python counts them as word characters, so
    system names like "VAG_Rad" produced `f-vag_rad~nuremberg~gbfs`.
    """
    text = f"{name} {location}".lower()
    text = "".join(c if c.isalnum() else " " for c in text)
    words = [w for w in text.split() if w]
    return "f-" + "~".join(OrderedDict.fromkeys(words)) + "~gbfs"


r = requests.get("https://raw.githubusercontent.com/mobilitydata/gbfs/master/systems.csv")
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
published = existing_ids_by_url(COMMITTED)
feeds = []
seen_ids = set()
for row in list(cr):
    url = row["Auto-Discovery URL"].strip()
    onestop_id = published.get(url) or mint_id(row["Name"], row["Location"])
    if onestop_id in seen_ids:
        # if Onestop ID will collide, we'll just skip this feed for now
        # because of https://github.com/NABSA/gbfs/pull/373
        continue
    seen_ids.add(onestop_id)
    feed = {
        "spec": "gbfs",
        "id": onestop_id,
        "urls": {"gbfs_auto_discovery": url}
    }
    feeds.append(feed)
dmfr = {
    "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.6.0.json",
    "feeds": feeds,
    "license_spdx_identifier": "CDLA-Permissive-1.0",
}
print(json.dumps(dmfr, indent=2, sort_keys=True, ensure_ascii=False))
