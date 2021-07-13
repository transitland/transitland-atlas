import csv
import os
import glob
import json
import sys

feeds = {}
for fn in glob.glob("feeds/*.dmfr.json"):
    with open(fn) as ff:
        d = json.load(ff)
        for feed in d['feeds']:
            feeds[feed['id']] = feed

operators = {}
for fn in glob.glob("operators/*.json"):
    with open(fn) as ff:
        d = json.load(ff)
        operators[d['onestop_id']] = d

updated_feeds = {}
updated_operators = {}
with open(sys.ARGV[1]) as f:
    reader = csv.DictReader(f)
    for row in reader:
        continue
        ######## operators
        osid = row['onestop_id']
        fsid = row['feed_onestop_id']
        operator = operators.get(osid)
        if not operator:
            print("making operator:", row['onestop_id'])
            operator = {
                "onestop_id": osid,
                "name": row["agency_name"],
                "associated_feeds": [{
                    "feed_onestop_id": fsid,
                    "gtfs_agency_id": row["gtfs_agency_id"],
                }]
            }
        updated_operator = False
        # for key,key2 in {'agency_name':'name'}.items():
        #     v = row.get(key)
        #     if operator.get(key2) != v:
        #         updated_operator = True
        #         print("updated operator", osid, "key:", key, key2)
        #         print("\twas:", operator[key2])
        #         print("\tnew:", v)
        #         operator[key2] = v

        for key, key2 in {'us_ntd_id':'us_ntd_id', 'wikidata_id': 'wikidata_id'}.items():
            v = row.get(key)
            tags = operator.get('tags', {})
            if not v or v == tags.get(key2):
                continue
            updated_operator = True
            tags[key2] = v
            operator['tags'] = tags

        if updated_operator:
            updated_operators[osid] = operator

        ######## feeds

        feed = feeds.get(fsid)
        if not feed:
            print("no feed:", fsid)
        else:
            updated_feed = False        
            new_url = row.get('updated fetch link ')
            if new_url:
                updated_feed = True
                urls = feed.get('urls',{})
                urls['static_current'] = new_url
                feed['urls'] = urls

            for key,key2 in {'license_url': 'url', 'license_spdx_identifier':'license_spdx_identifier'}.items():            
                v = row.get(key)
                if not v or len(v) == 0:
                    continue
                updated_feed = True
                lc = feed.get('license', {})
                lc[key2] = v
                feed['license'] = lc

            for key in ['developer_website_url', 'developer_contact_email', 'developer_discussion_url']:
                v = row.get(key)
                if not v:
                    continue
                updated_feed = True
                tags = feed.get('tags',{})
                tags[key] = row.get(key)
                feeds['tags'] = tags
                print("tags:", tags)

            if updated_feed:
                updated_feeds[fsid] = feed

for fn in glob.glob("feeds/*.dmfr.json"):
    d = {}
    with open(fn) as ff:
        d = json.load(ff)
    
    f2 = []
    updated = False
    for feed in d['feeds']:
        updated_feed = updated_feeds.get(feed['id'])
        if updated_feed:
            updated = True
            f2.append(updated_feed)
            print("updated feed:", updated_feed)
        else:
            f2.append(feed)
    d['feeds'] = f2

    # if updated:
    #     with open(fn, 'w') as ff:
    #         json.dump(d, ff, indent=2)

for fn in glob.glob("operators/*.json"):
    d = {}
    with open(fn) as ff:
        d = json.load(ff)
    osid = d['onestop_id']
    if updated_operators.get(osid):
        with open(fn, 'w') as ff:
            json.dump(updated_operators[osid], ff, indent=2)

