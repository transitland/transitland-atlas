wget http://bit.ly/catalogs-csv -O mobility-database-catalogs.csv
wget https://docs.google.com/spreadsheets/d/1Q96KDppKsn2khdrkraZCQ7T_qRSfwj7WsvqXvuMt4Bc/export\?format=csv&gid=1787149399 -O mobility-database-mappings.csv

cat mobility-database-mappings.csv | csvgrep -c "Transitland ID" -r "^$" | csvjoin -c "mdb_source_id,mdb_source_id" mobility-database-catalogs.csv - > mobility-database-records-not-mapped-to-transitland.c
sv