import re
import subprocess
import sys
import os
import sqlite3

fail_the_build = False

# load dmfr to database
db_filename = 'feed-validation.db'
os.system(f"rm -f {db_filename}")
sync_command = f"transitland dmfr sync --hide-unseen --hide-unseen-operators -dburl=sqlite3://{db_filename} ../feeds/*.dmfr.json"
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

# check operator onestop_id uniqueness
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
  if not valid:
    print(f"ERROR: improperly formatted Operator Onestop ID: {osid}")
    fail_the_build = True

if fail_the_build:
  sys.exit(1)
else:
  sys.exit(0)