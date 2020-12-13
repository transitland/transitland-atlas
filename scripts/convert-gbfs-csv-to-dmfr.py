import csv
import json
import re
import requests

r = requests.get("https://github.com/NABSA/gbfs/raw/master/systems.csv")
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
feeds = []
for row in list(cr):
    name = row["Name"].lower().replace(" ", "~").replace("-", "~")
    name = re.sub(r"~{2,}", "~", name)
    feed = {
        "spec": "gbfs",
        "id": f"f-{name}~gbfs",
        "urls": {"gbfs_auto_discovery": row["Auto-Discovery URL"]},
    }
    feeds.append(feed)
dmfr = {
    "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.3.0.json",
    "feeds": feeds,
    "license_spdx_identifier": "CDLA-Permissive-1.0",
}
print(json.dumps(dmfr, indent=2, sort_keys=True, ensure_ascii=False))