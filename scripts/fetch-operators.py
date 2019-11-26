import requests
import csv
import pandas as pd

operators = []
offset = 0
while True:
    r = requests.get(
        f"https://api.transit.land/api/v1/operators?per_page=1&offset={offset}")
    if r.status_code == 200:
        json = r.json()
        print(json['operators'][0]['onestop_id'])
        operators.append(json['operators'][0])
        offset += 1
        if offset == 10:
            break
    else:
        break

df = pd.DataFrame(operators)
df.drop(columns=['geometry', 'created_at', 'updated_at',
                 'created_or_updated_in_changeset_id'], inplace=True)
df.to_csv('some.csv', header=True)
