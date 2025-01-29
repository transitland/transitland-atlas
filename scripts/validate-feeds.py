import subprocess
import sys
import os
import sqlite3
import json
from contextlib import contextmanager

# Constants
DB_FILENAME = 'feed-validation.db'
FEED_PREFIX = 'f'
OPERATOR_PREFIX = 'o'
FEED_GLOB = '../feeds/*.dmfr.json'

# URL types that must be unique
URL_TYPES = [
    'static_current',
    'realtime_vehicle_positions',
    'realtime_trip_updates',
    'realtime_alerts',
    'gbfs_auto_discovery',
    'mds_provider'
]

ARRAY_URL_TYPES = [
    'static_planned',
    'static_hypothetical'
]

def validate_onestop_id(osid, prefix, entity_type):
    """
    Validate a Onestop ID format.
    
    Args:
        osid: The Onestop ID to validate
        prefix: Expected prefix ('f' for feeds, 'o' for operators)
        entity_type: Type of entity for error messages
    
    Returns:
        bool: True if valid, False if invalid
    """
    if not osid or not isinstance(osid, str):
        print(f"ERROR: {entity_type} Onestop ID is empty or invalid")
        return False
    if not osid.startswith(prefix):
        print(f"ERROR: {entity_type} Onestop ID must start with '{prefix}': {osid}")
        return False
    parts = osid.split('-')
    if len(parts) < 2 or len(parts) > 3 or '' in parts:
        print(f"ERROR: {entity_type} Onestop ID must have 2-3 parts separated by hyphens: {osid}")
        return False
    return True

def check_url_duplicates(cursor, url_type, is_array=False, warn_only=False):
    try:
        cursor.execute(f'''
            SELECT json_extract(urls, '$.{url_type}') from current_feeds
        ''')
        results = cursor.fetchall()
        
        if is_array:
            all_urls = []
            for result in results:
                if result[0]:  # if the array exists
                    urls = json.loads(result[0])
                    all_urls.extend(urls)
        else:
            all_urls = list(filter(None, [url_match[0] for url_match in results]))
        
        duplicate_urls = set([x for x in all_urls if all_urls.count(x) > 1])
        if len(duplicate_urls) > 0:
            msg_type = "WARNING" if warn_only else "ERROR"
            print(f"{msg_type}: more than one feed has the same value defined for urls.{url_type}:")
            for url in duplicate_urls:
                print(f"  - {url}")
            return not warn_only  # Return True if should fail build
        return False
    except (sqlite3.Error, json.JSONDecodeError) as e:
        print(f"ERROR: Failed to check URLs for {url_type}: {e}")
        return True

def execute_query(cursor, query, params=()):
    """Execute SQL query with error handling"""
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"ERROR: Database query failed: {query}")
        print(f"Error details: {e}")
        raise

@contextmanager
def database_connection(filename):
    """Context manager for database connections"""
    conn = None
    try:
        conn = sqlite3.connect(filename)
        yield conn
    finally:
        if conn:
            conn.close()

def log_error(message, context=None):
    """Log error messages with context"""
    error = {"error": message}
    if context:
        error.update(context)
    print(json.dumps(error))

def main():
    fail_the_build = False
    db_conn = None

    try:
        # load dmfr to database
        os.system(f"rm -f {DB_FILENAME}")
        sync_command = f"transitland dmfr sync --hide-unseen --hide-unseen-operators -dburl=sqlite3://{DB_FILENAME} {FEED_GLOB}"

        try:
            sync_log = subprocess.check_output(sync_command, shell=True)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to sync DMFR files: {e}")
            return 1

        for log_line in sync_log.splitlines():
            log_line = log_line.decode("utf-8")
            if log_line.find("updated feed") > -1:
                print(f"ERROR: duplicate feed found at: {log_line}")
                fail_the_build = True

        with database_connection(DB_FILENAME) as db_conn:
            c = db_conn.cursor()

            # Check feed onestop_id uniqueness and format
            for row in execute_query(c, 'SELECT onestop_id from current_feeds'):
                osid = row[0] or ''
                if not validate_onestop_id(osid, FEED_PREFIX, 'Feed'):
                    fail_the_build = True

            # Check URL uniqueness
            for url_type in URL_TYPES:
                if check_url_duplicates(c, url_type):
                    fail_the_build = True

            for url_type in ARRAY_URL_TYPES:
                if check_url_duplicates(c, url_type, is_array=True):
                    fail_the_build = True

            # Check static_historic URLs - warn only
            check_url_duplicates(c, 'static_historic', is_array=True, warn_only=True)

            # Check operator onestop_id uniqueness and format
            for row in execute_query(c, 'SELECT onestop_id from current_operators'):
                osid = row[0] or ''
                if not validate_onestop_id(osid, OPERATOR_PREFIX, 'Operator'):
                    fail_the_build = True

            # Check associated_feeds[].feed_onestop_id format
            for operator_onestop_id, associated_feeds_json in execute_query(c, 'SELECT onestop_id, associated_feeds from current_operators'):
                if operator_onestop_id is None or associated_feeds_json is None:
                    continue
                associated_feeds = json.loads(associated_feeds_json)
                for associated_feed in associated_feeds:
                    feed_onestop_id = associated_feed.get('feed_onestop_id')
                    if not feed_onestop_id:
                        print(f"ERROR: missing feed Onestop ID in the associated_feeds block for operator {operator_onestop_id}")
                        fail_the_build = True
                        continue
                    if not validate_onestop_id(feed_onestop_id, FEED_PREFIX, 'Feed'):
                        print(f"ERROR: in the associated_feeds block for operator {operator_onestop_id}")
                        fail_the_build = True

        return 1 if fail_the_build else 0

    except sqlite3.Error as e:
        print(f"ERROR: Database operation failed: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())