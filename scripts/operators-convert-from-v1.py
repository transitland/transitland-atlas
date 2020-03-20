import requests
import json

def writeToJsonFile(operator):
    filteredOperator = {k: v for k, v in operator.items() if k in ['onestop_id', 'name', 'short_name', 'tags', 'website']}
    filteredOperator['associated_feed_ids'] = operator['represented_in_feed_onestop_ids']
    with open(f"../operators/{filteredOperator['onestop_id']}.json", 'w', encoding='utf-8') as f:
        json.dump(filteredOperator, f, ensure_ascii=False, indent=2)

offset = 0
while True:
    r = requests.get(
        f"https://api.transit.land/api/v1/operators?per_page=1&offset={offset}")
    if r.status_code == 200:
        response = r.json()
        print(response['operators'][0]['onestop_id'])
        # operators.append(json['operators'][0])
        writeToJsonFile(response['operators'][0])
        offset += 1
    # else:
        # break