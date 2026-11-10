#!/usr/bin/env -S uv run


import csv
import requests
import json
import re
import subprocess
import logging

dmfr_url = 'https://github.com/transitland/transitland-atlas/raw/refs/heads/main/feeds/tshimada291.github.com.dmfr.json'
# URLs for other Japanese DMFR files to check for duplicate URLs
other_jp_dmfr_urls = [
    'https://github.com/transitland/transitland-atlas/raw/refs/heads/main/feeds/gtfs-data-jp.dmfr.json',
    'https://github.com/transitland/transitland-atlas/raw/refs/heads/main/feeds/odpt-gtfs.dmfr.json',
    'https://github.com/transitland/transitland-atlas/raw/refs/heads/main/feeds/odpt-gtfs-manual-additions.dmfr.json',
]
csv_url = 'https://github.com/tshimada291/gtfs-jp-list-datecheck/raw/refs/heads/main/GTFS_fixedURL_LastModified.csv'

# Feeds at these URLs are managed by collect-odpt-gtfs.py and
# collect-gtfs-data-jp.py, from each source's own listing. Other paths on the
# same hosts (e.g. ODPT's /files/Toei/) are not, and stay here.
SOURCE_COLLECTOR_URL = re.compile(
    r'^https?://(api|api-public)\.odpt\.org/api/v4/files/odpt/'
    r'|^https?://api\.gtfs-data\.jp/v2/organizations/', re.IGNORECASE)

existing_feed_urls_to_skip = [
    "http://opendata.busmaps.jp/yamanashi.zip",
    "https://www.city.aomori.aomori.jp/kotsu-kanri/koutsu/oshirase/documents/gtfs-aomoricitybus.zip",
]

