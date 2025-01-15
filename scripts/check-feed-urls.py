import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, TypedDict, Literal
import logging
import string

logger = logging.getLogger('dmfr_validator')

MAX_FEEDS_TO_CHECK = 5

def setup_logging() -> None:
    """Configure the logger"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Validate DMFR feed URLs using transitland'
    )
    parser.add_argument('file_path', type=Path, help='Path to the DMFR JSON file')
    args: argparse.Namespace = parser.parse_args()
    
    if not args.file_path.exists():
        logger.error(
            f"File Not Found",
            extra={'code_block': f"Error: File not found: {args.file_path}"}
        )
        sys.exit(1)
        
    is_valid: bool = process_dmfr(args.file_path)
    sys.exit(0 if is_valid else 1)

class FeedUrls(TypedDict, total=False):
    """TypedDict for the urls field in a feed"""
    static_current: str
    static_historic: List[str]
    static_planned: List[str]
    static_hypothetical: List[str]
    realtime_vehicle_positions: str
    realtime_trip_updates: str
    realtime_alerts: str
    gbfs_auto_discovery: str
    mds_provider: str

class Authorization(TypedDict):
    """TypedDict for feed authorization"""
    type: Literal["header", "basic_auth", "query_param", "path_segment", "replace_url"]
    param_name: Optional[str]
    info_url: Optional[str]

class Feed(TypedDict):
    """TypedDict for a DMFR feed entry"""
    id: str
    spec: Literal["gtfs", "gtfs-rt", "gbfs", "mds"]
    urls: Union[FeedUrls, List[str]]  # List[str] for legacy format
    authorization: Optional[Authorization]
    name: Optional[str]
    description: Optional[str]

class DMFRFile(TypedDict):
    """TypedDict for the root DMFR file structure"""
    feeds: List[Feed]
    license_spdx_identifier: Optional[str]

def validate_feed_url(url: str, dmfr_path: str) -> bool:
    try:
        logger.info(f"Validating feed URL: {url}")
        result = subprocess.run(
            ["transitland", "validate", url],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False
        )
        
        if result.returncode != 0:
            output = ''.join(c for c in (result.stdout if result.stdout else result.stderr) if c in string.printable)
            logger.error(
                f"Validation failed for {url} in {dmfr_path}\n" +
                output
            )
            return False
            
        logger.info("Validation successful")
        return True
        
    except subprocess.SubprocessError as e:
        logger.error(f"Error running transitland validate\n{str(e)}")
        return False

def process_dmfr(file_path: Path) -> bool:
    """
    Process a DMFR JSON file and validate eligible feeds.
    Returns True if all validations pass, False if any fail.
    """
    try:
        with open(file_path) as f:
            data: DMFRFile = json.load(f)
            
        if not isinstance(data, dict) or 'feeds' not in data:
            logger.error(f"Invalid DMFR file\nError: {file_path} is not a valid DMFR file")
            return False
            
        feeds: List[Feed] = data['feeds']
        if not isinstance(feeds, list):
            logger.error("Invalid DMFR file\nError: 'feeds' must be a list")
            return False

        all_valid: bool = True
        feeds_checked = 0
        total_feeds = len(feeds)

        for feed in feeds:
            if feeds_checked >= MAX_FEEDS_TO_CHECK:
                logger.warning(
                    f"\nReached limit of {MAX_FEEDS_TO_CHECK} feeds to check. "
                    f"Skipping remaining {total_feeds - feeds_checked} feeds."
                )
                break

            # Skip feeds that have authentication
            if 'authorization' in feed:
                logger.info("Skipping feed\nFeed requires authentication")
                continue
                
            # Check for static_current URL
            urls: Union[FeedUrls, List[str]] = feed.get('urls', {})
            if isinstance(urls, dict) and 'static_current' in urls:
                feeds_checked += 1
                if not validate_feed_url(urls['static_current'], str(file_path)):
                    all_valid = False
            elif isinstance(urls, list) and urls:
                # Handle legacy format where urls is an array
                feeds_checked += 1
                if not validate_feed_url(urls[0], str(file_path)):
                    all_valid = False
                    
        return all_valid
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON Parse Error\nError: Invalid JSON in {file_path}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Processing Error\nError processing {file_path}: {str(e)}")
        return False

if __name__ == '__main__':
    setup_logging()
    main() 