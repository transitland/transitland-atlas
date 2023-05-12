# turn https://github.com/tshimada291/gtfs-jp-list-datecheck into a DMFR
# license on the GitHub repo is CC0-1.0: https://github.com/tshimada291/gtfs-jp-list-datecheck/blob/main/LICENSE

import csv
import json
import requests
import re

# http POST https://transit.land/api/v2/query apikey:XXXX query="{agencies(where: {adm0_iso: \"JP\"}) {feed_version {feed {urls {static_current}}}}}" | jq '.data.agencies[] .feed_version.feed.urls.static_current' | uniq
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

existing_names_to_skip = []

r = requests.get(
    "https://raw.githubusercontent.com/tshimada291/gtfs-jp-list-datecheck/main/GTFS_fixedURL.csv"
)
decoded_content = r.content.decode("utf-8")
cr = csv.DictReader(decoded_content.splitlines(), delimiter=",")
feeds = []
for row in list(cr):
    # skip some feeds
    if row["fixed_current_url"] in existing_feeds_to_skip:
        continue
    if row["fixed_current_url"].startswith("https://openmobilitydata.org"):
        continue

    # name for Onestop ID
    if row["fixed_current_basename"] in ["GTFS-JP", "GTFS"]:
        name = row["feed_id"]
    elif row["fixed_current_basename"]:
        name = (
            row["fixed_current_basename"]
            .lower()
            .replace("_", "~")
            .replace("-", "~")
            .replace(".", "~")
        )
    else:
        name = row["feed_id"]

    # the source CSV has some rows with fixed_current_basename
    # we will only take the first feed URL for each
    if name in existing_names_to_skip:
        continue
    else:
        existing_names_to_skip.append(name)

    # prepare feed record
    feed = {
        "spec": "gtfs",
        "id": f"f-{name}~jp",
        "urls": {"static_current": row["fixed_current_url"]},
    }

    # license
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
