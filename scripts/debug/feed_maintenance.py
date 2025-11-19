#!/usr/bin/env python3
"""
Feed maintenance script to identify feeds that may be out of date and require
manual research to find new URLs. Fetches feed data and analyzes update cadence.
"""
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "pandas",
# ]
# ///

import os
import csv
import argparse
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import requests
import pandas as pd


def fetch_graphql(url: str, query: str, params: Optional[Dict] = None, key: str = None, apikey: str = None, max_retries: int = 3):
    """Fetch paginated GraphQL results with retry logic."""
    after = 0
    params = params or {}
    
    while True:
        print(f"Fetching... after: {after}")
        p = {**params, 'after': after}
        
        # Retry logic for timeouts and server errors
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    url,
                    headers={'apikey': apikey} if apikey else {},
                    json={"query": query, "variables": p},
                    timeout=(30, 120)  # 30s connect, 120s read timeout
                )
                
                # Check HTTP status and show detailed error for non-200 responses
                if response.status_code == 200:
                    break  # Success, exit retry loop
                elif response.status_code in (502, 503, 504):  # Server errors - retry
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        print(f"  Server error {response.status_code}, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        # Last attempt failed
                        error_msg = f"HTTP {response.status_code} Error after {max_retries} attempts"
                        try:
                            error_body = response.json()
                            if isinstance(error_body, dict):
                                if 'message' in error_body:
                                    error_msg += f": {error_body['message']}"
                        except:
                            error_msg += f"\nResponse text: {response.text[:1000]}"
                        raise RuntimeError(error_msg)
                else:
                    # Other HTTP errors - don't retry
                    error_msg = f"HTTP {response.status_code} Error"
                    try:
                        error_body = response.json()
                        if isinstance(error_body, dict):
                            if 'errors' in error_body:
                                errors = error_body['errors']
                                error_msg += f": {errors}"
                            elif 'message' in error_body:
                                error_msg += f": {error_body['message']}"
                            else:
                                error_msg += f": {error_body}"
                        else:
                            error_msg += f": {error_body}"
                    except:
                        error_msg += f"\nResponse text: {response.text[:1000]}"
                    raise RuntimeError(error_msg)
                    
            except (requests.exceptions.Timeout, requests.exceptions.ReadTimeout) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"  Request timeout, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    raise RuntimeError(f"Request timeout after {max_retries} attempts: {e}")
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"  Request error: {e}, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
        
        # Parse JSON
        try:
            json_data = response.json()
        except ValueError as e:
            raise ValueError(f"Invalid JSON response: {e}\nResponse text: {response.text[:500]}")
        
        # Check for GraphQL errors
        if 'errors' in json_data:
            errors = json_data['errors']
            error_msg = '\n'.join(str(e) for e in errors)
            raise RuntimeError(f"GraphQL errors: {error_msg}")
        
        # Extract data
        data = json_data.get('data', {}).get(key, [])
        
        for ent in data:
            after = ent.get('id')
            yield ent
        
        if len(data) == 0:
            break


FEED_QUERY = """
query($after:Int) {
  feeds(after:$after, limit:100) {
    id
    onestop_id
    spec
    urls {
        static_current
        realtime_vehicle_positions
        realtime_trip_updates
        realtime_alerts
    }
    tags
    feed_state {
        feed_version {
            agencies {
                id
                agency_id
                agency_name
                places {
                    rank
                    adm0_name
                    adm1_name
                }
            }
        }
    }
    feed_fetches(limit: 1) {
      id
      fetch_error
      fetched_at
      response_code
      response_sha1
      response_size
      success
      url
      url_type
    }
    feed_fetches_ok: feed_fetches(limit: 1, where: {success: true}) {
      id
      fetch_error
      fetched_at
      response_code
      response_sha1
      response_size
      success
      url
      url_type
    }
    feed_versions(limit: 10) {
      id
      fetched_at
      earliest_calendar_date
      latest_calendar_date
      sha1
      url
    }
  }
}
"""


def first(v):
    """Get first element of list or None."""
    return v[0] if v and len(v) > 0 else None


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def calculate_cadence(feed_versions: List[Dict], absolute_threshold_days: int = 270) -> Dict[str, Any]:
    """Calculate update cadence from feed versions.
    
    Args:
        feed_versions: List of feed version dictionaries
        absolute_threshold_days: Days threshold for "absolutely out of date" (default: 270)
    """
    if not feed_versions or len(feed_versions) < 2:
        # Even with no versions, check if we have a last update date
        days_since_last = None
        absolutely_out_of_date = False
        if feed_versions and len(feed_versions) == 1:
            last_dt = parse_datetime(feed_versions[0].get('fetched_at'))
            if last_dt:
                now = datetime.now(timezone.utc)
                days_since_last = (now - last_dt).total_seconds() / 86400
                absolutely_out_of_date = days_since_last > absolute_threshold_days
        
        return {
            'cadence_analysis_success': False,
            'feed_versions_count': len(feed_versions) if feed_versions else 0,
            'typical_update_interval_days': None,
            'days_since_last_update': round(days_since_last, 1) if days_since_last is not None else None,
            'expected_next_update': None,
            'outside_expected_window': False,
            'absolutely_out_of_date': absolutely_out_of_date,
        }
    
    # Parse timestamps
    timestamps = []
    for fv in feed_versions:
        dt = parse_datetime(fv.get('fetched_at'))
        if dt:
            timestamps.append(dt)
    
    if len(timestamps) < 2:
        # Still check if absolutely out of date
        days_since_last = None
        absolutely_out_of_date = False
        if timestamps:
            last_update = timestamps[-1]
            now = datetime.now(timezone.utc)
            days_since_last = (now - last_update).total_seconds() / 86400
            absolutely_out_of_date = days_since_last > absolute_threshold_days
        
        return {
            'cadence_analysis_success': False,
            'feed_versions_count': len(feed_versions),
            'typical_update_interval_days': None,
            'days_since_last_update': round(days_since_last, 1) if days_since_last is not None else None,
            'expected_next_update': None,
            'outside_expected_window': False,
            'absolutely_out_of_date': absolutely_out_of_date,
        }
    
    # Sort by date (oldest first)
    timestamps.sort()
    
    # Calculate intervals between consecutive updates
    intervals = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i-1]).total_seconds() / 86400  # days
        if delta > 0:
            intervals.append(delta)
    
    if not intervals:
        # Still check if absolutely out of date
        days_since_last = None
        absolutely_out_of_date = False
        if timestamps:
            last_update = timestamps[-1]
            now = datetime.now(timezone.utc)
            days_since_last = (now - last_update).total_seconds() / 86400
            absolutely_out_of_date = days_since_last > absolute_threshold_days
        
        return {
            'cadence_analysis_success': False,
            'feed_versions_count': len(feed_versions),
            'typical_update_interval_days': None,
            'days_since_last_update': round(days_since_last, 1) if days_since_last is not None else None,
            'expected_next_update': None,
            'outside_expected_window': False,
            'absolutely_out_of_date': absolutely_out_of_date,
        }
    
    # Calculate statistics
    typical_interval = pd.Series(intervals).median()
    last_update = timestamps[-1]
    now = datetime.now(timezone.utc)
    days_since_last = (now - last_update).total_seconds() / 86400
    expected_next = last_update.timestamp() + (typical_interval * 86400)
    expected_next_dt = datetime.fromtimestamp(expected_next, tz=timezone.utc)
    
    # Determine if outside expected window
    # Consider outside window if: more than 1.5x typical interval has passed AND at least 7 days
    outside_window = days_since_last > max(typical_interval * 1.5, 7)
    
    # Determine if absolutely out of date (regardless of cadence)
    absolutely_out_of_date = days_since_last > absolute_threshold_days
    
    return {
        'cadence_analysis_success': True,
        'feed_versions_count': len(feed_versions),
        'typical_update_interval_days': round(typical_interval, 1),
        'days_since_last_update': round(days_since_last, 1),
        'expected_next_update': expected_next_dt.isoformat(),
        'outside_expected_window': outside_window,
        'absolutely_out_of_date': absolutely_out_of_date,
    }