# Feeds at these URLs are superseded by another registered feed for the same
# agency, usually a gtfs-data.jp record, and no longer fetch or serve a current
# calendar. The upstream list still carries them.
superseded_feed_urls = {
    "http://opendata.sagabus.info/saga-2023-07-01.zip",
    "http://shimoden.net/busmada/opendata/GTFS-JP.zip",
    "http://www.city.tokai.aichi.jp/secure/47255/gtfs.zip",
    "http://www.sentetsu.biz-web.jp/GTFS-JP.zip",
    "https://ajt-mobusta-gtfs.mcapps.jp/static/14/current_data.zip",
    "https://ckan.hoda.jp/dataset/24d1dd70-5395-4d6b-b41f-0d83e8eabdb9/resource/c84abf64-f7ba-4d22-8cc1-acac7adbdc6f/download/abashiri_bus.zip",
    "https://ckan.hoda.jp/dataset/24d1dd70-5395-4d6b-b41f-0d83e8eabdb9/resource/e3bb6e27-d2bb-4925-a42c-adb5f49bb924/download/akan_bus.zip",
    "https://ckan.open-governmentdata.org/dataset/42e62bc8-cfe8-4f66-9e89-decffff56f23/resource/41dfa7d1-cb07-47b4-b509-5fcbe5c79765/download/401005_odekakekotsugtfs_tashirokawachi.zip",
    "https://ckan.open-governmentdata.org/dataset/468dc771-10a2-4bab-aad2-191b0fdaffa0/resource/bdbf46fd-870b-4064-9e7f-7bcedf97b526/download/401005_odekakekotsugtfs_edamitsu.zip",
    "https://ckan.open-governmentdata.org/dataset/950b03df-5142-4f34-b976-43dad98a9540/resource/91059af4-fc56-45e1-8ecd-fd4bcb5e2df1/download/401005_odekakekotsugtfs_okura.zip",
    "https://ckan.open-governmentdata.org/dataset/ccbc3e6b-d85f-4541-8d6f-2c40d065f836/resource/8d32347f-00a4-497c-b88e-51f413b4b49e/download/401005_odekakekotsugtfs_omadobaru.zip",
    "https://ckan.open-governmentdata.org/dataset/e3689c3c-d7e4-40a8-9d21-89aa95311bb8/resource/6185c93d-76cd-4287-a306-428216f7a285/download/401005_odekakekotsugtfs_koyanosekusubashihoshigaoka.zip",
    "https://data.bodik.jp/dataset/6d93f594-6b28-417b-a1df-931e93690a78/resource/6a0eaa15-185b-4156-8b98-9b680a65ad93/download/current_gtfs.zip",
    "https://hiroshima-opendata.dataeye.jp/resource_download/10045",
    "https://kan-etsu.net/relays/download/193/2807/433//?file=/files/libs/11049/202305261450561282.zip&file_name=GTFS-JP(kan-etsu)",
    "https://opendata.pref.saitama.lg.jp/resource_download/3194",
    "https://opendata.pref.saitama.lg.jp/resource_download/6357",
    "https://opendata.pref.shizuoka.jp/dataset/9718/resource/65790/sakuragi.ijiri.kurami.higashiyama.awamoto.shigaichijyunkan-minami.zip",
    "https://opendata.pref.shizuoka.jp/dataset/9718/resource/66109/sakuragi.ijiri.kurami.higashiyama.awamoto.shigaichijyunkan-minami202304.zip",
    "https://www.akita-bus.or.jp/~akita-gtfs/bus-akitachuoukotsu.zip",
    "https://www.akita-bus.or.jp/~akita-gtfs/bus-city-semboku.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/B01_Buzen_Nakatsu.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y010_Kawaraguchi.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y022_Kaminogouchi_Masaki_for_Kaminogouchi.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y031_Hiyamaji_Kamagi_for_Hiyamaji.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y032_Hiyamaji_Kamagi_for_Kamagi.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y110_Tsutami.zip",
    "https://www.city-nakatsu.jp/doc/2019072300060/file_contents/Y122_Urayabakei_for_Hiyamaji.zip",
    "https://www.city.ama.aichi.jp/_res/projects/default_project/_page_/001/002/512/ama_GTFS.zip",
    "https://www.city.chiryu.aichi.jp/ikkrwebBrowse/material/files/group/4/gtfs_data.zip",
    "https://www.city.hakodate.hokkaido.jp/docs/2020052700015/files/GTFS-JP.zip",
    "https://www.city.misawa.lg.jp/index.cfm/22%2C45535%2Cc%2Chtml/45535/GTFSJP3.zip",
    "https://www.city.nagakute.lg.jp/material/files/group/4/Nagakute_Nbus_gtfs_current_data.zip",
    "https://www.city.nonoichi.lg.jp/img/opendata/notty_current.zip",
    "https://www.city.takaoka.toyama.jp/joho/shise/opendata/documents/city_takaoka_gtfs.zip",
    "https://www.city.toyoake.lg.jp/secure/5217/toyoake.zip",
    "https://www.kotoden.co.jp/publichtm/gtfs/gtfsdata/latest/gtfs_kd.zip",
    "https://www.pref.fukui.lg.jp/doc/dx-suishin/opendata/gtfs_jp_d/fil/keifuku_bus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFS-Tosaden-Traffic_Airportbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Aki-Genkibus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Geisei-Village.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Hokubu-Traffic_Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Ino-InoLoop.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Konan-City.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Koryo-Traffic_Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Kuroiwa-Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Mihara-Miharabus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Motoyama-Town.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Muroto-City.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Nakatosa-Town.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Nankoku-City.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Pref-Ferry.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Reihoku-Kanko_Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Seinan-Expressbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Seinan-Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Seinan-Riverbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Seinan-Trolleybus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Shimanto-City.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Shimanto-Town.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Shimanto-Traffic_Feeder.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Shimanto-Traffic_Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Sukumo-Ferry.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Sukumo-Yururin-Bus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Susaki-Bus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Susaki-Ferry.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Tano-Town.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Tobu-Traffic_Localbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Tosa-City-Dragonbus.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Tosashimizu-Odekake.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Tsuno-Town.zip",
    "https://www.pref.kochi.lg.jp/opendata/docs/bosai_anzen_machizukuri/file_contents/GTFSJP-Yasuda-Town.zip",
    "https://www.pref.wakayama.lg.jp/prefg/062500/kotsu/opendata_d/fil/GTFS_kumano.zip",
    "https://www.pref.wakayama.lg.jp/prefg/062500/kotsu/opendata_d/fil/GTFS_meiko.zip",
    "https://www.pref.wakayama.lg.jp/prefg/062500/kotsu/opendata_d/fil/GTFS_rinkan.zip",
    "https://www.pref.wakayama.lg.jp/prefg/062500/kotsu/opendata_d/fil/GTFS_ryujin.zip",
    "https://www.pref.yamagata.jp/documents/20978/flower-liner_gtfs.zip",
    "https://www.pref.yamagata.jp/documents/20978/tozawa_gtfs.zip",
    "https://www.takubus.com/app/download/10941479479/GTFS_regular_line.zip",
    "https://www.takubus.com/app/download/11135470279/GTFS_highway_line.zip",
    "https://www.takubus.com/app/download/11236187979/GTFS_regular_line20230526.zip?t=1685093868",
    "https://www.takubus.com/app/download/11238066179/GTFS_highway_line20230601.zip?t=1685428757",
}

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
        elif SOURCE_COLLECTOR_URL.match(url):
            logging.info(f"URL managed by a source collector and removed: {url}")
        elif url in unique_urls:
            logging.warn(f"Duplicate URL found and removed: {url}")
        else:
            unique_urls.add(url)
            unique_data.append(row)
    return unique_data


