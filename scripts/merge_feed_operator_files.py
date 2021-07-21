import json
import csv
import os
import sys
import glob
import collections

feed_files = {}
for fn in glob.glob("feeds/*.dmfr.json"):
    data = {}
    with open(fn, encoding="utf-8") as f:
        data = json.load(f)
    for feed in data['feeds']:
        feed_files[feed["id"]] = fn

operators = {}
operator_file_matches =  collections.defaultdict(set)
operator_file_matches_single_feed = collections.defaultdict(set)
operator_multiple_files = collections.defaultdict(set)
operator_no_file = {}
operator_no_feed = {}
for fn in glob.glob("operators/*.json"):
    data = {}
    with open(fn, encoding="utf-8") as f:
        data = json.load(f)
    operator = data
    osid = operator.get('onestop_id')
    operators[osid] = operator
    ofsids = set(i.get('feed_onestop_id') for i in operator.get('associated_feeds',[]))
    fsids = set(feed_files.get(i) for i in ofsids)
    if None in fsids:
        operator_no_feed[osid] = osid
    elif len(fsids) > 1:
        # print(fn)
        # print("\toperator:", operator["onestop_id"])
        # print("\t\tfeeds in files:", fsids)
        operator_multiple_files[osid].add(osid)
    elif len(fsids) == 1 and len(ofsids) == 1:
        # operator appears in exactly 1 feed file and has single feed
        fsid = list(fsids)[0]
        operator_file_matches_single_feed[fsid].add(osid)
    elif len(fsids) == 1:
        # operator appears in exactly 1 feed file
        fsid = list(fsids)[0]
        operator_file_matches[fsid].add(osid)
    elif len(fsids) == 0:
        operator_no_file[osid] = osid

def filter_empty(d):
    a = {}
    for k,v in d.items():
        if v:
            a[k] = v
    return a


# single_feed_items = set(operator_file_matches_single_feed.keys()) | set(operator_file_matches.keys())
single_feed_items = set(["feeds/vta.org.dmfr.json"])
for k in single_feed_items:
    data = {}
    with open(k, encoding="utf-8") as f:
        data = json.load(f)

    v = operator_file_matches_single_feed[k]
    print("single feed match:", k, v)
    feed_operators = collections.defaultdict(list)
    for osid in v:
        operator = operators[osid]
        fsids = [i.get("feed_onestop_id") for i in operator["associated_feeds"]]
        oifs = [{"gtfs_agency_id": i.get("gtfs_agency_id")} for i in operator["associated_feeds"] if i.get("gtfs_agency_id")]
        if len(set(fsids)) != 1:
            raise Exception("more than one oif")
        operator['associated_feeds'] = oifs
        feed_operators[fsids[0]].append(filter_empty(operator))

    # preserve order
    for feed in data.get('feeds'):
        ops = feed_operators.get(feed["id"])
        if ops:
            feed["operators"] = ops
    
    filematches = operator_file_matches.get(k)
    if filematches:
        data["operators"] = [filter_empty(operators.get(i)) for i in filematches]

    with open(k, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
for k,v in operator_file_matches.items():
    print("single file match:", k, v)

for k,v in operator_multiple_files.items():
    print("multiple matches:", k, v)

for k,v in operator_no_file.items():
    print("no file:", k, v)

for k,v in operator_no_feed.items():
    print("no feed:", k, v)