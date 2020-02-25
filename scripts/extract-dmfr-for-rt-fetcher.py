import json
import subprocess
import sys
import os
import sqlite3
import tempfile

with tempfile.TemporaryDirectory() as tmpdirname:
  db_filename = f"{tmpdirname}/feed-validation.db"
  os.system(f"rm -f {db_filename}")
  sync_command = f"gotransit dmfr sync -dburl=sqlite3://{db_filename} ../feeds/*.dmfr.json"
  subprocess.check_output(sync_command, shell=True)

  db_conn = sqlite3.connect(db_filename)
  db_conn.row_factory = sqlite3.Row
  c = db_conn.cursor()

  c.execute('''
    SELECT * from current_feeds WHERE spec='gtfs-rt'
  ''')

  feeds = []

  for row in c:
    feed = {
      'spec': row['spec'],
      'id': row['onestop_id'],
      'urls': json.loads(row['urls']),
      'authorization': json.loads(row['auth']),
      'license': json.loads(row['license'])
    }
    feeds.append(feed)

  dmfr = {
    '$schema': 'https://dmfr.transit.land/json-schema/dmfr.schema-v0.1.2.json',
    'feeds': feeds,
    'license_spdx_identifier': 'CDLA-Permissive-1.0'
  }

  print(json.dumps(dmfr))