def process_feeds(endpoint: str, apikey: str, outfile: str, include_gtfs_rt: bool = False, absolute_threshold_days: int = 270):
    """Fetch feeds and write to CSV with cadence analysis.
    
    Args:
        endpoint: Transitland API endpoint
        apikey: API key
        outfile: Output CSV file path
        include_gtfs_rt: If True, include GTFS-RT feeds (default: False, only GTFS)
        absolute_threshold_days: Days threshold for "absolutely out of date" (default: 270)
    """
    tags = ['status', 'unstable_url', 'manual_import']
    places_count = 5
    
    keys = [
        'onestop_id',
        'spec',
        'urls.static_current',
        'urls.realtime_vehicle_positions',
        'urls.realtime_trip_updates',
        'urls.realtime_alerts',
        'agencies',
        'last_fetch_at',
        'last_fetch_success',
        'last_fetch_response_code',
        'last_ok_fetch_at',
        'last_ok_fetch_success',
        'last_ok_fetch_response_code',
        'most_recent_feed_version_sha1',
        'most_recent_feed_version_fetched_at',
        'feed_versions_count',
        'typical_update_interval_days',
        'days_since_last_update',
        'expected_next_update',
        'outside_expected_window',
        'absolutely_out_of_date',
        'cadence_analysis_success',
    ]
    for tag in tags:
        keys.append(f'tag_{tag}')
    for i in range(places_count):
        keys.append(f'adm0_name{i}')
        keys.append(f'adm1_name{i}')
    
    rows = []
    skipped_count = 0
    for ent in fetch_graphql(f"{endpoint}/query", FEED_QUERY, {}, key='feeds', apikey=apikey):
        spec = ent.get('spec', '')
        
        # Filter: Skip GBFS, only include GTFS (and optionally GTFS-RT)
        if spec == 'GBFS':
            skipped_count += 1
            continue
        if spec not in ('GTFS', 'GTFS_RT'):
            skipped_count += 1
            continue
        if spec == 'GTFS_RT' and not include_gtfs_rt:
            skipped_count += 1
            continue
        
        row = {}
        row['onestop_id'] = ent.get('onestop_id')
        row['spec'] = spec
        
        urls = ent.get('urls', {})
        row['urls.static_current'] = urls.get('static_current')
        row['urls.realtime_vehicle_positions'] = urls.get('realtime_vehicle_positions')
        row['urls.realtime_trip_updates'] = urls.get('realtime_trip_updates')
        row['urls.realtime_alerts'] = urls.get('realtime_alerts')
        
        # Process feed state and agencies
        feed_state_fv = (ent.get('feed_state') or {}).get('feed_version')
        if feed_state_fv:
            agencies = [a.get('agency_name') for a in (feed_state_fv.get('agencies') or [])]
            row['agencies'] = ', '.join(agencies)
            
            # Process places ordered by rank
            check_places = {}
            for agency in (feed_state_fv.get('agencies') or []):
                for place in (agency.get('places') or []):
                    key = (place.get('adm0_name'), place.get('adm1_name'))
                    if key[0] or key[1]:  # Only add if not both None
                        check_places[key] = place.get('rank', 0)
            
            ranked_places = sorted(check_places.items(), reverse=True, key=lambda x: x[1])
            for i, (place, _) in enumerate(ranked_places[:places_count]):
                row[f'adm0_name{i}'] = place[0]
                row[f'adm1_name{i}'] = place[1]
        
        # Process feed fetches
        if first(ent.get('feed_fetches')):
            fetch = first(ent.get('feed_fetches'))
            row['last_fetch_at'] = fetch.get('fetched_at')
            row['last_fetch_success'] = fetch.get('success')
            row['last_fetch_response_code'] = fetch.get('response_code')
        
        if first(ent.get('feed_fetches_ok')):
            fetch = first(ent.get('feed_fetches_ok'))
            row['last_ok_fetch_at'] = fetch.get('fetched_at')
            row['last_ok_fetch_success'] = fetch.get('success')
            row['last_ok_fetch_response_code'] = fetch.get('response_code')
        
        # Process feed versions and cadence
        feed_versions = ent.get('feed_versions', [])
        # Sort by fetched_at descending to ensure most recent first
        feed_versions.sort(
            key=lambda fv: parse_datetime(fv.get('fetched_at')) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        if first(feed_versions):
            fv = first(feed_versions)
            row['most_recent_feed_version_sha1'] = fv.get('sha1')
            row['most_recent_feed_version_fetched_at'] = fv.get('fetched_at')
        elif first(ent.get('feed_fetches_ok')):
            # Fallback: use last successful fetch if no feed versions
            fetch = first(ent.get('feed_fetches_ok'))
            row['most_recent_feed_version_fetched_at'] = fetch.get('fetched_at')
        
        # Calculate cadence
        cadence = calculate_cadence(feed_versions, absolute_threshold_days=absolute_threshold_days)
        row.update(cadence)
        
        # If no feed versions but we have a fetch date, check if absolutely out of date
        if not feed_versions and row.get('most_recent_feed_version_fetched_at'):
            last_dt = parse_datetime(row['most_recent_feed_version_fetched_at'])
            if last_dt:
                now = datetime.now(timezone.utc)
                days_since_last = (now - last_dt).total_seconds() / 86400
                row['days_since_last_update'] = round(days_since_last, 1)
                row['absolutely_out_of_date'] = days_since_last > absolute_threshold_days
        
        # Process tags
        feed_tags = ent.get('tags') or {}
        for tag in tags:
            row[f'tag_{tag}'] = feed_tags.get(tag)
        
        rows.append(row)
    
    # Write CSV
    with open(outfile, 'w', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    
    if skipped_count > 0:
        print(f"Skipped {skipped_count} feeds (GBFS or other non-GTFS specs)")
    
    return rows


def analyze_results(rows: List[Dict]):
    """Analyze and print results."""
    df = pd.DataFrame(rows)
    
    # Fill NaN for boolean columns
    df['outside_expected_window'] = df['outside_expected_window'].fillna(False)
    df['absolutely_out_of_date'] = df['absolutely_out_of_date'].fillna(False)
    
    print(f"\nTotal feeds analyzed: {len(df)}")
    print(f"Feeds with successful cadence analysis: {df['cadence_analysis_success'].sum()}")
    print(f"Feeds outside expected window: {df['outside_expected_window'].sum()}")
    print(f"Feeds absolutely out of date (>270 days): {df['absolutely_out_of_date'].sum()}")
    print()
    
    # Feeds absolutely out of date (highest priority)
    absolutely_out_of_date = df[df['absolutely_out_of_date'] == True].copy()
    
    if len(absolutely_out_of_date) > 0:
        print("=== ⚠️  FEEDS ABSOLUTELY OUT OF DATE (>270 DAYS) ===")
        print("These feeds haven't been updated in 270+ days and need immediate attention:\n")
        
        # Sort by days since last update (most stale first)
        absolutely_out_of_date = absolutely_out_of_date.sort_values(
            'days_since_last_update', 
            ascending=False, 
            na_position='last'
        )
        
        for _, feed in absolutely_out_of_date.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Webpage: https://www.transit.land/feeds/{feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Agencies: {feed['agencies']}")
            print(f"  Last update: {feed['most_recent_feed_version_fetched_at']}")
            print(f"  Days since last update: {feed['days_since_last_update']}")
            if pd.notna(feed['typical_update_interval_days']):
                print(f"  Typical interval: {feed['typical_update_interval_days']} days")
            print(f"  Current URL: {feed['urls.static_current']}")
            print(f"  Unstable URL tag: {feed['tag_unstable_url']}")
            print()
    
    # Feeds outside expected window (but not already shown as absolutely out of date)
    outside_window = df[
        (df['outside_expected_window'] == True) & 
        (df['absolutely_out_of_date'] == False)
    ].copy()
    
    if len(outside_window) > 0:
        print("=== FEEDS OUTSIDE EXPECTED UPDATE WINDOW ===")
        print("These feeds may have changed URLs or stopped updating:\n")
        
        for _, feed in outside_window.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Webpage: https://www.transit.land/feeds/{feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Agencies: {feed['agencies']}")
            print(f"  Last update: {feed['most_recent_feed_version_fetched_at']}")
            print(f"  Days since last update: {feed['days_since_last_update']}")
            print(f"  Typical interval: {feed['typical_update_interval_days']} days")
            print(f"  Expected next update: {feed['expected_next_update']}")
            print(f"  Current URL: {feed['urls.static_current']}")
            print(f"  Unstable URL tag: {feed['tag_unstable_url']}")
            print()
    
    # Feeds with failed cadence analysis
    failed_analysis = df[df['cadence_analysis_success'] == False].copy()
    
    if len(failed_analysis) > 0:
        print("=== FEEDS WITH FAILED CADENCE ANALYSIS ===")
        print("These feeds may need manual review:\n")
        
        for _, feed in failed_analysis.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Feed versions count: {feed['feed_versions_count']}")
            print(f"  Last update: {feed['most_recent_feed_version_fetched_at']}")
            print()
    
    # Feeds with very frequent updates
    frequent_updates = df[
        (df['cadence_analysis_success'] == True) & 
        (df['typical_update_interval_days'] < 1)
    ].copy()
    
    if len(frequent_updates) > 0:
        print("=== FEEDS WITH VERY FREQUENT UPDATES (< 1 day interval) ===")
        print("These feeds may have issues or be real-time feeds:\n")
        
        for _, feed in frequent_updates.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Typical interval: {feed['typical_update_interval_days']} days")
            print()
    
    # Summary statistics
    print("=== SUMMARY STATISTICS ===")
    successful_analysis = df[df['cadence_analysis_success'] == True]
    
    if len(successful_analysis) > 0:
        print(f"Median update interval: {successful_analysis['typical_update_interval_days'].median():.1f} days")
        print(f"Mean update interval: {successful_analysis['typical_update_interval_days'].mean():.1f} days")
        print(f"25th percentile: {successful_analysis['typical_update_interval_days'].quantile(0.25):.1f} days")
        print(f"75th percentile: {successful_analysis['typical_update_interval_days'].quantile(0.75):.1f} days")
    
    # Recommendations
    print()
    print("=== RECOMMENDATIONS ===")
    if len(absolutely_out_of_date) > 0:
        print(f"1. ⚠️  PRIORITY: Investigate {len(absolutely_out_of_date)} feeds absolutely out of date (>270 days)")
        print("   - These feeds haven't updated in 270+ days and likely need URL updates")
        print("   - Check agency websites for new feed locations")
        print("   - Verify if agencies are still publishing feeds")
    
    if len(outside_window) > 0:
        print(f"2. Investigate {len(outside_window)} feeds outside expected update window")
        print("   - Check if URLs have changed")
        print("   - Verify if agencies are still publishing feeds")
        print("   - Consider adding unstable_url=true tag if URL changes are expected")
    
    if len(failed_analysis) > 0:
        print(f"3. Review {len(failed_analysis)} feeds with failed cadence analysis")
        print("   - May need more feed versions for analysis")
        print("   - Check for data quality issues")
    
    print("4. Focus on feeds with unstable_url=false that are out of date")
    print("   - These are most likely to have changed URLs unexpectedly")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Fetch feed data and analyze update cadence to identify feeds needing attention'
    )
    parser.add_argument(
        '--endpoint',
        default="https://api.transit.land/api/v2",
        help="Transitland API endpoint"
    )
    parser.add_argument(
        '--analyze-only',
        action='store_true',
        help='Only analyze existing CSV file (skip fetching)'
    )
    parser.add_argument(
        '--fetch-only',
        action='store_true',
        help='Only fetch data (skip analysis)'
    )
    parser.add_argument(
        '--include-gtfs-rt',
        action='store_true',
        help='Include GTFS-RT feeds (default: only static GTFS)'
    )
    parser.add_argument(
        '--absolute-threshold-days',
        type=int,
        default=270,
        help='Days threshold for "absolutely out of date" flag (default: 270)'
    )
    parser.add_argument(
        'OUTFILE',
        help='Output CSV file (or input file if --analyze-only)'
    )
    args = parser.parse_args()
    
    apikey = os.environ.get('TRANSITLAND_API_KEY', '')
    
    if args.analyze_only:
        # Read existing CSV and analyze
        df = pd.read_csv(args.OUTFILE)
        # Filter to GTFS only (and optionally GTFS-RT) for analysis
        if not args.include_gtfs_rt:
            df = df[df['spec'] == 'GTFS']
        else:
            df = df[df['spec'].isin(['GTFS', 'GTFS_RT'])]
        df = df[df['spec'] != 'GBFS']  # Always exclude GBFS
        analyze_results(df.to_dict('records'))
    else:
        # Fetch and optionally analyze
        print("Fetching feed data...")
        rows = process_feeds(
            args.endpoint, 
            apikey, 
            args.OUTFILE, 
            include_gtfs_rt=args.include_gtfs_rt,
            absolute_threshold_days=args.absolute_threshold_days
        )
        print(f"Wrote {len(rows)} feeds to {args.OUTFILE}")
        
        if not args.fetch_only:
            analyze_results(rows)
