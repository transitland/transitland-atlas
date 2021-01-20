import re
import subprocess
import sys
import os
import sqlite3

fail_the_build = False

db_filename = 'feed-validation.db'
os.system(f"rm -f {db_filename}")
sync_command = f"transitland dmfr sync -dburl=sqlite3://{db_filename} ../feeds/*.dmfr.json"
sync_log = subprocess.check_output(sync_command, shell=True)

for log_line in sync_log.splitlines():
  log_line = log_line.decode("utf-8")
  if log_line.find("updated feed") > -1:
    print(f"ERROR: duplicate feed found at: {log_line}")
    fail_the_build = True
db_conn = sqlite3.connect(db_filename)
c = db_conn.cursor()

c.execute('''
  SELECT onestop_id from current_feeds
''')
onestop_ids = c.fetchall()
for row in onestop_ids:
  if row[0].count("-") == 1:
    # TODO if re.match(r"", row[0])
    continue
  elif row[0].count("-") == 2:
    # TODO
    continue
  else:
    print(f"ERROR: improperly formatted Onestop ID: {row[0]}")
    fail_the_build = True

if fail_the_build:
  sys.exit(1)
else:
  sys.exit(0)