#!/usr/bin/env python3

"""
Query the Spanish National Access Point (NAP) API and generate DMFR file.

This script will:
1. Query the NAP API to get all GTFS feeds
2. Transform the data into DMFR format
3. Save a single DMFR file to ./feeds/nap.transportes.gob.es.dmfr.json

See https://nap.transportes.gob.es/Account/InstruccionesAPI

Usage:
    export SPANISH_NAP_API_KEY=your-api-key
    python3 scripts/debug/scrape-spanish-nap.py [--save-api-response]
"""

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
            if fichero.get("tipoFicheroNombre") == "GTFS":
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
    
    try:
        feed_url = f"{API_BASE_URL}/Fichero/download/{fichero_id}"
    except Exception as e:
        logger.error(f"Error getting feed URL for ID {fichero_id}: {e}")
        raise ValueError(f"Could not get feed URL: {e}")
    
    # Create basic feed record
    dmfr_feed = {
        "id": feed_id,
        "spec": "gtfs",
        "urls": {
            "static_current": feed_url
        },
    }

    dmfr_feed["tags"] = {
        "es_nap_fichero_id": str(fichero_id)
    }
    operators = feed_data.get("operadores", [])
    # if there is a single operator, we're readying an actual operator record
    # otherwise we'll just put in a note for reference while editing DMFR
    if len(operators) > 1:
        operator_names = [op.get("nombre", "Unknown Operator") for op in operators]
        operator_summary = "Operators: " + ", ".join(operator_names)
        dmfr_feed["tags"]["notes"] = operator_summary

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
        "type": "header",
        "param_name": "ApiKey",
        "info_url": "https://nap.transportes.gob.es/"
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
    """Save all feeds to a single DMFR file, preserving existing records."""
    filename = "../feeds/nap.transportes.gob.es.dmfr.json"
    
    # Try to read existing file
    existing_dmfr = {
        "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.5.1.json",
        "feeds": [],
        "license_spdx_identifier": "CDLA-Permissive-1.0"
    }
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                existing_dmfr = json.load(f)
                logger.info(f"Found existing DMFR file with {len(existing_dmfr.get('feeds', []))} feeds")
    except Exception as e:
        logger.warning(f"Error reading existing DMFR file: {e}")
    
    # Create lookup of existing feeds by fichero_id
    existing_feeds_by_id = {}
    for feed in existing_dmfr.get('feeds', []):
        fichero_id = feed.get('tags', {}).get('es_nap_fichero_id')
        if fichero_id:
            existing_feeds_by_id[fichero_id] = feed
    
    # Process new feeds
    updated_feeds = []
    new_feeds_by_id = {}
    
    for feed in feeds:
        fichero_id = feed.get('tags', {}).get('es_nap_fichero_id')
        if not fichero_id:
            logger.warning(f"Feed missing fichero_id, skipping: {feed.get('id')}")
            continue
        new_feeds_by_id[fichero_id] = feed
    
    # First add all existing feeds that are still present in new data
    for fichero_id, feed in existing_feeds_by_id.items():
        if fichero_id in new_feeds_by_id:
            updated_feeds.append(feed)  # Keep the existing record
            del new_feeds_by_id[fichero_id]  # Remove from new feeds since we're keeping existing
    
    # Then add all new feeds
    updated_feeds.extend(new_feeds_by_id.values())
    
    # Create final DMFR data
    dmfr_data = {
        "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.5.1.json",
        "feeds": updated_feeds,
        "license_spdx_identifier": "CDLA-Permissive-1.0"
    }

    # Ensure feeds directory exists
    Path("feeds").mkdir(exist_ok=True)

    # Save file with consistent formatting
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(dmfr_data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    
    logger.info(f"Created DMFR file with {len(updated_feeds)} feeds ({len(new_feeds_by_id)} new, {len(existing_feeds_by_id)} existing)")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Scrape Spanish NAP API for GTFS feeds")
    parser.add_argument("--save-api-response", action="store_true", help="Save API responses to debug directory")
    global args
    args = parser.parse_args()
    
    # Get all GTFS feeds
    feeds_data = get_gtfs_feeds()
    logger.info(f"Found {len(feeds_data)} feeds")
    
    # Transform all feeds to DMFR format
    dmfr_feeds = []
    for feed in feeds_data:
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
