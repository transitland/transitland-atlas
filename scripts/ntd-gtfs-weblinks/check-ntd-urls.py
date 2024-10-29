import re
import requests
import concurrent.futures
from tqdm import tqdm
import csv
import sys
import argparse
import os
import json
import re

# Define the GraphQL endpoint
GRAPHQL_ENDPOINT = "https://transit.land/api/v2/query"

# Example GraphQL query template to get information about a feed
FEED_QUERY = """
query ($feed_url: String!) {
  feeds(where: {source_url: {url: $feed_url}}) {
    onestop_id
  }
}
"""

# Function to query Transit.land GraphQL endpoint
def query_feed(feed_url):
    headers = {
        "Content-Type": "application/json",
        "apikey": os.getenv("TRANSITLAND_API_KEY")  # Replace with your actual API key as per Transit.land documentation
    }

    variables = {"feed_url": feed_url}

    response = requests.post(
        GRAPHQL_ENDPOINT,
        json={"query": FEED_QUERY, "variables": variables},
        headers=headers
    )

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"GraphQL query failed: {response.status_code}\n{response.text}")

# Function to create a JSON object for feeds with no matching results
def create_json_object(row, feed_url):
    agency_name = row["Agency Name"]
    agency_name_id = re.sub(r'[^a-zA-Z0-9]+', '~', agency_name).lower()
    json_object = {
        "id": f"f-{agency_name_id}",
        "spec": "gtfs",
        "urls": {
            "static_current": feed_url
        },
        "operators": [
            {
                "onestop_id": f"o-{agency_name_id}",
                "name": agency_name,
                "tags": {
                    "us_ntd_id": row["NTD ID"]
                }
            }
        ]
    }
    return json_object

# Main function to process an array of feed URLs
def main(input_csv, output_csv, output_dmfr_json, max_workers):
    try:
        # Read feed URLs from CSV file using the standard library
        feed_urls = []
        with open(input_csv, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["Weblink"]:
                    # Clean up feed URL
                    if row["Weblink"].startswith('https://urldefense.com/v3/__'):
                        row["Weblink"] = re.sub(r'^https://urldefense\.com/v3/__', '', row["Weblink"])
                        row["Weblink"] = re.sub(r'__.*$', '', row["Weblink"])
                    feed_urls.append(row)

        no_result_json_objects = []

        # Process feed URLs
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_row = {executor.submit(query_feed, row["Weblink"]): row for row in feed_urls}
            for future in tqdm(concurrent.futures.as_completed(future_to_row), total=len(feed_urls), desc="Processing feed URLs"):
                row = future_to_row[future]
                feed_url = row["Weblink"].strip()
                if not feed_url:
                    row["Status"] = "Skipped (Blank URL)"
                    continue
                try:
                    result = future.result()
                    feed_data = result.get("data", {}).get("feeds")
                    if feed_data and len(feed_data) > 0:
                        row["Status"] = "Success"
                    elif feed_url.startswith("http://"):
                            modified_url = feed_url.replace("http://", "https://")
                            result = query_feed(modified_url)
                            feed_data = result.get("data", {}).get("feeds")
                            if feed_data and len(feed_data) > 0:
                                row["Status"] = "Success (Retried with HTTPS)"
                            else:
                                row["Status"] = "No results"
                                no_result_json_objects.append(create_json_object(row, feed_url))
                    elif feed_url.startswith("https://"):
                            modified_url = feed_url.replace("https://", "http://")
                            result = query_feed(modified_url)
                            feed_data = result.get("data", {}).get("feeds")
                            if feed_data and len(feed_data) > 0:
                                row["Status"] = "Success (Retried with HTTP)"
                            else:
                                row["Status"] = "No results"
                                no_result_json_objects.append(create_json_object(row, feed_url))
                    if not row["Status"]:
                        row["Status"] = "No results"
                        no_result_json_objects.append(create_json_object(row, feed_url))
                except Exception as e:
                    row["Status"] = f"Error: {e}"

        # Write results to output CSV file
        output_fieldnames = list(feed_urls[0].keys()) + ["Status"]
        with open(output_csv, mode='w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=output_fieldnames)
            writer.writeheader()
            writer.writerows(feed_urls)

        # Write JSON objects to a file
        with open(output_dmfr_json, mode='w') as jsonfile:
            json.dump(no_result_json_objects, jsonfile, indent=2)

        print(f"Results have been written to '{output_csv}' and JSON data to '{args.output_dmfr_json}'")

    except FileNotFoundError:
        print(f"CSV file '{input_csv}' not found. Please provide a valid file path.", file=sys.stderr)
    except KeyError as e:
        print(f"CSV file must contain the column '{e.args[0]}'.", file=sys.stderr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process feed URLs from a CSV file.")
    parser.add_argument("input_csv", nargs='?', default="2023-gtfs-weblinks.csv", help="Path to the input CSV file containing feed URLs (default: '2023-gtfs-weblinks.csv').")
    parser.add_argument("output_csv", nargs='?', default="2023-gtfs-weblinks-checked.csv", help="Path to the output CSV file (default: '2023-gtfs-weblinks-checked.csv').")
    parser.add_argument("output_dmfr_json", nargs='?', default="2023-gtfs-weblinks.dmfr.json", help="Path to the output JSON file (default: '2023-gtfs-weblinks.dmfr.json').")
    parser.add_argument("--max_workers", type=int, default=10, help="Maximum number of workers for parallel requests (default: 10).")
    args = parser.parse_args()
    main(args.input_csv, args.output_csv, args.output_dmfr_json, args.max_workers)
