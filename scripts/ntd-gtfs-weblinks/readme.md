# Review of US NTD GTFS weblinks

The files in this subdirectory related to reviewing the US NTD release of GTFS weblinks and using it to update and add coverage to Transitland Atlas.

## Background

See:

- https://www.interline.io/blog/us-ntd-reporting-gtfs/
- https://www.interline.io/blog/us-ntd-reporting-gtfs-adopted/
- https://www.interline.io/blog/us-national-transit-database-releases-data-and-requests-more-feedback-2/
- https://www.transit.dot.gov/ntd/data-product/2023-annual-database-general-transit-feed-specification-gtfs-weblinks

## Files in this subdirectory

- `2023-gtfs-weblinks-checked.csv` - CSV of feeds that have been checked against Transitland contents in late 2024; the `Status` column at the end of the file indicates whether there is an existing feed with the same URL in Transitland
- `2023-gtfs-weblinks.dmfr.json` - a JSON file that has been created with initial DMFR records for all of the potentially unmatched records

## Help wanted

TO help, review the contents of `2023-gtfs-weblinks.dmfr.json`:

1) Look for records that may be missing in Transitland Atlas
2) Check for `likely_match_operator_link` and `likely_match_full` to see if the script successfully found an existing match, or if it's just noise
3) Also browse the Transitland map and website for an existing feed record in the same location but a different name (sometimes agencies change their brand name, or use a different name when reporting to NTD)
4) If adding a feed to Transitland Atlas, you can also delete the relevant portion of `2023-gtfs-weblinks.dmfr.json`

## Notes

To create these files:

```sh
wget https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-10/2023%20GTFS%20Weblinks.xlsx

in2csv 2023\ GTFS\ Weblinks.xlsx > 2023-gtfs-weblinks.csv

pipenv run python check-ntd-urls.py 2023-gtfs-weblinks.csv > 2023-gtfs-weblinks-checked.csv

pipenv run python check-ntd-urls.py
```
