import csv
import requests

r = requests.get("https://docs.google.com/spreadsheets/d/e/2PACX-1vSBISDNDA14y815xhzao8yIfPg250LUe8bH4fqgyc--Lle84f392OnWJMsQqOXexzSSD605nx3lf4C8/pub?gid=3&single=true&output=csv")
decoded_content = r.content.decode('utf-8')
cr = csv.reader(decoded_content.splitlines(), delimiter=',')
my_list = list(cr)
for row in my_list:
    print(row)