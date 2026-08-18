#!/usr/bin/env -S uv run

import sys
import os
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_registry

# Resolved from this file rather than from the working directory, so the script
# runs from anywhere. It previously globbed "../feeds/*" and so only worked when
# invoked from inside scripts/.
FEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "feeds")

fail_the_build = False

# check that all files in feeds/ have a .dmfr.json extension
all_feed_files = glob.glob(os.path.join(FEEDS_DIR, "*"))
for file_path in all_feed_files:
    if os.path.isfile(file_path) and not file_path.endswith(".dmfr.json"):
        print(f"ERROR: {file_path} does not have a .dmfr.json extension (all files under feeds/ must end in .dmfr.json)")
        fail_the_build = True

# validate DMFR schema version consistency
# Get schema version from environment variable, default to v0.6.0
dmfr_schema_version = os.environ.get('DMFR_SCHEMA_VERSION', 'v0.6.0')
EXPECTED_SCHEMA = f"https://dmfr.transit.land/json-schema/dmfr.schema-{dmfr_schema_version}.json"
dmfr_files = glob.glob(os.path.join(FEEDS_DIR, "*.dmfr.json"))

for file_path in dmfr_files:
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        if '$schema' not in data:
            print(f"ERROR: {file_path} is missing $schema field")
            fail_the_build = True
            continue
        
        schema = data['$schema']
        if schema != EXPECTED_SCHEMA:
            print(f"ERROR: {file_path} has incorrect schema version")
            print(f"  Expected: {EXPECTED_SCHEMA}")
            print(f"  Found:    {schema}")
            fail_the_build = True
    except json.JSONDecodeError as e:
        print(f"ERROR: {file_path} is not valid JSON: {e}")
        fail_the_build = True
    except Exception as e:
        print(f"ERROR: Failed to read {file_path}: {e}")
        fail_the_build = True

if not fail_the_build and len(dmfr_files) > 0:
    print(f"All {len(dmfr_files)} DMFR files use the correct schema version ({dmfr_schema_version})")

# load dmfr to database
sync_log = []
db_conn = atlas_registry.load(FEEDS_DIR, sync_log=sync_log)
c = db_conn.cursor()

for log_line in sync_log:
  if "updated feed" in log_line:
    print(f"ERROR: duplicate feed found at: {log_line}")
    fail_the_build = True

# check feed onestop_id uniqueness
c.execute('''
  SELECT onestop_id from current_feeds
''')
onestop_ids = c.fetchall()
for row in onestop_ids:
  osid = row[0] or ''
  problems = atlas_registry.onestop_id_problems(osid, "feed")
  if problems:
    print(f"ERROR: improperly formatted Feed Onestop ID: {osid} ({'; '.join(problems)})")
    fail_the_build = True

# check uniqueness of urls.static_current
c.execute('''
  SELECT json_extract(urls, '$.static_current') from current_feeds
''')
results = c.fetchall()
urls = list(filter(None, [url_match[0] for url_match in results]))
duplicate_urls = set([x for x in urls if urls.count(x) > 1])
if len(duplicate_urls) > 0:
    print(f"ERROR: more than one feed has the same value defined for urls.static_current: {duplicate_urls}")
    fail_the_build = True

# check operator onestop_id uniqueness and format
c.execute('''
  SELECT onestop_id from current_operators
''')
onestop_ids = c.fetchall()
for row in onestop_ids:
  osid = row[0] or ''
  # Case is not enforced here; see onestop_id_problems for why.
  problems = atlas_registry.onestop_id_problems(osid, "operator", require_lowercase=False)
  if problems:
    print(f"ERROR: improperly formatted Operator Onestop ID: {osid} ({'; '.join(problems)})")
    fail_the_build = True

# check associated_feeds[].feed_onstop_id format
c.execute('''
  SELECT onestop_id, associated_feeds from current_operators
''')
operators = c.fetchall()
for o in operators:
  operator_onestop_id = o[0]
  associated_feeds = json.loads(o[1])
  if operator_onestop_id == None or associated_feeds == None:
    continue
  for associated_feed in associated_feeds:
    associated_feed_onestop_id = associated_feed['feed_onestop_id']
    if not associated_feed_onestop_id:
      print(f"ERROR: missing feed Onestop ID in the associated_feeds block for operator {operator_onestop_id}")
      fail_the_build = True
      continue
    problems = atlas_registry.onestop_id_problems(associated_feed_onestop_id, "feed")
    if problems:
      print(f"ERROR: improperly formatted feed Onestop ID: {associated_feed_onestop_id} in the associated_feeds block for operator {operator_onestop_id} ({'; '.join(problems)})")
      fail_the_build = True

# check that every associated_feeds reference names a feed that exists
#
# A reference to a feed that is not in the registry is silently ignored on sync,
# so an operator can look associated and not be. These accumulate when a feed is
# renamed or retired and the operator that referenced it is not updated.
c.execute('''
  SELECT o.onestop_id, o.associated_feeds
  FROM current_operators o
  WHERE o.associated_feeds IS NOT NULL
''')
known_feeds = {row[0] for row in db_conn.cursor().execute('SELECT onestop_id FROM current_feeds')}
for operator_onestop_id, associated_feeds in c.fetchall():
  for associated_feed in (json.loads(associated_feeds) or []):
    fid = associated_feed.get('feed_onestop_id')
    if fid and fid not in known_feeds:
      print(f"ERROR: operator {operator_onestop_id} is associated with {fid}, which is not a feed in this registry")
      fail_the_build = True

# report realtime feeds that no operator claims
#
# A gtfs-rt feed with no operator is invisible in the platform's operator view,
# and easy to create by accident when the realtime lives in a vendor-named file
# separate from the agency's static. Reported rather than enforced while the
# existing ones are worked through; make it fail once the count reaches zero.
c.execute('''
  SELECT f.onestop_id
  FROM current_feeds f
  WHERE f.spec = 'gtfs-rt'
    AND f.onestop_id NOT IN (
      SELECT json_extract(value, '$.feed_onestop_id')
      FROM current_operators, json_each(current_operators.associated_feeds)
      WHERE json_extract(value, '$.feed_onestop_id') IS NOT NULL
    )
  ORDER BY f.onestop_id
''')
unclaimed = [row[0] for row in c.fetchall()]
if unclaimed:
  print(f"WARNING: {len(unclaimed)} gtfs-rt feed(s) have no operator associated with them:")
  for osid in unclaimed:
    print(f"  {osid}")

if fail_the_build:
  sys.exit(1)
else:
  sys.exit(0)