#!/usr/bin/env python3

"""
Convert feed Onestop IDs to lowercase in DMFR files.

This script will:
1. Find all .dmfr.json files in the ./feeds directory
2. For each feed with capital letters in its Onestop ID:
   - Convert the ID to lowercase
   - Add the old ID to supersedes_ids
3. Save the updated files with consistent formatting

WARNINGS:

- It's up to you to update any references to the old ID in associated_feeds arrays
- This script doesn't look at operator Onestop IDs
- After running the script, don't forget to apply the opinionated DMFR format


Usage:
    python3 scripts/debug/lowercase_feed_onestop_ids.py
    gfind ./feeds -type f -name "*.dmfr.json" -exec transitland dmfr format --save {} \;
"""

import json
import os
import glob
from pathlib import Path

def load_dmfr_file(file_path):
    """Load a DMFR JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_dmfr_file(file_path, data):
    """Save a DMFR JSON file with consistent formatting."""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')  # Add trailing newline

def process_feed(feed):
    """Process a single feed, converting its ID to lowercase if needed."""
    if not feed.get('id'):
        return feed
    
    old_id = feed['id']
    new_id = old_id.lower()
    
    if old_id != new_id:
        # Create supersedes_ids array if it doesn't exist
        if 'supersedes_ids' not in feed:
            feed['supersedes_ids'] = []
        
        # Add old ID to supersedes_ids if not already present
        if old_id not in feed['supersedes_ids']:
            feed['supersedes_ids'].append(old_id)
        
        # Update the ID to lowercase
        feed['id'] = new_id
    
    return feed

def process_dmfr_file(file_path):
    """Process a single DMFR file."""
    print(f"Processing {file_path}")
    
    # Load the DMFR file
    data = load_dmfr_file(file_path)
    
    # Process all feeds
    if 'feeds' in data:
        data['feeds'] = [process_feed(feed) for feed in data['feeds']]
    
    # Save the updated file
    save_dmfr_file(file_path, data)
    print(f"Updated {file_path}")

def main():
    # Get all DMFR files in the feeds directory
    feeds_dir = Path('feeds')
    dmfr_files = glob.glob(str(feeds_dir / '*.dmfr.json'))
    
    # Process each file
    for file_path in dmfr_files:
        process_dmfr_file(file_path)

if __name__ == '__main__':
    main()
