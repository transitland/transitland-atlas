import csv
import json
import requests

r = requests.get(
    "https://github.com/openmobilityfoundation/mobility-data-specification/raw/main/providers.csv"
)
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
feeds = []
for row in list(cr):
    name = row["provider_name"].lower().replace(" ", "~")
    feed = {
        "spec": "mds",
        "id": f"f-{name}~mds",
        "urls": {"mds_provider": row["mds_api_url"]},
    }
    if row["gbfs_api_url"]:
        feed["urls"]["gbfs_auto_discovery"] = row["gbfs_api_url"]
    feeds.append(feed)
dmfr = {
    "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.4.1.json",
    "feeds": feeds,
    "license_spdx_identifier": "CDLA-Permissive-1.0",
}
print(json.dumps(dmfr, indent=2, sort_keys=True))