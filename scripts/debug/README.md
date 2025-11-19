# Enhanced Feed Maintenance Script

This enhanced version of the feed maintenance script analyzes multiple recent feed versions and uses the Prophet library for time series analysis to detect feeds that are outside their expected update cadence.

## Features

- **Multi-version Analysis**: Analyzes up to 10 recent feed versions instead of just the most recent
- **Prophet Time Series Analysis**: Uses Facebook's Prophet library to predict expected update cadence
- **Cadence Detection**: Identifies feeds that are outside their expected update window
- **Comprehensive Reporting**: Provides detailed analysis results in CSV format

## Installation

1. Install the required dependencies using pipenv:
```bash
pipenv install
```

Note: Prophet may require additional system dependencies on some platforms. See the [Prophet installation guide](https://facebook.github.io/prophet/docs/installation.html) for details.

## Usage

### 1. Run the Enhanced Feed Maintenance Script

```bash
# Set your API key
export TRANSITLAND_API_KEY="your_api_key_here"

# Run the script
pipenv run python feed_maintenance.py feeds_analysis.csv
```

### 2. Analyze the Results

```bash
pipenv run python analyze_cadence_results.py feeds_analysis.csv
```

## Output Fields

The enhanced script adds several new fields to the CSV output:

- **`feed_versions_count`**: Number of feed versions analyzed
- **`expected_next_update`**: Predicted next update date based on Prophet analysis
- **`typical_update_interval_days`**: Median interval between updates
- **`days_since_last_update`**: Days since the last feed version was fetched
- **`outside_expected_window`**: Boolean flag indicating if feed is outside expected update window
- **`cadence_analysis_success`**: Boolean indicating if Prophet analysis was successful

## How It Works

### Cadence Analysis

1. **Data Collection**: Collects up to 10 recent feed versions and their fetch timestamps
2. **Time Series Modeling**: Uses Prophet to fit a trend model to the update timestamps
3. **Pattern Recognition**: Identifies typical update intervals and predicts next expected update
4. **Anomaly Detection**: Flags feeds that are significantly overdue for updates

### Threshold Logic

A feed is considered "outside expected window" if:
- It has been more than 1.5x the typical update interval since the expected next update
- At least 7 days have passed (minimum threshold)

## Use Cases

### 1. URL Change Detection
Feeds that are outside their expected update window may have:
- Changed hostnames or file paths
- Moved to new servers
- Changed file naming conventions

### 2. Agency Communication Issues
- Agencies may have stopped publishing feeds
- Technical issues preventing updates
- Changes in publishing schedules

### 3. Manual Intervention Required
- Feeds marked with `unstable_url=false` that are outside expected window need immediate attention
- Feeds with `unstable_url=true` may need URL updates

## Example Analysis

```bash
# Generate analysis
pipenv run python feed_maintenance.py feeds_2024.csv

# Review results
pipenv run python analyze_cadence_results.py feeds_2024.csv
```

The analysis script will show:
- Feeds outside expected update window
- Feeds with failed cadence analysis
- Statistical summary of update patterns
- Specific recommendations for investigation

## Troubleshooting

### Prophet Installation Issues
- On macOS: `brew install pkg-config`
- On Ubuntu/Debian: `sudo apt-get install build-essential python3-dev`
- Consider using conda: `conda install -c conda-forge prophet`

### Memory Issues
- For large datasets, consider processing feeds in batches
- Prophet analysis requires at least 3 feed versions for reliable results

## Limitations

- Requires at least 3 feed versions for meaningful analysis
- Prophet analysis may fail for feeds with irregular update patterns
- Real-time feeds (daily updates) may trigger false positives
- Historical patterns may not predict future changes in agency behavior

## Contributing

When adding new analysis features:
1. Update the GraphQL query if new fields are needed
2. Add new output fields to the CSV keys list
3. Update the analysis logic in `analyze_update_cadence()`
4. Add corresponding fields to the analysis script
