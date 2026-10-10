#!/usr/bin/env -S uv run


"""
Query the Spanish National Access Point (NAP) API and generate DMFR file.

This script will:
1. Query the NAP API to get all GTFS feeds
2. Transform the data into DMFR format
3. Update those feeds in feeds/gtfs-source-feeds.transit.land.dmfr.json

That file is shared with other feeds. This script only manages the feeds tagged
es_nap_fichero_id; every other feed and top-level field in it is left untouched.

See https://nap.transportes.gob.es/Account/InstruccionesAPI

Usage:
    export SPANISH_NAP_API_KEY=your-api-key
    uv run collect-nap-gtfs.py [--save-api-response]
"""

import copy
import os
import json
import logging
import requests
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import re

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Transitland Atlas repo paths
REPO_ROOT = Path(__file__).parent.parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"

# The shared DMFR file these feeds live in, and the tag that marks the ones
# this script manages. Feeds without the tag are never touched.
DMFR_FILE = FEEDS_DIR / "gtfs-source-feeds.transit.land.dmfr.json"
MANAGED_TAG = "es_nap_fichero_id"
# Above this many removals in one run (or a tenth of the managed feeds, if
# larger), the script stops instead of writing; see save_dmfr_file.
MAX_REMOVED_FEEDS = 5
FEED_URL_TEMPLATE = "https://gtfs-source-feeds.transit.land/es-nap-{fichero_id}.zip"

# NAP ficheros deliberately not registered, though NAP still lists them. Without
# this list each run would add them back.
EXCLUDED_FICHERO_IDS = {
    "1163": "Cercanías Madrid: an empty stub; Cercanías Renfe (1130) covers it",
    "1132": "VAC-232 Madrid-Málaga-Algeciras: expired 2023, not republished",
    "1711": "VAC-231 Madrid-Piedrabuena-Agudo: expired 2023, not republished",
    "1712": "VAC-245 Huesca-Barcelona: expired 2024, not republished",
    "1713": "VAC-124 Huesca-Lleida: expired 2024, not republished",
}

# API configuration
API_BASE_URL = "https://nap.transportes.gob.es/api"
API_KEY = os.environ.get("SPANISH_NAP_API_KEY")

if not API_KEY:
    raise ValueError("Please set SPANISH_NAP_API_KEY environment variable")

# Configure retry strategy
retry_strategy = Retry(
    total=3,  # number of retries
    backoff_factor=1,  # wait 1, 2, 4 seconds between retries
    status_forcelist=[429, 500, 502, 503, 504]  # HTTP status codes to retry on
)

# Create session with retry strategy
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)

HEADERS = {
    "ApiKey": API_KEY,
    "accept": "application/json"
}

def save_api_response(endpoint: str, data: Dict):
    """Save API response to a JSON file for debugging."""
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    
    # Create filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = debug_dir / f"api_response_{endpoint}_{timestamp}.json"
    
    # Save response data
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    
    logger.debug(f"Saved API response to {filename}")

def make_request(method: str, url: str, **kwargs) -> requests.Response:
    """Make an API request with retry logic and rate limiting."""
    try:
        response = session.request(method, url, **kwargs)
        response.raise_for_status()
        
        # Extract endpoint name for debug file
        endpoint = url.replace(API_BASE_URL, "").strip("/").replace("/", "_")
        
        # Save response for debugging if flag is enabled
        if args.save_api_response:
            try:
                save_api_response(endpoint, response.json())
            except Exception as e:
                logger.warning(f"Failed to save API response: {e}")
        
        # Add small delay to avoid hitting rate limits
        time.sleep(0.5)
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
        raise

