#!/usr/bin/env -S uv run


import csv
import json
import requests
from collections import OrderedDict


def mint_id(name, location):
    """A Onestop ID name component: alphanumerics from any script, '~' between.

    The scheme allows no other punctuation. `\\W` was used here before, which
    keeps underscores because Python counts them as word characters, so
    system names like "VAG_Rad" produced `f-vag_rad~nuremberg~gbfs`. Dropping
    empty words also removes the `~~` that a trailing "(DE)" or "Mark," left
    behind.

    These ids are derived from the system's name on every run and are not
    maintained across upstream renames, so no supersedes record is kept.
    """
    text = f"{name} {location}".lower()
    text = "".join(c if c.isalnum() else " " for c in text)
    words = [w for w in text.split() if w]
    return "f-" + "~".join(OrderedDict.fromkeys(words)) + "~gbfs"


r = requests.get("https://raw.githubusercontent.com/mobilitydata/gbfs/master/systems.csv")
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
feeds = []
for row in list(cr):
    onestop_id = mint_id(row["Name"], row["Location"])
    if onestop_id in [f["id"] for f in feeds]:
        # if Onestop ID will collide, we'll just skip this feed for now
        # because of https://github.com/NABSA/gbfs/pull/373
        continue
    url = row["Auto-Discovery URL"].strip()
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
