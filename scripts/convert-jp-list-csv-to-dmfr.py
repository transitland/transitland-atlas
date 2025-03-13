import csv
import requests
import json
import re
import subprocess
import logging

dmfr_url = 'https://github.com/transitland/transitland-atlas/raw/refs/heads/main/feeds/tshimada291.github.com.dmfr.json'
csv_url = 'https://github.com/tshimada291/gtfs-jp-list-datecheck/raw/refs/heads/main/GTFS_fixedURL_LastModified.csv'

existing_feed_urls_to_skip = [
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

# Collection to store created DMFR records
created_dmfr_records = []

def load_csv_from_url(csv_url):
    response = requests.get(csv_url)
    response.raise_for_status()  # Raise an error for bad responses
    lines = response.content.decode('utf-8').splitlines()
    reader = csv.DictReader(lines)
    data = [row for row in reader]
    return data


# Remove duplicate URLs from data
def remove_duplicate_urls(data):
    unique_urls = set()
    unique_data = []
    for row in data:
        url = row.get('url')
        if '?rid=next' in url:
            logging.warn(f"URL found with '?rid=next' and removed: {url}")
        elif '?date=next' in url:
            logging.warn(f"URL found with '?date=next' and removed: {url}")
        elif '/next/' in url:
            logging.warn(f"URL found with '/next/' and removed: {url}")
        elif '_next.zip' in url:
            logging.warn(f"URL found with '_next.zip' and removed: {url}")
        elif url in unique_urls:
            logging.warn(f"Duplicate URL found and removed: {url}")
        else:
            unique_urls.add(url)
            unique_data.append(row)
    return unique_data


def create_dmfr_record(feed_url, label, license_name, new_dmfr):
    label = label.strip().lower()
    label = re.sub(r'\(.*?\)|\[.*?\]', '', label)  # remove [gtfs-data], [HODaP], and (HODaP)
    label = re.sub(r'[()\[\]（）「」『』、。“”‘’]', '', label)
    label = re.sub(r'\s+|・|〜', '~', label)
    label = label.strip('~')
    onestop_id = f"f-{label}"

    # Ensure there are no duplicate IDs by appending a tilde and digit if necessary
    existing_ids = {feed['id'] for feed in new_dmfr.get('feeds', [])}
    counter = 1
    original_id = onestop_id
    while onestop_id in existing_ids:
        onestop_id = f"{original_id}~{counter}"
        counter += 1

    # Determine SPDX identifier and additional license properties based on license name
    spdx_identifier = None
    use_without_attribution = None
    if license_name == "CC BY 4.0":
        spdx_identifier = "CC-BY-4.0"
        use_without_attribution = "no"
    elif license_name == "CC BY 2.1":
        spdx_identifier = "CC-BY-SA-2.1-JP"
        use_without_attribution = "no"

    dmfr = {
        "id": onestop_id,
        "spec": "gtfs",
        "urls": {
            "static_current": feed_url
        },
        "tags": {
            "notes": "This feed record is managed in https://github.com/tshimada291/gtfs-jp-list-datecheck/blob/main/GTFS_fixedURL_LastModified.csv"
        },
        "license": {
            "spdx_identifier": spdx_identifier,
            "use_without_attribution": use_without_attribution
        } if spdx_identifier else None
    }
    new_dmfr.setdefault("feeds", []).append(dmfr)


# Load existing DMFR from URL
def load_existing_dmfr(dmfr_url):
    response = requests.get(dmfr_url)
    response.raise_for_status()  # Raise an error for bad responses
    dmfr_data = response.json()
    return dmfr_data


# Check if a feed URL exists in the existing DMFR records
def check_for_feed_in_existing_dmfr(feed_url, existing_dmfr):
    for feed in existing_dmfr.get("feeds", []):
        if feed.get("urls", {}).get("static_current") == feed_url:
            logging.info(f"Feed URL found in existing DMFR: {feed_url}")
            return True, feed
    logging.info(f"Feed URL not found in existing DMFR: {feed_url}")
    return False, None


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Load data from external CSV
    data = load_csv_from_url(csv_url)

    # Remove duplicate URLs
    data = remove_duplicate_urls(data)

    # Load existing DMFR
    existing_dmfr = load_existing_dmfr(dmfr_url)

    new_dmfr = {
        "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.5.1.json",
        "feeds": [],
        "license_spdx_identifier": "CDLA-Permissive-1.0"
    }

    # Iterate through all rows to check for existing TLv2 feed and create DMFR record if needed
    if data:
        for row in data:
            feed_url = row.get('url')
            label = row.get('label', '')
            license_name = row.get('license_name', '')
            if feed_url in existing_feed_urls_to_skip:
                logging.info("Skipping a feed URL that is managed in a different DMFR file")
                continue
            if feed_url:
                result, feed = check_for_feed_in_existing_dmfr(feed_url, existing_dmfr)
                if result and feed:
                    new_dmfr.setdefault("feeds", []).append(feed)
                else:
                    create_dmfr_record(feed_url, label, license_name, new_dmfr)

    output_path = '../feeds/tshimada291.github.com.dmfr.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(new_dmfr, f, indent=2)
    logging.info(f"All Created DMFR Records have been written to {output_path}")

    # Run transitland command to format and save the DMFR file
    subprocess.run(["transitland", "dmfr", "format", "--save", output_path], check=True)

    # Log summary of processing
    logging.info(f"Total unique URLs loaded from CSV: {len(data)}")
    logging.info(f"Total feed records loaded from existing DMFR: {len(existing_dmfr.get('feeds', []))}")
    logging.info(f"Total feed records outputted: {len(new_dmfr.get('feeds', []))}")
