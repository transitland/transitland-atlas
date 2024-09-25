import os
import csv
import requests

APIKEY = ""
ENDPOINT = ""

def fetch_grapqhl(url, query, params=None, key=None, apikey=None):
    after = 0
    while True:
        print("Fetching... after:", after)
        p = {}
        p.update(params)
        p['after'] = after
        data = requests.post(
            url,
            headers={
                'apikey':apikey,
            },
            json={
                "query":query,
                "variables":p,
            }
        ).json().get('data',{}).get(key, [])
        for ent in data:
            after = ent.get('id')
            yield ent
        if len(data) == 0:
            break

feed_request = """
query($after:Int) {
  feeds(after:$after, limit:100) {
    id
    onestop_id
    spec
    urls {
        static_current
        realtime_vehicle_positions
        realtime_trip_updates
        realtime_alerts
    }
    tags
    feed_state {
        feed_version {
            agencies {
                id
                agency_id
                agency_name
                places {
                    rank
                    adm0_name
                    adm1_name
                }
            }
        }
    }
    feed_fetches(limit: 1) {
      id
      fetch_error
      fetched_at
      response_code
      response_sha1
      response_size
      success
      url
      url_type
    }
    feed_fetches_ok: feed_fetches(limit: 1, where: {success: true}) {
      id
      fetch_error
      fetched_at
      response_code
      response_sha1
      response_size
      success
      url
      url_type
    }
    feed_versions(limit: 1) {
      id
      fetched_at
      earliest_calendar_date
      latest_calendar_date
      sha1
      url
    }
  }
}
"""

def first(v):
    if v and len(v) > 0:
        return v[0]
    return None

def process_feeds(outfile):
    keys = [
        'onestop_id',
        'spec',
        'urls.static_current',
        'urls.realtime_vehicle_positions',
        'urls.realtime_trip_updates',
        'urls.realtime_alerts',
        'agencies',
        'last_fetch_at',
        'last_fetch_success',
        'last_fetch_response_code',
        'last_ok_fetch_at',
        'last_ok_fetch_success',
        'last_ok_fetch_response_code',
        'most_recent_feed_version_sha1',
        'most_recent_feed_version_fetched_at',
    ]
    tags = ['status','unstable_url','manual_import']
    for tag in tags:
        keys.append('tag_'+tag)
    places_count = 5
    for i in range(places_count):
        keys.append(f"adm0_name{i}")
        keys.append(f"adm1_name{i}")        

    with open(outfile, 'w', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        req_args = {}
        for ent in fetch_grapqhl(f"{ENDPOINT}/query", feed_request, req_args, key='feeds', apikey=APIKEY):
            row = {}
            row['onestop_id'] = ent.get('onestop_id')
            row['spec'] = ent.get('spec')
            row['urls.static_current'] = ent.get('urls').get('static_current')
            row['urls.realtime_vehicle_positions'] = ent.get('urls').get('realtime_vehicle_positions')
            row['urls.realtime_trip_updates'] = ent.get('urls').get('realtime_trip_updates')
            row['urls.realtime_alerts'] = ent.get('urls').get('realtime_alerts')
            feed_state_fv = (ent.get('feed_state') or {}).get('feed_version')
            if feed_state_fv:
                agencies = [i.get('agency_name') for i in (feed_state_fv.get('agencies') or [])]
                row['agencies'] = ', '.join(agencies)
                # Include places ordered by rank
                check_places = {}
                for agency in (feed_state_fv.get('agencies') or []):
                    for place in (agency.get('places') or []):
                        key = (place.get('adm0_name'), place.get('adm1_name'))
                        check_places[key] = place.get('rank')
                ranked_places = [i[0] for i in sorted(check_places.items(), reverse=True, key=lambda x:x[1])]
                for (i,check_place) in enumerate(ranked_places[:places_count]):
                    row[f"adm0_name{i}"] = check_place[0]
                    row[f"adm1_name{i}"] = check_place[1]
            if first(ent.get('feed_fetches')):
                fetch = first(ent.get('feed_fetches'))
                row['last_fetch_at'] = fetch.get('fetched_at')
                row['last_fetch_success'] = fetch.get('success')
                row['last_fetch_response_code'] = fetch.get('response_code')
            if first(ent.get('feed_fetches_ok')):
                fetch = first(ent.get('feed_fetches_ok'))
                row['last_ok_fetch_at'] = fetch.get('fetched_at')
                row['last_ok_fetch_success'] = fetch.get('success')
                row['last_ok_fetch_response_code'] = fetch.get('response_code')
            if first(ent.get('feed_versions')):
                fv = first(ent.get('feed_versions'))
                row['most_recent_feed_version_sha1'] = fv.get('sha1')
                row['most_recent_feed_version_fetched_at'] = fv.get('fetched_at') 
            for tag in tags:                
                row['tag_'+tag] = (ent.get('tags') or {}).get(tag)
            writer.writerow(row)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog = 'Feed maintenance dump')
    parser.add_argument('--endpoint', default="https://api.transit.land/api/v2", help="TL endpoint")
    parser.add_argument('OUTFILE', help='Output csv file')
    args = parser.parse_args()
    APIKEY = os.environ.get('TRANSITLAND_API_KEY', '')
    ENDPOINT = args.endpoint
    process_feeds(args.OUTFILE)
