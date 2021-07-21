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
    fsids = set(i.get('feed_onestop_id') for i in operator.get('associated_feeds',[]))
    files = set(feed_files.get(i) for i in fsids)
    if None in files or None in fsids:
        operator_no_feed[osid] = osid
    elif len(files) > 1:
        # print(fn)
        # print("\toperator:", operator["onestop_id"])
        # print("\t\tfeeds in files:", fsids)
        operator_multiple_files[osid].add(osid)
    elif len(files) == 1 and len(fsids) == 1:
        # operator appears in exactly 1 feed file and has single feed
        operator_file_matches_single_feed[list(files)[0]].add(osid)
    elif len(files) == 1:
        # operator appears in exactly 1 feed file
        operator_file_matches[list(files)[0]].add(osid)
    elif len(files) == 0:
        operator_no_file[osid] = osid

def filter_empty(d):
    a = {}
    for k,v in d.items():
        if v:
            a[k] = v
    return a


single_feed_items = set(operator_file_matches_single_feed.keys()) | set(operator_file_matches.keys())
# single_feed_items = set(["feeds/vta.org.dmfr.json"])
for feed_path in single_feed_items:
    osids = operator_file_matches_single_feed[feed_path]
    print("single file and feed match:", feed_path, osids)    

    data = {}
    with open(feed_path, encoding="utf-8") as f:
        data = json.load(f)

    # process this way to preserve order
    for feed in data.get('feeds'):
        fsid = feed["id"]
        feed_operators = collections.defaultdict(list)
        for osid in osids:
            operator = operators[osid]
            fsids = [i.get("feed_onestop_id") for i in operator["associated_feeds"]]
            oifs = [{"gtfs_agency_id": i.get("gtfs_agency_id")} for i in operator["associated_feeds"] if i.get("gtfs_agency_id")]
            if fsid not in fsids:
                continue
            if len(set(fsids)) != 1:
                raise Exception("more than one unique oif")
            operator['associated_feeds'] = oifs
            if not feed.get("operators"):
                feed["operators"] = []
            feed["operators"].append(filter_empty(operator))
            os.unlink(os.path.join("operators", osid+".json"))
    
    filematches = operator_file_matches.get(feed_path)
    if filematches:
        print("single file match:", feed_path, filematches)    
        data["operators"] = [filter_empty(operators.get(i)) for i in filematches]
        for i in filematches:
            os.unlink(os.path.join("operators", i+".json"))

    with open(feed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    
# for k,v in operator_multiple_files.items():
#     print("multiple matches:", k, v)

# for k,v in operator_no_file.items():
#     print("no file:", k, v)

# for k,v in operator_no_feed.items():
#     print("no feed:", k, v)