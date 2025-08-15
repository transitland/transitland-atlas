#!/usr/bin/env -S uv run --script

# /// script
# dependencies = [
#   "requests>=2.31.0",
# ]
# ///

"""
Script to collect data from the GTFS Data JP API and identify feeds to add to Transitland Atlas repo.

This script:
1. Fetches all available feeds from the GTFS Data JP API
2. Checks which feeds are not already in the Transitland Atlas repo
3. Generates DMFR records for new feeds with stable identifiers encoded in Onestop IDs
4. Creates GTFS-RT feed records when real-time URLs are available
5. Creates operator records to associate static and real-time feeds
6. Outputs a summary of what should be added

Usage:
    python collect-gtfs-data-jp.py [--analysis]
    
Options:
    --analysis    Generate detailed analysis file for debugging (default: False)
"""

import requests
import json
import logging
import os
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from urllib.parse import urlparse
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
GTFS_DATA_JP_BASE_URL = "https://api.gtfs-data.jp/v2"
FEEDS_ENDPOINT = f"{GTFS_DATA_JP_BASE_URL}/feeds"
FILES_ENDPOINT = f"{GTFS_DATA_JP_BASE_URL}/files"
REQUEST_TIMEOUT = 30  # seconds
MAX_FEEDS_TO_LOG = 10  # Number of feeds to show in summary

# Transitland Atlas repo paths
REPO_ROOT = Path(__file__).parent.parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"