def create_dmfr_record(feed_url, label, license_name, new_dmfr):
    label = label.strip().lower()
    # bracketed annotations, in ASCII and fullwidth forms: [gtfs-data], （HODaP）, ［HODaP］
    label = re.sub(r'\(.*?\)|\[.*?\]|（.*?）|［.*?］', '', label)
    # The name component of a Onestop ID allows alphanumerics from any script
    # and '~', and nothing else. Enumerating punctuation to strip missed the
    # fullwidth forms — ～ (U+FF5E) is a different character from 〜 (U+301C),
    # so ＊, ．and ［］ all reached published ids. Treat anything that is not
    # alphanumeric as a word break instead.
    label = ''.join(c if c.isalnum() else '~' for c in label)
    label = re.sub(r'~+', '~', label).strip('~')
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


# Check if a feed URL exists in any DMFR file (returns True if found, False otherwise)
def check_for_feed_url_in_any_dmfr(feed_url, *dmfr_files):
    """Check if a feed URL exists in any of the provided DMFR files.
    Returns True if the URL is found in any DMFR file, False otherwise."""
    for dmfr in dmfr_files:
        if dmfr and check_for_feed_in_existing_dmfr(feed_url, dmfr)[0]:
            return True
    return False


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Load data from external CSV
    data = load_csv_from_url(csv_url)

    # Remove duplicate URLs
    data = remove_duplicate_urls(data)

    # Load existing DMFR files
    existing_dmfr = load_existing_dmfr(dmfr_url)
    
    # Load all other Japanese DMFR files to check for duplicate URLs
    other_jp_dmfrs = []
    for url in other_jp_dmfr_urls:
        try:
            dmfr = load_existing_dmfr(url)
            other_jp_dmfrs.append(dmfr)
            logging.info(f"Loaded DMFR file for duplicate checking: {url}")
        except Exception as e:
            logging.warning(f"Failed to load DMFR file {url}: {e}")

    new_dmfr = {
        "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.6.0.json",
        "feeds": [],
        "license_spdx_identifier": "CDLA-Permissive-1.0"
    }

    # Two-pass processing to avoid id collisions between existing feeds (which
    # keep their committed onestop_ids) and newly-created feeds (which derive
    # their onestop_ids from labels and may collide with an existing one).
    #
    # Pass 1: import any existing feed whose URL is still present in the CSV
    # (and not skipped by the other filters). This reserves the existing
    # onestop_ids in new_dmfr.feeds.
    #
    # Pass 2: iterate the CSV and create new records for URLs that weren't
    # imported in pass 1. create_dmfr_record disambiguates against the now-
    # populated new_dmfr.feeds, so any label-derived collision becomes ~N.
    if data:
        csv_urls = {row.get('url') for row in data if row.get('url')}

        for feed in existing_dmfr.get("feeds", []):
            feed_url = feed.get("urls", {}).get("static_current")
            if not feed_url or feed_url not in csv_urls:
                continue
            if feed_url in existing_feed_urls_to_skip or feed_url in superseded_feed_urls:
                continue
            if check_for_feed_url_in_any_dmfr(feed_url, *other_jp_dmfrs):
                continue
            new_dmfr.setdefault("feeds", []).append(feed)

        imported_urls = {
            f.get("urls", {}).get("static_current")
            for f in new_dmfr.get("feeds", [])
        }

        for row in data:
            feed_url = row.get('url')
            label = row.get('label', '')
            license_name = row.get('license_name', '')
            if not feed_url:
                continue
            if feed_url in existing_feed_urls_to_skip:
                logging.info("Skipping a feed URL that is managed in a different DMFR file")
                continue
            if feed_url in superseded_feed_urls:
                continue
            if check_for_feed_url_in_any_dmfr(feed_url, *other_jp_dmfrs):
                logging.info(f"Skipping feed URL that exists in another Japanese DMFR file: {feed_url}")
                continue
            if feed_url in imported_urls:
                continue  # already imported from existing DMFR in pass 1
            create_dmfr_record(feed_url, label, license_name, new_dmfr)

    output_path = '../../feeds/tshimada291.github.com.dmfr.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(new_dmfr, f, indent=2)
    logging.info(f"All Created DMFR Records have been written to {output_path}")

    # Run transitland command to format and save the DMFR file
    subprocess.run(["transitland", "dmfr", "format", "--save", output_path], check=True)

    # Log summary of processing
    logging.info(f"Total unique URLs loaded from CSV: {len(data)}")
    logging.info(f"Total feed records loaded from existing DMFR: {len(existing_dmfr.get('feeds', []))}")
    logging.info(f"Total feed records outputted: {len(new_dmfr.get('feeds', []))}")
