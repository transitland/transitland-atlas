#!/usr/bin/env python3
"""
Helper script to analyze the output from feed_maintenance.py and identify feeds
that may need attention due to being outside their expected update window.
"""

import pandas as pd
import argparse
from datetime import datetime

def analyze_results(csv_file):
    """Analyze the cadence analysis results and identify problematic feeds."""
    
    # Read the CSV file
    df = pd.read_csv(csv_file)
    
    print(f"Total feeds analyzed: {len(df)}")
    print(f"Feeds with successful cadence analysis: {df['cadence_analysis_success'].sum()}")
    print(f"Feeds outside expected window: {df['outside_expected_window'].sum()}")
    print()
    
    # Show feeds that are outside their expected update window
    outside_window = df[df['outside_expected_window'] == True].copy()
    
    if len(outside_window) > 0:
        print("=== FEEDS OUTSIDE EXPECTED UPDATE WINDOW ===")
        print("These feeds may have changed URLs or stopped updating:")
        print()
        
        for _, feed in outside_window.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Agencies: {feed['agencies']}")
            print(f"  Last update: {feed['most_recent_feed_version_fetched_at']}")
            print(f"  Days since last update: {feed['days_since_last_update']}")
            print(f"  Typical interval: {feed['typical_update_interval_days']} days")
            print(f"  Expected next update: {feed['expected_next_update']}")
            print(f"  Current URL: {feed['urls.static_current']}")
            print(f"  Unstable URL tag: {feed['tag_unstable_url']}")
            print()
    
    # Show feeds with failed cadence analysis
    failed_analysis = df[df['cadence_analysis_success'] == False].copy()
    
    if len(failed_analysis) > 0:
        print("=== FEEDS WITH FAILED CADENCE ANALYSIS ===")
        print("These feeds may need manual review:")
        print()
        
        for _, feed in failed_analysis.iterrows():
            print(f"Feed: {feed['onestop_id']}")
            print(f"  Spec: {feed['spec']}")
            print(f"  Feed versions count: {feed['feed_versions_count']}")
            print(f"  Last update: {feed['most_recent_fetch_at']}")
            print()
    
    # Show feeds with very frequent updates (potential issues)
    frequent_updates = df[
        (df['cadence_analysis_success'] == True) & 
        (df['typical_update_interval_days'] < 1)
    ].copy()
    
    if len(frequent_updates) > 0:
        print("=== FEEDS WITH VERY FREQUENT UPDATES (< 1 day interval) ===")
        print("These feeds may have issues or be real-time feeds:")
        print()
        
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
    if len(outside_window) > 0:
        print(f"1. Investigate {len(outside_window)} feeds outside expected update window")
        print("   - Check if URLs have changed")
        print("   - Verify if agencies are still publishing feeds")
        print("   - Consider adding unstable_url=true tag if URL changes are expected")
    
    if len(failed_analysis) > 0:
        print(f"2. Review {len(failed_analysis)} feeds with failed cadence analysis")
        print("   - May need more feed versions for analysis")
        print("   - Check for data quality issues")
    
    print("3. Focus on feeds with unstable_url=false that are outside expected window")
    print("   - These are most likely to have changed URLs unexpectedly")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Analyze feed cadence analysis results')
    parser.add_argument('csv_file', help='CSV file from feed_maintenance.py')
    args = parser.parse_args()
    
    analyze_results(args.csv_file)