class GTFSDataJPCollector:
    """Collector for GTFS Data JP API"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Transitland-Atlas-Collector/1.0'
        })
    
    def fetch_feeds(self) -> List[Dict]:
        """Fetch all available feeds from the GTFS Data JP API"""
        try:
            logger.info("Fetching feeds from GTFS Data JP API...")
            response = self.session.get(FEEDS_ENDPOINT, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            data = response.json()
            if data.get('code') == 200:
                feeds = data.get('body', [])
                if not isinstance(feeds, list):
                    logger.error("API returned invalid feeds format")
                    return []
                logger.info(f"Found {len(feeds)} feeds")
                return feeds
            else:
                logger.error(f"API returned error: {data.get('message', 'Unknown error')}")
                return []
                
        except requests.RequestException as e:
            logger.error(f"Failed to fetch feeds: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse API response: {e}")
            return []
    
    def fetch_files(self) -> List[Dict]:
        """Fetch all available files from the GTFS Data JP API"""
        try:
            logger.info("Fetching files from GTFS Data JP API...")
            response = self.session.get(FILES_ENDPOINT, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            data = response.json()
            if data.get('code') == 200:
                files = data.get('body', [])
                if not isinstance(files, list):
                    logger.error("API returned invalid files format")
                    return []
                logger.info(f"Found {len(files)} files")
                return files
            else:
                logger.error(f"API returned error: {data.get('message', 'Unknown error')}")
                return []
                
        except requests.RequestException as e:
            logger.error(f"Failed to fetch files: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse API response: {e}")
            return []

class TransitlandAtlasAnalyzer:
    """Analyzer for Transitland Atlas repository"""
    
    def __init__(self):
        self.feeds_dir = FEEDS_DIR
        self.existing_feeds: Set[str] = set()
        self.existing_urls: Set[str] = set()
        self.load_existing_feeds()
    
    def load_existing_feeds(self):
        """Load all existing feeds from the repository"""
        logger.info("Loading existing feeds from Transitland Atlas repo...")
        logger.info(f"Scanning directory: {self.feeds_dir}")
        
        dmfr_files_found = list(self.feeds_dir.glob("*.dmfr.json"))
        logger.info(f"Found {len(dmfr_files_found)} DMFR files: {[f.name for f in dmfr_files_found]}")
        
        for dmfr_file in dmfr_files_found:
            try:
                logger.debug(f"Processing DMFR file: {dmfr_file}")
                with open(dmfr_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Extract feed IDs and URLs
                feeds_in_file = data.get('feeds', [])
                logger.debug(f"Found {len(feeds_in_file)} feeds in {dmfr_file.name}")
                
                for feed in feeds_in_file:
                    feed_id = feed.get('id')
                    if feed_id:
                        self.existing_feeds.add(feed_id)
                    
                    # Check static_current URLs
                    static_current = feed.get('urls', {}).get('static_current')
                    if static_current:
                        self.existing_urls.add(static_current)
                        logger.debug(f"Added URL: {static_current}")
                        
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to parse {dmfr_file}: {e}")
        
        logger.info(f"Loaded {len(self.existing_feeds)} existing feed IDs")
        logger.info(f"Loaded {len(self.existing_urls)} existing URLs")
        
        # Log some examples of what was found
        if self.existing_urls:
            sample_urls = list(self.existing_urls)[:3]
            logger.info(f"Sample URLs found: {sample_urls}")
        if self.existing_feeds:
            sample_feeds = list(self.existing_feeds)[:3]
            logger.info(f"Sample feed IDs found: {sample_feeds}")
    
    def is_feed_new(self, feed_data: Dict) -> bool:
        """Check if a feed is new (not already in the repo)"""
        org_id = feed_data.get('organization_id', '')
        feed_id = feed_data.get('feed_id', '')
        if org_id and feed_id:
            # Construct the expected URL pattern for this feed
            expected_url_pattern = f"api.gtfs-data.jp/v2/organizations/{org_id}/feeds/{feed_id}".lower()
            logger.debug(f"Checking if feed {org_id}/{feed_id} exists. Looking for pattern: {expected_url_pattern}")
            
            for existing_url in self.existing_urls:
                # Strip protocol and make case-insensitive for comparison
                url_lower = existing_url.lower()
                if url_lower.startswith('http://'):
                    url_lower = url_lower[7:]  # Remove 'http://'
                elif url_lower.startswith('https://'):
                    url_lower = url_lower[8:]  # Remove 'https://'
                
                if expected_url_pattern in url_lower:
                    logger.info(f"Feed {org_id}/{feed_id} already exists via URL: {existing_url}")
                    return False
            
            logger.debug(f"Feed {org_id}/{feed_id} is new - no matching URLs found")
        
        return True
    
    def is_url_new(self, url: str) -> bool:
        """Check if a URL is new (not already in the repo)"""
        return url not in self.existing_urls

    def is_feed_in_dmfr_file(self, feed_data: Dict) -> bool:
        """Check if a feed is already present in the current DMFR file"""
        org_id = feed_data.get('organization_id', '')
        feed_id = feed_data.get('feed_id', '')
        if org_id and feed_id:
            expected_onestop_id = DMFRGenerator.generate_feed_id(org_id, feed_id)
            for feed in self.feeds_dir.glob("*.dmfr.json"):
                try:
                    with open(feed, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for existing_feed in data.get('feeds', []):
                            if existing_feed.get('id') == expected_onestop_id:
                                return True
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to check DMFR file {feed.name}: {e}")
        return False

class DMFRGenerator:
    """Generator for DMFR records with stable identifiers encoded"""
    
    @staticmethod
    def generate_feed_id(org_id: str, feed_id: str, suffix: str = "") -> str:
        """Generate a unique feed ID with stable identifiers encoded"""
        # Clean the identifiers for use in Onestop ID
        org_id_clean = org_id.lower().replace('_', '~').replace('-', '~')
        feed_id_clean = feed_id.lower().replace('_', '~').replace('-', '~')
        
        # Create ID with organization and feed identifiers encoded
        base_id = f"f-{org_id_clean}~{feed_id_clean}"
        if suffix:
            base_id += f"~{suffix}"
        return base_id
    
    @staticmethod
    def generate_operator_id(org_id: str) -> str:
        """Generate a unique operator ID"""
        org_id_clean = org_id.lower().replace('_', '~').replace('-', '~')
        return f"o-{org_id_clean}"
    
    @staticmethod
    def map_license(license_name: str) -> Optional[str]:
        """Map Japanese license names to SPDX identifiers"""
        license_mapping = {
            "CC BY 4.0": "CC-BY-4.0",
            "CC BY-SA 4.0": "CC-BY-SA-4.0",
            "CC0 1.0": "CC0-1.0",
            "CC BY 2.1": "CC-BY-SA-2.1-JP",
            "CC BY-SA 2.1": "CC-BY-SA-2.1-JP",
        }
        return license_mapping.get(license_name)
    
    @staticmethod
    def has_realtime_capabilities(feed_data: Dict) -> bool:
        """Check if a feed has real-time capabilities"""
        real_time = feed_data.get('real_time', {})
        return any([
            real_time.get('trip_update_url'),
            real_time.get('vehicle_position_url'),
            real_time.get('alert_url')
        ])
    
    @staticmethod
    def create_static_gtfs_record(feed_data: Dict, file_data: Optional[Dict] = None) -> Dict:
        """Create a DMFR record for a static GTFS feed"""
        # Validate required fields
        org_id = feed_data.get('organization_id', '')
        feed_id = feed_data.get('feed_id', '')
        feed_name = feed_data.get('feed_name', '')
        
        if not org_id or not feed_id or not feed_name:
            logger.warning(f"Missing required fields for feed: org_id={org_id}, feed_id={feed_id}, name={feed_name}")
            return None
        
        org_name = feed_data.get('organization_name', '')
        pref_id = feed_data.get('feed_pref_id')
        
        # Generate unique feed ID with stable identifiers encoded
        feed_onestop_id = DMFRGenerator.generate_feed_id(org_id, feed_id)
        
        # Get license information
        license_info = feed_data.get('feed_license', '')
        spdx_identifier = DMFRGenerator.map_license(license_info)
        
        # Create DMFR record
        dmfr_record = {
            "id": feed_onestop_id,
            "spec": "gtfs",
            "name": feed_name,
            "urls": {}
        }
        
        # Add static_current URL if available
        if file_data and file_data.get('file_url'):
            dmfr_record["urls"]["static_current"] = file_data['file_url'].strip()
        
        # Add license information
        if spdx_identifier:
            dmfr_record["license"] = {
                "spdx_identifier": spdx_identifier,
                "use_without_attribution": "no" if "CC-BY" in spdx_identifier else None,
                "create_derived_product": "yes" if "CC" in spdx_identifier else None,
                "commercial_use_allowed": "yes" if "CC" in spdx_identifier else None,
                "redistribution_allowed": "yes" if "CC" in spdx_identifier else None
            }
        
        # Add comprehensive tags following repository patterns
        tags = {
            "gtfs_data_jp_prefecture_id": str(pref_id),
        }
        
        # Note: Discontinued feeds are not added to Transitland Atlas
        
        dmfr_record["tags"] = tags
        
        return dmfr_record
    
    @staticmethod
    def create_realtime_gtfs_records(feed_data: Dict) -> List[Dict]:
        """Create DMFR records for GTFS-RT feeds when available"""
        real_time = feed_data.get('real_time', {})
        org_id = feed_data.get('organization_id', '')
        feed_id = feed_data.get('feed_id', '')
        feed_name = feed_data.get('feed_name', '')
        org_name = feed_data.get('organization_name', '')
        
        realtime_records = []
        
        # Check if any real-time endpoints are available
        has_trip_updates = bool(real_time.get('trip_update_url'))
        has_vehicle_positions = bool(real_time.get('vehicle_position_url'))
        has_alerts = bool(real_time.get('alert_url'))
        
        # Only create a record if at least one real-time endpoint exists
        if has_trip_updates or has_vehicle_positions or has_alerts:
            # Create a single consolidated GTFS-RT feed record
            urls = {}
            if has_trip_updates:
                urls["realtime_trip_updates"] = real_time['trip_update_url'].strip()
            if has_vehicle_positions:
                urls["realtime_vehicle_positions"] = real_time['vehicle_position_url'].strip()
            if has_alerts:
                urls["realtime_alerts"] = real_time['alert_url'].strip()
            
            # Generate a single ID for the real-time feed
            rt_feed_id = DMFRGenerator.generate_feed_id(org_id, feed_id, "rt")
            
            # Create descriptive name based on available endpoints
            endpoint_types = []
            if has_trip_updates:
                endpoint_types.append("Trip Updates")
            if has_vehicle_positions:
                endpoint_types.append("Vehicle Positions")
            if has_alerts:
                endpoint_types.append("Alerts")
            
            name_suffix = " + ".join(endpoint_types)
            
            realtime_record = {
                "id": rt_feed_id,
                "spec": "gtfs-rt",
                "name": f"{feed_name} - {name_suffix}",
                "urls": urls
            }
            
            realtime_records.append(realtime_record)
        
        return realtime_records
    
    @staticmethod
    def create_operator_record(feed_data: Dict, static_feed_id: str, realtime_feed_ids: List[str]) -> Dict:
        """Create an operator record to associate static and real-time feeds"""
        org_id = feed_data.get('organization_id', '')
        org_name = feed_data.get('organization_name', '')
        pref_id = feed_data.get('feed_pref_id')
        
        # Generate operator ID
        operator_id = DMFRGenerator.generate_operator_id(org_id)
        
        # Collect all associated feed IDs
        associated_feeds = [{"feed_onestop_id": static_feed_id}]
        for rt_feed_id in realtime_feed_ids:
            associated_feeds.append({"feed_onestop_id": rt_feed_id})
        
        # Create operator record
        operator_record = {
            "onestop_id": operator_id,
            "name": org_name,
            "website": feed_data.get('organization_web_url'),
            "associated_feeds": associated_feeds,
            "tags": {
                "source": "gtfs-data.jp",
                "gtfs_data_jp_organization_id": org_id,
                "gtfs_data_jp_prefecture_id": str(pref_id),
                "notes": f"Derived from gtfs-data.jp API"
            }
        }
        
        # Note: Organization email is not included in tags to keep them simple
        
        return operator_record

def main():
    """Main function to collect and analyze GTFS Data JP feeds"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Collect GTFS Data JP feeds for Transitland Atlas')
    parser.add_argument('--analysis', action='store_true', 
                       help='Generate detailed analysis file for debugging')
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    logger.info("Starting GTFS Data JP data collection process...")
    
    # Initialize components
    collector = GTFSDataJPCollector()
    analyzer = TransitlandAtlasAnalyzer()
    
    # Fetch data from API
    feeds = collector.fetch_feeds()
    files = collector.fetch_files()
    
    if not feeds:
        logger.error("No feeds found. Exiting.")
        return
    
    # Create a mapping of files by organization and feed
    files_by_feed = {}
    for file_data in files:
        org_id = file_data.get('organization_id', '')
        feed_id = file_data.get('feed_id', '')
        key = f"{org_id}_{feed_id}"
        
        if key not in files_by_feed:
            files_by_feed[key] = []
        files_by_feed[key].append(file_data)
    
    # Analyze feeds and generate DMFR records
    new_feeds = []
    existing_feeds = []
    discontinued_feeds = []
    feeds_to_remove = []  # Track feeds that should be removed from the DMFR file
    all_realtime_feeds = []
    all_operators = []
    
    for feed in feeds:
        org_id = feed.get('organization_id', '')
        feed_id = feed.get('feed_id', '')
        key = f"{org_id}_{feed_id}"
        
        # Get the most recent file for this feed
        feed_files = files_by_feed.get(key, [])
        current_file = None
        if feed_files:
            # Sort by last_updated_at and get the most recent
            feed_files.sort(key=lambda x: x.get('file_last_updated_at', ''), reverse=True)
            current_file = feed_files[0]
        
        # Skip feeds that have no files available
        if not current_file:
            logger.warning(f"Skipping feed with no files available: {feed.get('feed_name')} ({org_id}/{feed_id})")
            continue
        
        # Check if feed is discontinued
        if feed.get('feed_is_discontinued'):
            discontinued_feeds.append(feed)
            logger.info(f"Discontinued feed (skipping): {feed.get('feed_name')} ({org_id}/{feed_id})")
            
            # Check if this discontinued feed exists in the current DMFR file
            if analyzer.is_feed_in_dmfr_file(feed):
                feeds_to_remove.append(feed)
                logger.info(f"Feed marked for removal from DMFR file: {feed.get('feed_name')} ({org_id}/{feed_id})")
            
            continue
        
        # Always check actual repository state to determine if feed exists
        if analyzer.is_feed_new(feed):
            # Generate static GTFS record
            static_dmfr_record = DMFRGenerator.create_static_gtfs_record(feed, current_file)
            if static_dmfr_record is None:
                logger.warning(f"Skipping feed due to missing required fields: {feed.get('feed_name')}")
                continue
                
            new_feeds.append({
                'feed_data': feed,
                'file_data': current_file,
                'dmfr_record': static_dmfr_record
            })
            
            # Generate real-time GTFS records if available
            realtime_records = DMFRGenerator.create_realtime_gtfs_records(feed)
            if realtime_records:
                all_realtime_feeds.extend(realtime_records)
                logger.info(f"Created {len(realtime_records)} real-time feeds for {feed.get('feed_name')}")
            
            # Generate operator record if we have real-time feeds
            if realtime_records:
                operator_record = DMFRGenerator.create_operator_record(
                    feed, 
                    static_dmfr_record['id'], 
                    [rt['id'] for rt in realtime_records]
                )
                all_operators.append(operator_record)
                logger.info(f"Created operator record for {org_id}/{feed_id}")
            
            logger.info(f"New feed found: {feed.get('feed_name')} ({org_id}/{feed_id})")
        else:
            existing_feeds.append(feed)
            logger.debug(f"Existing feed: {feed.get('feed_name')} ({org_id}/{feed_id})")
    
    # Generate output
    output = {
        'summary': {
            'total_feeds_found': len(feeds),
            'new_feeds': len(new_feeds),
            'existing_feeds': len(existing_feeds),
            'discontinued_feeds': len(discontinued_feeds),
            'feeds_to_remove': len(feeds_to_remove),
            'realtime_feeds_created': len(all_realtime_feeds),
            'operators_created': len(all_operators),
            'scraped_at': datetime.now(timezone.utc).isoformat()
        },
        'new_feeds': new_feeds,
        'existing_feeds': existing_feeds,
        'discontinued_feeds': discontinued_feeds,
        'feeds_to_remove': feeds_to_remove,
        'realtime_feeds': all_realtime_feeds,
        'operators': all_operators
    }
    
    # Save detailed output
    output_file = Path(__file__).parent / "gtfs-data-jp-analysis.json"
    if args.analysis:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Detailed analysis saved to: {output_file}")
    else:
        logger.info("Skipping detailed analysis file generation.")
    
    # Generate DMFR file for new feeds (static + real-time + operators)
    if new_feeds or all_realtime_feeds or all_operators or feeds_to_remove:
        # First, read existing DMFR file to remove discontinued feeds
        existing_dmfr_file = FEEDS_DIR / 'gtfs-data-jp.dmfr.json'
        existing_feeds = []
        existing_operators = []
        
        if existing_dmfr_file.exists():
            try:
                with open(existing_dmfr_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    existing_feeds = existing_data.get('feeds', [])
                    existing_operators = existing_data.get('operators', [])
                    logger.info(f"Loaded {len(existing_feeds)} existing feeds and {len(existing_operators)} existing operators from current DMFR file")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read existing DMFR file: {e}")
        
        # Remove discontinued feeds from existing feeds
        feeds_to_remove_ids = set()
        for feed in feeds_to_remove:
            onestop_id = DMFRGenerator.generate_feed_id(feed.get('organization_id', ''), feed.get('feed_id', ''))
            feeds_to_remove_ids.add(onestop_id)
            logger.info(f"Marking feed for removal: {onestop_id} ({feed.get('feed_name')})")
        
        # Filter out discontinued feeds
        filtered_existing_feeds = [feed for feed in existing_feeds if feed.get('id') not in feeds_to_remove_ids]
        removed_count = len(existing_feeds) - len(filtered_existing_feeds)
        if removed_count > 0:
            logger.info(f"Removed {removed_count} discontinued feeds from existing DMFR file")
        
        # Create new DMFR file with new feeds + existing feeds (minus discontinued ones)
        dmfr_output = {
            "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.6.0.json",
            "feeds": filtered_existing_feeds + [feed['dmfr_record'] for feed in new_feeds] + all_realtime_feeds,
            "operators": existing_operators + all_operators
        }
        
        dmfr_file = FEEDS_DIR / 'gtfs-data-jp.dmfr.json'
        with open(dmfr_file, 'w', encoding='utf-8') as f:
            json.dump(dmfr_output, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Generated DMFR file with {len(filtered_existing_feeds)} existing feeds, {len(new_feeds)} new static feeds, {len(all_realtime_feeds)} real-time feeds, and {len(existing_operators + all_operators)} operators: {dmfr_file}")
        
        # Format the DMFR file using transitland dmfr format for consistent sorting
        try:
            import subprocess
            result = subprocess.run(['transitland', 'dmfr', 'format', '--save', str(dmfr_file)], 
                                  capture_output=True, text=True, check=True)
            logger.info("Applied transitland dmfr format to ensure consistent sorting")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to format DMFR file: {e}")
            logger.warning("DMFR file generated but not formatted - consider running 'transitland dmfr format --save' manually")
        except FileNotFoundError:
            logger.warning("transitland command not found - DMFR file generated but not formatted")
            logger.warning("Install transitland CLI and run 'transitland dmfr format --save' manually for consistent sorting")
    else:
        logger.info("No changes to make to DMFR file")
    
    # Print summary
    logger.info("=" * 50)
    logger.info("DATA COLLECTION SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total feeds found: {len(feeds)}")
    logger.info(f"New static feeds to add: {len(new_feeds)}")
    logger.info(f"Real-time feeds created: {len(all_realtime_feeds)}")
    logger.info(f"Operators created: {len(all_operators)}")
    logger.info(f"Existing feeds (already in repo): {len(existing_feeds)}")
    logger.info(f"Discontinued feeds (skipped): {len(discontinued_feeds)}")
    logger.info(f"Feeds to remove from DMFR file: {len(feeds_to_remove)}")
    if args.analysis:
        logger.info(f"Detailed analysis saved to: {output_file}")
    logger.info(f"DMFR file for new feeds: {dmfr_file}")
    logger.info("\nNew feeds to consider adding:")
    for feed in new_feeds[:MAX_FEEDS_TO_LOG]:  # Show first N
        feed_data = feed['feed_data']
        logger.info(f"  - {feed_data.get('feed_name')} ({feed_data.get('organization_name')})")
    if len(new_feeds) > MAX_FEEDS_TO_LOG:
        logger.info(f"  ... and {len(new_feeds) - MAX_FEEDS_TO_LOG} more")
    
    if all_realtime_feeds:
        logger.info(f"\nReal-time feeds created:")
        for rt_feed in all_realtime_feeds[:5]:  # Show first 5
            logger.info(f"  - {rt_feed['name']}")
        if len(all_realtime_feeds) > 5:
            logger.info(f"  ... and {len(all_realtime_feeds) - 5} more")
    
    if feeds_to_remove:
        logger.info(f"\nFeeds to remove from DMFR file:")
        for remove_feed in feeds_to_remove[:5]:  # Show first 5
            logger.info(f"  - {remove_feed.get('feed_name')} ({remove_feed.get('organization_name')})")
        if len(feeds_to_remove) > 5:
            logger.info(f"  ... and {len(feeds_to_remove) - 5} more")
    
    if discontinued_feeds:
        logger.info(f"\nDiscontinued feeds (not added to Transitland Atlas):")
        for disc_feed in discontinued_feeds[:5]:  # Show first 5
            logger.info(f"  - {disc_feed.get('feed_name')} ({disc_feed.get('organization_name')})")
        if len(discontinued_feeds) > 5:
            logger.info(f"  ... and {len(discontinued_feeds) - 5} more")
    
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
