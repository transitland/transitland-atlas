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


operator_file_matches =  collections.defaultdict(set)
operator_file_matches_single_feed = collections.defaultdict(set)
operator_multiple_files = collections.defaultdict(set)
operator_no_file = {}
for fn in glob.glob("operators/*.json"):
    data = {}
    with open(fn, encoding="utf-8") as f:
        data = json.load(f)
    operator = data
    osid = operator.get('onestop_id')
    ofsids = set(i.get('feed_onestop_id') for i in operator.get('associated_feeds',[]))
    fsids = set(feed_files.get(i) for i in ofsids)
    if len(fsids) > 1:
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

for k,v in operator_file_matches_single_feed.items():
    print("single feed match:", k, v)

for k,v in operator_file_matches.items():
    print("single file match:", k, v)

for k,v in operator_multiple_files.items():
    print("multiple matches:", k, v)

for k,v in operator_no_file.items():
    print("no file:", k, v)

