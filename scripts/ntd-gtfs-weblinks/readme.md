see https://www.transit.dot.gov/ntd/data-product/2023-annual-database-general-transit-feed-specification-gtfs-weblinks


```sh
wget https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-10/2023%20GTFS%20Weblinks.xlsx

in2csv 2023\ GTFS\ Weblinks.xlsx > 2023-gtfs-weblinks.csv

python check-ntd-urls.py 2023-gtfs-weblinks.csv > 2023-gtfs-weblinks-checked.csv
```