def validate_url(url: str) -> bool:
    """Validate if a URL is well-formed and accessible."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def get_gtfs_feeds() -> List[Dict]:
    """Query the API to get all GTFS feeds."""
    # Get list of all files
    response = make_request(
        "GET",
        f"{API_BASE_URL}/Fichero/GetList",
        headers=HEADERS
    )
    data = response.json()
    
    # Extract GTFS feeds from the response
    feeds = []
    for conjunto in data.get("conjuntosDatoDto", []):
        # Check if any of the transport types are bus or train
        transport_types = [t.get("nombre", "").lower() for t in conjunto.get("tiposTransporte", [])]
        if not any(t.lower() in ["ferroviario", "autobús"] for t in transport_types):
            continue

        # Look for GTFS files in ficherosDto
        for fichero in conjunto.get("ficherosDto", []):
            if fichero.get("tipoFicheroNombre") in ("GTFS", "GTFS-ZIP"):
                # Combine conjunto and fichero data - no need for extra API call
                # since conjunto already has all the data we need
                feed_data = {
                    "nombre": conjunto.get("nombre"),
                    "descripcion": conjunto.get("descripcion"),
                    "organizacion": conjunto.get("organizacion"),
                    "operadores": conjunto.get("operadores"),
                    "fichero": fichero
                }
                feeds.append(feed_data)
    
    logger.info(f"Found {len(feeds)} GTFS feeds")
    return feeds

def get_organization_name(org_id: int) -> str:
    """Get organization name from API."""
    response = requests.get(
        f"{API_BASE_URL}/Organizacion/{org_id}",
        headers=HEADERS
    )
    response.raise_for_status()
    return response.json()["nombre"]

def get_operator_name(operator_id: int) -> str:
    """Get operator name from API."""
    response = requests.get(
        f"{API_BASE_URL}/Operador/{operator_id}",
        headers=HEADERS
    )
    response.raise_for_status()
    return response.json()["nombre"]

def create_onestop_id(name: str, prefix: str = "o") -> str:
    """Create a Onestop ID from a name."""
    # Remove special characters and spaces, convert to lowercase
    clean_name = "".join(c.lower() if c.isalnum() else "~" for c in name)
    # Remove multiple consecutive tildes and leading/trailing tildes
    clean_name = re.sub(r'~+', '~', clean_name)  # Replace multiple tildes with single tilde
    clean_name = re.sub(r'^~+|~+$', '', clean_name)  # Remove leading/trailing tildes
    return f"{prefix}-{clean_name}"

def create_dmfr_feed(feed_data: Dict) -> Dict:
    """Transform API feed data into DMFR format."""
    logger.debug(f"Processing feed data")
    
    # Extract feed name and metadata
    feed_name = feed_data.get("nombre")
    if not feed_name:
        logger.error(f"Could not find feed name in data: {feed_data}")
        raise ValueError("Feed name not found in data")
    
    feed_id = create_onestop_id(feed_name, "f")
    
    # Get the feed URL from the fichero data
    fichero = feed_data.get("fichero", {})
    fichero_id = fichero.get("ficheroId")
    if not fichero_id:
        logger.error(f"Could not find ficheroId in data: {feed_data}")
        raise ValueError("File ID not found in data")
    
    feed_url = FEED_URL_TEMPLATE.format(fichero_id=fichero_id)

    # Create basic feed record
    dmfr_feed = {
        "id": feed_id,
        "spec": "gtfs",
        "urls": {
            "static_current": feed_url
        },
    }

    dmfr_feed["tags"] = {
        MANAGED_TAG: str(fichero_id),
    }

    # Add license
    dmfr_feed["license"] = {
        "url": "https://nap.transportes.gob.es/licencia-datos",
        "use_without_attribution": "no",  # Must cite MITRAMS as data source
        "create_derived_product": "yes",  # Section "Ámbito" point 3 explicitly allows value-added services
        "commercial_use_allowed": "yes",  # License allows both commercial and non-commercial use
        "share_alike_optional": "yes",    # Derived works can use different licenses per "Ámbito" point 3
        "attribution_text": "Powered by MITRAMS",
        "attribution_instructions": "Must include attribution text and a link to https://www.transportes.gob.es/"
    }

    dmfr_feed["authorization"] = {
        "type": "query_param",
        "param_name": "token",
    }

    # operators

    operators = feed_data.get("operadores", [])
    # we can handle one operator
    # but it turns out agency_id isn't in the API, so we can't link multiple operators to a single feed
    if operators and len(operators) == 1:
        op = operators[0]
        dmfr_feed["operators"] = []
        operator_dict = {
            "onestop_id": create_onestop_id(op["nombre"]),
            "name": op["nombre"]
        }
        # Only add website if URL exists and is valid
        url = op.get("url", "")
        if url and validate_url(url):
            parsed = urlparse(url)
            operator_dict["website"] = f"{parsed.scheme.lower()}://{parsed.netloc}{parsed.path}" # DMFR format wants http:// or https://
            if parsed.query:
                operator_dict["website"] += f"?{parsed.query}"
            if parsed.fragment:
                operator_dict["website"] += f"#{parsed.fragment}"
        dmfr_feed["operators"].append(operator_dict)

    return dmfr_feed

def save_dmfr_file(feeds: List[Dict]):
    """Update the NAP feeds in the shared DMFR file, preserving everything else."""
    dmfr_file = DMFR_FILE

    # The file is shared with feeds this script does not manage, so it must be
    # read successfully. Starting from an empty file would drop all of them.
    with open(dmfr_file, 'r', encoding='utf-8') as f:
        existing_dmfr = json.load(f)
    logger.info(f"Found existing DMFR file with {len(existing_dmfr.get('feeds', []))} feeds")

    # Create lookup of existing feeds by fichero_id
    existing_feeds_by_id = {}
    feeds_to_preserve = []  # Feeds this script does not manage: no MANAGED_TAG

    for feed in existing_dmfr.get('feeds', []):
        fichero_id = feed.get('tags', {}).get(MANAGED_TAG)

        # Preserve feeds without the tag (other sources sharing this file)
        if not fichero_id:
            feeds_to_preserve.append(feed)
        else:
            existing_feeds_by_id[fichero_id] = feed
    
    # Process new feeds - first add fichero_id to each feed's tags
    updated_feeds = []
    new_feeds_by_id = {}
    
    for feed in feeds:
        fichero_id = feed.get('tags', {}).get(MANAGED_TAG)
        if not fichero_id:
            logger.warning(f"Feed missing fichero_id, skipping: {feed.get('id')}")
            continue
        new_feeds_by_id[fichero_id] = feed
    
    # A managed feed that is missing from the API listing gets removed. Name
    # each one, and refuse to remove an implausible share at once: a partial
    # listing or a relabelled transport type should not silently delete records.
    removed = [f for fid, f in existing_feeds_by_id.items() if fid not in new_feeds_by_id]
    for feed in removed:
        logger.warning(f"Removing {feed.get('id')} (fichero {feed['tags'][MANAGED_TAG]}): no longer in the NAP listing")
    max_removed = max(MAX_REMOVED_FEEDS, len(existing_feeds_by_id) // 10)
    if len(removed) > max_removed:
        logger.error(f"{len(removed)} managed feeds would be removed (limit {max_removed}); aborting without writing")
        raise SystemExit(1)

    # Existing feeds still in the API: start from the existing record, so its
    # Onestop ID and any hand-curated fields (operators, supersedes_ids,
    # static_historic, extra tags, name, ...) survive, and overlay only what the
    # API provides. Operators come from the API only if the record has none.
    existing_matched_count = 0
    for fichero_id, existing_feed in existing_feeds_by_id.items():
        if fichero_id not in new_feeds_by_id:
            continue
        new_feed = new_feeds_by_id.pop(fichero_id)
        merged_feed = copy.deepcopy(existing_feed)
        urls = merged_feed.setdefault('urls', {})
        old_url, new_url = urls.get('static_current'), new_feed['urls']['static_current']
        # A replaced source URL goes to static_historic; a scheme-only change
        # (http -> https) is the same source and is not recorded.
        if old_url and old_url.split('://', 1)[-1] != new_url.split('://', 1)[-1]:
            historic = urls.setdefault('static_historic', [])
            if old_url not in historic:
                historic.append(old_url)
        urls['static_current'] = new_url
        for key in ('license', 'authorization'):
            if key in new_feed:
                merged_feed[key] = new_feed[key]
        merged_feed.setdefault('tags', {}).update(new_feed.get('tags', {}))
        if not merged_feed.get('operators') and new_feed.get('operators'):
            merged_feed['operators'] = new_feed['operators']
        updated_feeds.append(merged_feed)
        existing_matched_count += 1

    # Then add new feeds, unless their generated Onestop ID is already taken:
    # the file is shared, and a duplicate would fail validation for every feed.
    taken_ids = {f.get('id') for f in updated_feeds} | {f.get('id') for f in feeds_to_preserve}
    new_count = 0
    for fichero_id, feed in new_feeds_by_id.items():
        if feed['id'] in taken_ids:
            logger.error(f"Skipping new fichero {fichero_id}: Onestop ID {feed['id']} already exists in {dmfr_file.name}")
            continue
        taken_ids.add(feed['id'])
        updated_feeds.append(feed)
        new_count += 1

    # Finally, add feeds that should be preserved (GTFS-RT, manually added, etc.)
    preserved_count = len(feeds_to_preserve)
    updated_feeds.extend(feeds_to_preserve)
    
    # Replace only the feed list; every other top-level field ($schema,
    # operators, ...) is carried over from the existing file unchanged.
    dmfr_data = dict(existing_dmfr)
    dmfr_data["feeds"] = updated_feeds

    # Save file with consistent formatting
    with open(dmfr_file, 'w', encoding='utf-8') as f:
        json.dump(dmfr_data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    
    logger.info(f"Created DMFR file with {len(updated_feeds)} feeds ({new_count} new, {existing_matched_count} existing from API, {preserved_count} preserved)")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Collect Spanish NAP API GTFS feeds")
    parser.add_argument("--save-api-response", action="store_true", help="Save API responses to debug directory")
    global args
    args = parser.parse_args()
    
    # Get all GTFS feeds
    feeds_data = get_gtfs_feeds()
    logger.info(f"Found {len(feeds_data)} feeds")

    # Safety check: abort if the API returned no feeds, to avoid
    # zeroing out the existing DMFR file due to an API change or outage
    if len(feeds_data) == 0:
        logger.error("API returned 0 GTFS feeds — aborting to avoid overwriting the existing DMFR file. "
                      "The upstream API response format may have changed.")
        raise SystemExit(1)

    # Transform all feeds to DMFR format
    dmfr_feeds = []
    for feed in feeds_data:
        fichero_id = str((feed.get("fichero") or {}).get("ficheroId", ""))
        if fichero_id in EXCLUDED_FICHERO_IDS:
            logger.info(f"Skipping excluded fichero {fichero_id}: {EXCLUDED_FICHERO_IDS[fichero_id]}")
            continue
        try:
            dmfr_feed = create_dmfr_feed(feed)
            dmfr_feeds.append(dmfr_feed)
        except Exception as e:
            logger.error(f"Error processing feed: {e}")
            logger.debug(f"Problematic feed data: {json.dumps(feed, indent=2)}")
            continue

    logger.info(f"Successfully processed {len(dmfr_feeds)} feeds")

    # Save all feeds to a single file
    save_dmfr_file(dmfr_feeds)

if __name__ == "__main__":
    main()

