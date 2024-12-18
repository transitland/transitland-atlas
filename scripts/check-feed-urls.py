import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any

def validate_feed_url(url: str, dmfr_path: str) -> bool:
    """
    Run transitland validate command for a given URL.
    Returns True if validation succeeds, False otherwise.
    """
    try:
        print(f"Validating feed URL: {url}")
        result = subprocess.run(
            ["transitland", "validate", url],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            print(f"Validation failed for {url} in {dmfr_path}")
            print("Error output:", result.stderr)
            return False
            
        print("Validation successful")
        return True
        
    except subprocess.SubprocessError as e:
        print(f"Error running transitland validate: {str(e)}")
        return False

def process_dmfr(file_path: Path) -> bool:
    """
    Process a DMFR JSON file and validate eligible feeds.
    Returns True if all validations pass, False if any fail.
    """
    try:
        with open(file_path) as f:
            data = json.load(f)
            
        if not isinstance(data, dict) or 'feeds' not in data:
            print(f"Error: {file_path} is not a valid DMFR file")
            return False
            
        feeds = data['feeds']
        if not isinstance(feeds, list):
            print(f"Error: 'feeds' must be a list")
            return False

        all_valid = True
        for feed in feeds:
            # Skip feeds that have authentication
            if any(key in feed for key in ['authorization', 'authorization_type', 'api_key']):
                print(f"Skipping feed (requires authentication)")
                continue
                
            # Check for static_current URL
            urls = feed.get('urls', {})
            if isinstance(urls, dict) and 'static_current' in urls:
                if not validate_feed_url(urls['static_current'], str(file_path)):
                    all_valid = False
            elif isinstance(urls, list) and urls:
                # Handle legacy format where urls is an array
                if not validate_feed_url(urls[0], str(file_path)):
                    all_valid = False
                    
        return all_valid
        
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {file_path}: {str(e)}")
        return False
    except Exception as e:
        print(f"Error processing {file_path}: {str(e)}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Validate DMFR feed URLs using transitland')
    parser.add_argument('file_path', type=Path, help='Path to the DMFR JSON file')
    args = parser.parse_args()
    
    if not args.file_path.exists():
        print(f"Error: File not found: {args.file_path}")
        sys.exit(1)
        
    is_valid = process_dmfr(args.file_path)
    sys.exit(0 if is_valid else 1)

if __name__ == '__main__':
    main() 