import requests
import csv
import sys
import argparse
import os

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

# Main function to process an array of feed URLs
def main(input_csv):
    try:
        # Read feed URLs from CSV file using the standard library
        feed_urls = []
        with open(input_csv, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                feed_urls.append(row)

        # Process feed URLs
        for row in feed_urls:
            feed_url = row["Weblink"]
            try:
                result = query_feed(feed_url)
                feed_data = result.get("data", {}).get("feeds")
                if feed_data and len(feed_data) > 0:
                    row["Status"] = "Success"
                else:
                    if feed_url.startswith("http://"):
                        modified_url = feed_url.replace("http://", "https://")
                        result = query_feed(modified_url)
                        feed_data = result.get("data", {}).get("feeds")
                        if feed_data and len(feed_data) > 0:
                            row["Status"] = "Success (Retried with HTTPS)"
                        else:
                            row["Status"] = "No results"
                    else:
                        row["Status"] = "No results"
            except Exception as e:
                row["Status"] = f"Error: {e}"

        # Write results to stdout as CSV
        output_fieldnames = list(feed_urls[0].keys()) + ["Status"]
        writer = csv.DictWriter(sys.stdout, fieldnames=output_fieldnames)
        writer.writeheader()
        writer.writerows(feed_urls)

    except FileNotFoundError:
        print(f"CSV file '{input_csv}' not found. Please provide a valid file path.", file=sys.stderr)
    except KeyError:
        print("CSV file must contain a column named 'Weblink'.", file=sys.stderr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process feed URLs from a CSV file.")
    parser.add_argument("input_csv", help="Path to the input CSV file containing feed URLs.")
    args = parser.parse_args()
    main(args.input_csv)
