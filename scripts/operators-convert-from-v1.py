import os
import requests
import json

def addApiKeyToUrl(base):
    return f"{base}apikey={os.environ['TRANSITLAND_API_KEY']}"

def writeToJsonFile(operator):
    filteredOperator = {k: v for k, v in operator.items() if k in ['onestop_id', 'name', 'short_name', 'tags', 'website']}
    filteredOperator['associated_feeds'] = []
    if len(operator['represented_in_feed_onestop_ids']) == 1:
        filteredOperator['associated_feeds'].append({
            'feed_onestop_id': operator['represented_in_feed_onestop_ids'][0]
        })
    else:
        for feed_onestop_id in operator['represented_in_feed_onestop_ids']:
            oifs = getOperatorsInFeed(filteredOperator['onestop_id'], feed_onestop_id)
            filteredOperator['associated_feeds'].extend(oifs)
        # remove duplicates:
        filteredOperator['associated_feeds'] = [dict(t) for t in {tuple(d.items()) for d in filteredOperator['associated_feeds']}]
    with open(f"../operators/{filteredOperator['onestop_id']}.json", 'w', encoding='utf-8') as f:
        json.dump(filteredOperator, f, ensure_ascii=False, indent=2)

def getOperatorsInFeed(operator_onestop_id, feed_onestop_id):
    r = requests.get(
        addApiKeyToUrl(f"https://api.transit.land/api/v1/feeds/{feed_onestop_id}?")
    )
    if r.status_code == 200:
        response = r.json()
        oifs = []
        if response['spec'] == 'gtfs':
            for oif in response['operators_in_feed']:
                if oif['operator_onestop_id'] == operator_onestop_id:
                    oifs.append({
                        'feed_onestop_id': feed_onestop_id,
                        'gtfs_agency_id': oif['gtfs_agency_id']
                    })
        else:
            oifs = [
                {
                    'feed_onestop_id': feed_onestop_id,
                }
            ]
        return oifs

offset = 0
while True:
    r = requests.get(
        addApiKeyToUrl(f"https://api.transit.land/api/v1/operators?per_page=1&offset={offset}&")
    )
    if r.status_code == 200:
        response = r.json()
        print(response['operators'][0]['onestop_id'])
        writeToJsonFile(response['operators'][0])
        offset += 1
    # else:
        # break