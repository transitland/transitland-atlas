import subprocess
import sys
import os
import sqlite3

db_filename = 'feed-validation.db'
os.system(f"rm -f {db_filename}")
# TODO: source gotransit binary from GH release
sync_command = f"./gotransit dmfr sync -dburl=sqlite3://{db_filename} ../feeds/*.dmfr.json"
sync_log = subprocess.check_output(sync_command, shell=True)

for log_line in sync_log.splitlines():
  log_line = log_line.decode("utf-8")
  if log_line.find("updated feed") > -1:
    print(f"ERROR: duplicate feed found at: {log_line}")
    sys.exit(1)  

# db_conn = sqlite3.connect(db_filename)
# c = db_conn.cursor()

# c.execute('''
#   SELECT COUNT(*) from current_feeds GROUP BY current_feeds.id HAVING(COUNT(id) > 1)
# ''')