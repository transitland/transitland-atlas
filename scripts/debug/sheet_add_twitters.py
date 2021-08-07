import csv
import os
import glob
import json
import sys

update_tags = ['twitter_general', 'twitter_service_alerts']
operator_tags = {}
updated_operators = {}

with open(sys.argv[1]) as f:
    reader = csv.DictReader(f)
    for row in reader:
        osid = row['onestop_id']
        operator_tags[osid] = operator_tags.get(osid, {})        
        tags = operator_tags[osid]
        for key in update_tags:
            v = row.get(key)
            if v:
                if v[-1] == "/":
                    v = v[:-1]
                a = v.rpartition("/")[2]
                print(v, a)
                tags[key] = a

for fn in glob.glob("feeds/*.dmfr.json"):
    d = {}
    with open(fn) as ff:
        d = json.load(ff)
    updated = False
    for feed in d.get('feeds', []):
        for op in feed.get('operators', []):
            osid = op.get('onestop_id')
            tags = operator_tags.get(osid)
            if tags:
                updated = True
                op['tags'] = op.get('tags',{})
                op['tags'].update(tags)
    for op in d.get('operators', []):
            osid = op.get('onestop_id')
            tags = operator_tags.get(osid)
            if tags:
                updated = True
                op['tags'] = op.get('tags',{})
                op['tags'].update(tags)

    if updated:
        print("saving:", d)
        with open(fn, 'w', encoding="utf-8") as ff:
            json.dump(d, ff, indent=2, ensure_ascii=False)

