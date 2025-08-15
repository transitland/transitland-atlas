#!/usr/bin/env -S uv run --script

# /// script
# dependencies = [
#   "requests>=2.31.0",
# ]
# ///

"""
Script to collect GTFS and GTFS-RT feeds from ODPT (Public Transportation Open Data Center)
and identify feeds to add to Transitland Atlas repo.

This script:
1. Discovers available GTFS feeds via the metadata API
2. Downloads GTFS files using the file download API
3. Checks which feeds are not already in the Transitland Atlas repo
4. Generates DMFR records for new feeds
5. Creates GTFS-RT feed records when real-time data is available
6. Outputs a summary of what should be added

Usage:
    # Run with uv (recommended)
    uv run collect-odpt-gtfs.py [--analysis]
    
    # Or with ODPT_ACCESS_TOKEN for full functionality
    ODPT_ACCESS_TOKEN=your_token uv run collect-odpt-gtfs.py [--analysis]
    
Options:
    --analysis    Generate detailed analysis file for debugging (default: False)
"""

import requests
import json
import logging
import argparse
import os
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from urllib.parse import urlparse
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
ODPT_METADATA_API = "https://members-portal.odpt.org/api/v1/resources"
ODPT_FILE_API_BASE = "https://api.odpt.org/api/v4/files/odpt"
ODPT_RT_API_BASE = "https://api.odpt.org/api/v4/gtfs/realtime"
REQUEST_TIMEOUT = 30  # seconds
MAX_FEEDS_TO_LOG = 10  # Number of feeds to show in summary

# Transitland Atlas repo paths
REPO_ROOT = Path(__file__).parent.parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"

