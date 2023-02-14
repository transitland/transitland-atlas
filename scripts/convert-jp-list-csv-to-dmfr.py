# turn https://github.com/tshimada291/gtfs-jp-list-datecheck into a DMFR
# license on the GitHub repo is CC0-1.0: https://github.com/tshimada291/gtfs-jp-list-datecheck/blob/main/LICENSE

import csv
import json
import requests
import re

# http POST https://transit.land/api/v2/query apikey:Zy8N3gC2ZHMm5A5Yog3V1oe21xhKMa4Q query="{agencies(where: {adm0_iso: \"JP\"}) {feed_version {feed {urls {static_current}}}}}" | jq '.data.agencies[] .feed_version.feed.urls.static_current' | uniq
existing_feeds_to_skip = [
    "http://codeforkobe.github.io/kobe-transit/kobe_subway_gtfs.zip",
    "http://www.city.nomi.ishikawa.jp/data/open/cnt/3/5145/1/GTFSnomi2018.zip",
    "https://www.city.aomori.aomori.jp/toshi-seisaku/opendata/documents/siminbus20190401.zip",
    "http://opentrans.it/feed/gtfs/5649391675244544/google_transit.zip",
    "http://opentrans.it/feed/gtfs/5634472569470976/google_transit.zip",
    "http://opentrans.it/feed/gtfs/5707702298738688/google_transit.zip",
    "http://opentrans.it/feed/gtfs/5724160613416960/gtfs.zip",
    "http://opentrans.it/feed/gtfs/5697423099822080/gtfs.zip",
    "http://opentrans.it/feed/gtfs/5714163003293696/google_transit.zip",
    "http://opendata.busmaps.jp/yamanashi.zip",
    "http://www.city.fukuoka.lg.jp/data/open/cnt/3/59675/1/fukuokasiei_tosen_GTFSfeeds.zip",
    "https://www.city.aomori.aomori.jp/kotsu-kanri/koutsu/oshirase/documents/gtfs-aomoricitybus.zip",
    "http://www3.unobus.co.jp/opendata/GTFS-JP.zip",
]

r = requests.get(
    "https://raw.githubusercontent.com/tshimada291/gtfs-jp-list-datecheck/main/GTFS_fixedURL.csv"
)
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
feeds = []
for row in list(cr):
    if row["fixed_current_url"] in existing_feeds_to_skip:
        continue
    if row["fixed_current_basename"]:
        name = (
            row["fixed_current_basename"]
            .lower()
            .replace("_", "~")
            .replace("-", "~")
            .replace(".", "~")
        )
    elif row["fixed_current_url"].startswith("https://toyama-pref.box.com"):
        filename = re.search("static/(.+).zip$", row["fixed_current_url"])[1]
        name = f"toyama~pref~{filename}"
    else:
        raise Exception("Cannot determine name for a feed")
    feed = {
        "spec": "gtfs",
        "id": f"f-{name}~jp",
        "urls": {"static_current": row["fixed_current_url"]},
    }
    if row["license_name"] == "CC BY 4.0":
        feed["license"] = {
            "spdx_identifier": "CC-BY-4.0",
            "use_without_attribution": "no",
        }
    elif row["license_name"] == "CC 0":
        feed["license"] = {
            "spdx_identifier": "CC0-1.0",
            "use_without_attribution": "yes",
        }
    elif row["license_name"] == "CC BY 2.1":
        feed["license"] = {
            "spdx_identifier": "CC-BY-SA-2.1-JP",
            "use_without_attribution": "no",
        }
    feeds.append(feed)
dmfr = {
    "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.4.1.json",
    "feeds": feeds,
    "license_spdx_identifier": "CDLA-Permissive-1.0",
}
print(json.dumps(dmfr, indent=2, sort_keys=True))

# don't forget to format:
# gfind ./feeds -type f -name "*.dmfr.json" -exec transitland dmfr format --save {} \;
