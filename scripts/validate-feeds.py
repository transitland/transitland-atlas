import subprocess
import sys
import os
import sqlite3
import json
import glob

fail_the_build = False

# validate DMFR schema version consistency
# Get schema version from environment variable, default to v0.6.0
dmfr_schema_version = os.environ.get('DMFR_SCHEMA_VERSION', 'v0.6.0')
EXPECTED_SCHEMA = f"https://dmfr.transit.land/json-schema/dmfr.schema-{dmfr_schema_version}.json"
dmfr_files = glob.glob("../feeds/*.dmfr.json")

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
db_filename = 'feed-validation.db'
os.system(f"rm -f {db_filename}")
sync_command = f"transitland sync --hide-unseen --hide-unseen-operators --dburl=sqlite3://{db_filename} ../feeds/*.dmfr.json"
sync_log = subprocess.check_output(sync_command, shell=True)

for log_line in sync_log.splitlines():
  log_line = log_line.decode("utf-8")
  if log_line.find("updated feed") > -1:
    print(f"ERROR: duplicate feed found at: {log_line}")
    fail_the_build = True
db_conn = sqlite3.connect(db_filename)
c = db_conn.cursor()

# check feed onestop_id uniqueness
c.execute('''
  SELECT onestop_id from current_feeds
''')
onestop_ids = c.fetchall()
for row in onestop_ids:
  osid = row[0] or ''
  valid = True
  dashcount = osid.count("-")
  if len(osid) == 0:
    valid = False
  if dashcount == 0 or dashcount > 2:
    valid = False
  if len(osid) > 0 and osid[0] != "f":
    valid = False
  if osid != osid.lower():
    valid = False
  if osid.endswith("~"):
    valid = False
  if not valid:
    print(f"ERROR: improperly formatted Feed Onestop ID: {osid}")
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
  valid = True
  dashcount = osid.count("-")
  if len(osid) == 0:
    valid = False
  if dashcount == 0 or dashcount > 2:
    valid = False
  if len(osid) > 0 and osid[0] != "o":
    valid = False
  if '' in osid.split('-'):
    valid = False
  if osid.endswith("~"):
    valid = False
  if not valid:
    print(f"ERROR: improperly formatted Operator Onestop ID: {osid}")
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
    valid = True
    associated_feed_onestop_id = associated_feed['feed_onestop_id']
    if not associated_feed_onestop_id:
      print(f"ERROR: missing feed Onestop ID in the associated_feeds block for operator {operator_onestop_id}")
      fail_the_build = True
      continue
    dashcount = associated_feed_onestop_id.count("-")
    if len(associated_feed_onestop_id) == 0:
      valid = False
    if dashcount == 0 or dashcount > 2:
      valid = False
    if len(associated_feed_onestop_id) > 0 and associated_feed_onestop_id[0] != "f":
      valid = False
    if associated_feed_onestop_id.endswith("~"):
      valid = False
    if not valid:
      print(f"ERROR: improperly formatted feed Onestop ID: {associated_feed_onestop_id} in the associated_feeds block for operator {operator_onestop_id}")
      fail_the_build = True

if fail_the_build:
  sys.exit(1)
else:
  sys.exit(0)