class ODPTGTFSCollector:
    """Collector for ODPT GTFS feeds using the discovered APIs"""
    
    def __init__(self, access_token: str = None):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Transitland-Atlas-Collector/1.0'
        })
    
    def discover_gtfs_feeds(self) -> List[Dict]:
        """Discover all available GTFS feeds via the metadata API"""
        try:
            logger.info("Discovering GTFS feeds via ODPT metadata API...")
            
            # Get all GTFS feeds
            params = {
                'format': 'gtfs'
            }
            
            response = self.session.get(ODPT_METADATA_API, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Found {len(data)} organizations with GTFS data")
            
            # Flatten the data to get individual feeds
            all_feeds = []
            for org in data:
                for dataset in org.get('datasets', []):
                    if dataset.get('format_type') == 'GTFS/GTFS-JP':
                        feed_info = {
                            'organization': org,
                            'dataset': dataset,
                            'organization_id': org.get('label', ''),
                            'dataset_id': dataset.get('label', ''),
                            'name_ja': org.get('name_ja', ''),
                            'name_en': org.get('name_en', ''),
                            'url_ja': org.get('url_ja', ''),
                            'url_en': org.get('url_en', ''),
                            'license_type': dataset.get('license_type', ''),
                            'is_gtfsrt': dataset.get('is_gtfsrt', False),
                            'mode_list': dataset.get('mode_list', []),
                            'data_resources': dataset.get('dataresource', []),
                            'vehicle_position': dataset.get('vehicle_position'),
                            'trip_update': dataset.get('trip_update'),
                            'alert': dataset.get('alert')
                        }
                        all_feeds.append(feed_info)
            
            logger.info(f"Found {len(all_feeds)} GTFS feeds total")
            return all_feeds
            
        except requests.RequestException as e:
            logger.error(f"Failed to discover GTFS feeds: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse metadata API response: {e}")
            return []
    
    def get_gtfs_download_url(self, feed_info: Dict, include_access_token: bool = False) -> str:
        """Generate GTFS download URL for a specific feed using the private API endpoint"""
        organization_id = feed_info.get('organization_id', '')
        dataset_id = feed_info.get('dataset_id', '')
        
        if not organization_id or not dataset_id:
            return None
        
        # Always use the private API endpoint for stable URLs
        base_url = f"https://api.odpt.org/api/v4/files/odpt/{organization_id}/{dataset_id}.zip?date=current"
        
        # Include access token if explicitly requested (for testing)
        if include_access_token and self.access_token:
            base_url += f"&acl:consumerKey={self.access_token}"
        
        return base_url
    
    def get_gtfsrt_urls(self, feed_info: Dict, include_access_token: bool = False) -> Dict[str, str]:
        """Extract GTFS-RT URLs from feed information"""
        rt_urls = {}
        
        # Vehicle position
        vehicle_position = feed_info.get('vehicle_position')
        if vehicle_position and isinstance(vehicle_position, dict) and vehicle_position.get('is_operated'):
            url = vehicle_position.get('url', '')
            if include_access_token and self.access_token:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', self.access_token)
            else:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', '')
            rt_urls['realtime_vehicle_positions'] = url
        
        # Trip updates
        trip_update = feed_info.get('trip_update')
        if trip_update and isinstance(trip_update, dict) and trip_update.get('is_operated'):
            url = trip_update.get('url', '')
            if include_access_token and self.access_token:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', self.access_token)
            else:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', '')
            rt_urls['realtime_trip_updates'] = url
        
        # Alerts
        alert = feed_info.get('alert')
        if alert and isinstance(alert, dict) and alert.get('is_operated'):
            url = alert.get('url', '')
            if include_access_token and self.access_token:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', self.access_token)
            else:
                url = url.replace('[アクセストークン/YOUR_ACCESS_TOKEN]', '')
            rt_urls['realtime_alerts'] = url
        
        return rt_urls

class TransitlandAtlasAnalyzer:
    """Analyzer for Transitland Atlas repository"""
    
    def __init__(self, collector=None):
        self.feeds_dir = FEEDS_DIR
        self.existing_feeds: Set[str] = set()
        self.existing_urls: Set[str] = set()
        self.collector = collector
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
    
    def normalize_odpt_url(self, url: str) -> str:
        """Normalize ODPT URLs to handle api.odpt.org vs api-public.odpt.org variations"""
        if not url:
            return url
        
        # Strip protocol and make case-insensitive for comparison
        url_lower = url.lower()
        if url_lower.startswith('http://'):
            url_lower = url_lower[7:]  # Remove 'http://'
        elif url_lower.startswith('https://'):
            url_lower = url_lower[8:]  # Remove 'https://'
        
        # Remove query parameters
        url_lower = url_lower.split('?')[0]
        
        return url_lower
    
    def is_feed_new(self, feed_info: Dict) -> bool:
        """Check if a feed is new (not already in the repo)"""
        # Check if this feed already exists by URL pattern
        download_url = self.collector.get_gtfs_download_url(feed_info, include_access_token=False)
        if download_url:
            # Construct the expected URL pattern for this feed
            org_id = feed_info.get('organization_id', '')
            dataset_id = feed_info.get('dataset_id', '')
            expected_url_pattern = f"odpt/{org_id}/{dataset_id}".lower()
            
            # Check if any existing URL matches the pattern
            for existing_url in self.existing_urls:
                normalized_existing_url = self.normalize_odpt_url(existing_url)
                if expected_url_pattern in normalized_existing_url:
                    logger.info(f"Feed already exists with different URL format: {feed_info['name_ja']} ({org_id}/{dataset_id})")
                    logger.info(f"  Existing: {existing_url}")
                    logger.info(f"  New: {download_url}")
                    return False
        
        return True

class DMFRGenerator:
    """Generator for DMFR records from ODPT GTFS feeds"""
    
    @staticmethod
    def generate_feed_id(organization_id: str, dataset_id: str, suffix: str = "") -> str:
        """Generate a unique feed ID for ODPT feed"""
        # Clean the identifiers for use in Onestop ID
        org_clean = organization_id.lower().replace('_', '~').replace('-', '~')
        dataset_clean = dataset_id.lower().replace('_', '~').replace('-', '~')
        
        # Create ID with organization and dataset identifiers encoded
        base_id = f"f-{org_clean}~{dataset_clean}"
        if suffix:
            base_id += f"~{suffix}"
        return base_id
    
    @staticmethod
    def generate_operator_id(organization_id: str) -> str:
        """Generate a unique operator ID"""
        org_clean = organization_id.lower().replace('_', '~').replace('-', '~')
        return f"o-{org_clean}"
    
    @staticmethod
    def create_static_gtfs_record(feed_info: Dict, download_url: str, has_rt_feed: bool = False) -> Dict:
        """Create a DMFR record for a static GTFS feed"""
        organization_id = feed_info.get('organization_id', '')
        dataset_id = feed_info.get('dataset_id', '')
        name_ja = feed_info.get('name_ja', '')
        name_en = feed_info.get('name_en', '')
        license_type = feed_info.get('license_type', '')
        mode_list = feed_info.get('mode_list', [])
        
        if not organization_id or not dataset_id:
            logger.warning(f"Missing required fields for feed: org_id={organization_id}, dataset_id={dataset_id}")
            return None
        
        # Generate unique feed ID
        feed_onestop_id = DMFRGenerator.generate_feed_id(organization_id, dataset_id, "jp")
        rt_feed_onestop_id = DMFRGenerator.generate_feed_id(organization_id, dataset_id, "jp~rt")
        
        # Create DMFR record
        dmfr_record = {
            "id": feed_onestop_id,
            "spec": "gtfs",
            "urls": {
                "static_current": download_url
            },
            "authorization": {
              "type": "query_param",
              "param_name": "acl:consumerKey",
              "info_url": "https://developer.odpt.org/"
            }
        }
        
        # Add license
        match license_type:
            case "CC BY 4.0":
                dmfr_record["license"] = {
                    "spdx_identifier": "CC-BY-4.0"
                }
            case "CC0":
                dmfr_record["license"] = {
                    "spdx_identifier": "CC0-1.0"
                }
            case "ODPT基本":
                dmfr_record["license"] = {
                    "url": "https://developer.odpt.org/terms/data_basic_license.html"
                }
        
        # Note: Operators are created at the top level, not per feed
        # This prevents duplication and ensures proper feed associations
        
        return dmfr_record
    
    @staticmethod
    def create_realtime_gtfs_record(feed_info: Dict, rt_urls: Dict[str, str]) -> Dict:
        """Create a DMFR record for a GTFS-RT feed"""
        organization_id = feed_info.get('organization_id', '')
        dataset_id = feed_info.get('dataset_id', '')
        name_ja = feed_info.get('name_ja', '')
        name_en = feed_info.get('name_en', '')
        
        if not organization_id or not dataset_id:
            logger.warning(f"Missing required fields for feed: org_id={organization_id}, dataset_id={dataset_id}")
            return None
        
        # Generate unique feed ID
        feed_onestop_id = DMFRGenerator.generate_feed_id(organization_id, dataset_id, "jp~rt")
        
        # Create DMFR record
        dmfr_record = {
            "id": feed_onestop_id,
            "spec": "gtfs-rt",
            "urls": rt_urls,
            "tags": {
                "odpt_organization_id": organization_id,
                "odpt_dataset_id": dataset_id,
            }
        }
        
        return dmfr_record

def main():
    """Main function to collect and analyze ODPT GTFS feeds"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Collect ODPT GTFS feeds for Transitland Atlas')
    parser.add_argument('--analysis', action='store_true', 
                       help='Generate detailed analysis file for debugging')
    parser.add_argument('--include-access-token', action='store_true', 
                       help='Include access token in download URLs (recommended for stable private API URLs)')
    args = parser.parse_args()
    
    # Get access token (optional for metadata discovery, required for file downloads)
    access_token = os.environ.get('ODPT_ACCESS_TOKEN')
    if not access_token:
        logger.warning("ODPT_ACCESS_TOKEN not set - will only discover feeds, cannot generate download URLs")
        logger.warning("Set ODPT_ACCESS_TOKEN for full functionality including file downloads")
    else:
        logger.info("ODPT_ACCESS_TOKEN found - will generate stable private API URLs")
    
    logger.info("Starting ODPT GTFS data collection process...")
    
    # Initialize components
    collector = ODPTGTFSCollector(access_token)
    analyzer = TransitlandAtlasAnalyzer(collector)
    
    # Discover available GTFS feeds
    feeds = collector.discover_gtfs_feeds()
    
    if not feeds:
        logger.error("No GTFS feeds found. Exiting.")
        return
    
    # Process feeds and generate DMFR records
    new_static_feeds = []
    new_rt_feeds = []
    existing_feeds = []
    
    # Only include access token if explicitly requested
    should_include_token = args.include_access_token
    
    for feed_info in feeds:
        if analyzer.is_feed_new(feed_info):
            # Generate static GTFS record
            download_url = collector.get_gtfs_download_url(
                feed_info,
                include_access_token=should_include_token
            )
            
            # Check if this feed has real-time data
            has_rt_feed = feed_info.get('is_gtfsrt', False)
            static_dmfr_record = DMFRGenerator.create_static_gtfs_record(feed_info, download_url, has_rt_feed)
            if static_dmfr_record:
                new_static_feeds.append({
                    'feed_info': feed_info,
                    'dmfr_record': static_dmfr_record,
                    'download_url': download_url
                })
                logger.info(f"New GTFS feed: {feed_info['name_ja']} ({feed_info['organization_id']}/{feed_info['dataset_id']})")
            
            # Generate GTFS-RT record if available
            if feed_info.get('is_gtfsrt'):
                rt_urls = collector.get_gtfsrt_urls(feed_info, should_include_token)
                if rt_urls:
                    rt_dmfr_record = DMFRGenerator.create_realtime_gtfs_record(feed_info, rt_urls)
                    if rt_dmfr_record:
                        new_rt_feeds.append({
                            'feed_info': feed_info,
                            'dmfr_record': rt_dmfr_record,
                            'rt_urls': rt_urls
                        })
                        logger.info(f"New GTFS-RT feed: {feed_info['name_ja']} ({feed_info['organization_id']}/{feed_info['dataset_id']})")
        else:
            existing_feeds.append(feed_info)
            logger.debug(f"Existing feed: {feed_info['name_ja']} ({feed_info['organization_id']}/{feed_info['dataset_id']})")
    
    # Generate output
    output = {
        'summary': {
            'total_feeds_found': len(feeds),
            'new_static_feeds': len(new_static_feeds),
            'new_rt_feeds': len(new_rt_feeds),
            'existing_feeds': len(existing_feeds),
            'scraped_at': datetime.now(timezone.utc).isoformat()
        },
        'new_static_feeds': new_static_feeds,
        'new_rt_feeds': new_rt_feeds,
        'existing_feeds': existing_feeds,
        'all_feeds': feeds
    }
    
    # Save detailed output
    output_file = Path(__file__).parent / "odpt-gtfs-analysis.json"
    if args.analysis:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Detailed analysis saved to: {output_file}")
    else:
        logger.info("Skipping detailed analysis file generation.")
    
    # Generate DMFR file for new feeds
    if new_static_feeds or new_rt_feeds:
        # Create top-level operators to associate feeds
        operators = []
        
        # First, collect all feed IDs that were actually created
        created_feed_ids = set()
        for feed in new_static_feeds:
            created_feed_ids.add(feed['dmfr_record']['id'])
        for feed in new_rt_feeds:
            created_feed_ids.add(feed['dmfr_record']['id'])
        
        for feed in new_static_feeds:
            feed_info = feed['feed_info']
            organization_id = feed_info.get('organization_id', '')
            
            if organization_id:
                operator_id = DMFRGenerator.generate_operator_id(organization_id)
                
                # Check if operator already exists
                existing_operator = next((op for op in operators if op['onestop_id'] == operator_id), None)
                
                if existing_operator:
                    # Add this feed to existing operator
                    existing_operator['associated_feeds'].append({
                        'feed_onestop_id': feed['dmfr_record']['id']
                    })
                    
                    # Add RT feed if it exists and was actually created
                    rt_feed = next((rt for rt in new_rt_feeds 
                                  if rt['feed_info']['organization_id'] == organization_id 
                                  and rt['feed_info']['dataset_id'] == feed_info['dataset_id']), None)
                    if rt_feed and rt_feed['dmfr_record']['id'] in created_feed_ids:
                        existing_operator['associated_feeds'].append({
                            'feed_onestop_id': rt_feed['dmfr_record']['id']
                        })
                else:
                    # Create new operator
                    associated_feeds = [{'feed_onestop_id': feed['dmfr_record']['id']}]
                    
                    # Add RT feed if it exists and was actually created
                    rt_feed = next((rt for rt in new_rt_feeds 
                                  if rt['feed_info']['organization_id'] == organization_id 
                                  and rt['feed_info']['dataset_id'] == feed_info['dataset_id']), None)
                    if rt_feed and rt_feed['dmfr_record']['id'] in created_feed_ids:
                        associated_feeds.append({'feed_onestop_id': rt_feed['dmfr_record']['id']})
                    
                    operator_record = {
                        'onestop_id': operator_id,
                        'name': feed_info.get('name_ja', '') or organization_id,
                        'associated_feeds': associated_feeds
                    }
                    
                    # Only add short_name if it exists
                    if feed_info.get('name_en'):
                        operator_record['short_name'] = feed_info.get('name_en')
                    
                    # Only add website if it exists
                    website = feed_info.get('url_ja') or feed_info.get('url_en')
                    if website:
                        operator_record['website'] = website
                    
                    operators.append(operator_record)
        
        dmfr_output = {
            "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.6.0.json",
            "feeds": [feed['dmfr_record'] for feed in new_static_feeds + new_rt_feeds],
            "operators": operators
        }
        
        dmfr_file = FEEDS_DIR / 'odpt-gtfs.dmfr.json'
        with open(dmfr_file, 'w', encoding='utf-8') as f:
            json.dump(dmfr_output, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Generated DMFR file with {len(new_static_feeds)} GTFS feeds, {len(new_rt_feeds)} GTFS-RT feeds, and {len(operators)} operators: {dmfr_file}")
        
        # Format the DMFR file using transitland CLI
        try:
            import subprocess
            logger.info("Formatting DMFR file with transitland CLI...")
            result = subprocess.run(
                ['transitland', 'dmfr', 'format', '--save', str(dmfr_file)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT
            )
            if result.returncode == 0:
                logger.info("DMFR file successfully formatted and saved")
            else:
                logger.warning(f"DMFR formatting failed: {result.stderr}")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.warning(f"Could not run transitland CLI to format DMFR file: {e}")
            logger.info("DMFR file generated but not formatted - run 'transitland dmfr format --save feeds/odpt-gtfs.dmfr.json' manually")
    else:
        logger.info("No new feeds to add to DMFR file")
    
    # Print summary
    logger.info("=" * 50)
    logger.info("ODPT GTFS DATA COLLECTION SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total feeds found: {len(feeds)}")
    logger.info(f"New GTFS feeds to add: {len(new_static_feeds)}")
    logger.info(f"New GTFS-RT feeds to add: {len(new_rt_feeds)}")
    logger.info(f"Existing feeds (already in repo): {len(existing_feeds)}")
    
    if new_static_feeds:
        logger.info("\nNew GTFS feeds to consider adding:")
        for feed in new_static_feeds[:MAX_FEEDS_TO_LOG]:
            feed_info = feed['feed_info']
            logger.info(f"  - {feed_info['name_ja']} ({feed_info['organization_id']}/{feed_info['dataset_id']})")
        if len(new_static_feeds) > MAX_FEEDS_TO_LOG:
            logger.info(f"  ... and {len(new_static_feeds) - MAX_FEEDS_TO_LOG} more")
    
    if new_rt_feeds:
        logger.info(f"\nNew GTFS-RT feeds to consider adding:")
        for feed in new_rt_feeds[:MAX_FEEDS_TO_LOG]:
            feed_info = feed['feed_info']
            logger.info(f"  - {feed_info['name_ja']} ({feed_info['organization_id']}/{feed_info['dataset_id']})")
        if len(new_rt_feeds) > MAX_FEEDS_TO_LOG:
            logger.info(f"  ... and {len(new_rt_feeds) - MAX_FEEDS_TO_LOG} more")
    
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
