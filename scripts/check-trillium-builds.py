#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# ///

"""Compare Trillium's two builds per agency, and say which routes Atlas is missing.

Trillium publishes many clients at two paths:

    https://data.trilliumtransit.com/gtfs/<slug>/<slug>.zip
    https://data.trilliumtransit.com/gtfs/<slug>/<slug>--flex-v2.zip

It is tempting to treat the flex build as the plain one plus demand-response
service, and to switch a registration to it on that basis. **That is true for
about six in ten clients and not the rest.** Measured across the 107 slugs Atlas
references that publish both: 66 superset, 15 identical route sets, 16 disjoint
-- the flex build sharing no route with the plain one -- and 10 overlapping,
where each build has routes the other lacks and neither alone is complete.

So the switch has to be checked per agency, and this is that check. For every
slug where both builds exist it reports what the build Atlas does *not* register
would add, which is the thing that decides whether one feed is enough or both
belong in the registry.

Two failure modes it exists to prevent:

  - switching to the flex build and silently dropping the fixed routes
  - registering only the plain build and silently dropping the dial-a-ride
    service, which for a rural agency may be most of what it runs

A route missing here is not automatically a gap. It is often registered from
another source: several California agencies have their demand-response service
from the state DDS files rather than from Trillium. Check the operator's other
feeds before concluding anything.

Usage:
  cd scripts && uv run check-trillium-builds.py
  cd scripts && uv run check-trillium-builds.py --slug laketahoe-ca-us
  cd scripts && uv run check-trillium-builds.py --json-out ../trillium.json
"""

import argparse
import collections
import concurrent.futures
import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

import atlas_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://data.trilliumtransit.com/gtfs/{s}/{s}{suffix}.zip"
SLUG_RE = re.compile(r"https?://(?:data|oregon-gtfs)\.trilliumtransit\.com/gtfs(?:_data)?/([^/]+)/")
UA = {"User-Agent": "transitland-atlas-check-trillium-builds/1.0"}


def slugs_in(feeds_dir: str) -> set[str]:
    """Every Trillium slug any feed references, current or superseded."""
    out = set()
    for path in sorted(os.listdir(feeds_dir)):
        if not path.endswith(".dmfr.json"):
            continue
        doc = json.load(open(os.path.join(feeds_dir, path), encoding="utf-8"))
        for feed in doc.get("feeds", []):
            urls = feed.get("urls") or {}
            for value in [urls.get("static_current", "")] + list(urls.get("static_historic") or []):
                m = SLUG_RE.match(value or "")
                if m:
                    out.add(m.group(1))
    return out


def exists(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status == 200
    except Exception:
        return False


def routes_of(url: str) -> collections.Counter:
    """Route name -> trip count. Names rather than ids, because the two builds
    do not always agree on ids even when they agree on the service."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        zf = zipfile.ZipFile(io.BytesIO(r.read()))

    def rows(name):
        try:
            return list(csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig")))
        except KeyError:
            return []

    names = {r["route_id"]: (r.get("route_long_name") or r.get("route_short_name") or r["route_id"])
             for r in rows("routes.txt")}
    counts = collections.Counter()
    for t in rows("trips.txt"):
        counts[names.get(t["route_id"], t["route_id"])] += 1
    return counts


def classify(plain: collections.Counter, flex: collections.Counter) -> str:
    p, f = set(plain), set(flex)
    if not p & f:
        return "disjoint"
    if p == f:
        return "identical"
    if p < f:
        return "superset"
    if f < p:
        return "subset"
    return "overlapping"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", action="append", help="check only these slugs; repeatable")
    ap.add_argument("--feeds", default=os.path.join(ROOT, "feeds"))
    ap.add_argument("--json-out")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    db = atlas_registry.load(args.feeds)
    active = atlas_registry.active_urls(db)
    wanted = set(args.slug) if args.slug else slugs_in(args.feeds)
    print(f"{len(wanted)} Trillium slug(s) referenced by feeds/")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers * 2) as ex:
        avail = list(ex.map(lambda s: (s, exists(BASE.format(s=s, suffix="")),
                                       exists(BASE.format(s=s, suffix="--flex-v2"))), sorted(wanted)))
    pairs = [s for s, p, f in avail if p and f]
    print(f"   publish both builds: {len(pairs)}   plain only: {sum(1 for _, p, f in avail if p and not f)}"
          f"   flex only: {sum(1 for _, p, f in avail if f and not p)}")

    def one(slug):
        plain_url, flex_url = BASE.format(s=slug, suffix=""), BASE.format(s=slug, suffix="--flex-v2")
        try:
            plain, flex = routes_of(plain_url), routes_of(flex_url)
        except Exception as e:
            return {"slug": slug, "kind": "fetch-error", "error": str(e)[:60]}
        reg_plain = sorted(active.get(atlas_registry.normalise_url(plain_url), ()))
        reg_flex = sorted(active.get(atlas_registry.normalise_url(flex_url), ()))
        # What the build we do not register would add. That, not the raw
        # relationship, is what decides whether one feed is enough.
        unregistered_adds = {}
        if reg_plain and not reg_flex:
            unregistered_adds = {k: v for k, v in flex.items() if k not in plain}
        elif reg_flex and not reg_plain:
            unregistered_adds = {k: v for k, v in plain.items() if k not in flex}
        return {"slug": slug, "kind": classify(plain, flex),
                "plain_routes": len(plain), "flex_routes": len(flex),
                "registered_plain": reg_plain, "registered_flex": reg_flex,
                "missing_from_registered": unregistered_adds}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(one, pairs))

    print("\nrelationship between the two builds:")
    for kind, n in collections.Counter(r["kind"] for r in results).most_common():
        print(f"   {n:4d}  {kind}")

    gaps = [r for r in results if r.get("missing_from_registered")]
    print(f"\n{len(gaps)} slug(s) where the build Atlas does not register carries routes it lacks.")
    print("Check the operator's other feeds before treating any of these as a gap:\n")
    for r in sorted(gaps, key=lambda x: -len(x["missing_from_registered"])):
        held = ", ".join(r["registered_plain"] or r["registered_flex"])
        side = "plain" if r["registered_plain"] else "flex"
        print(f"   {r['slug']:28s} {r['kind']:12s} registered on {side}: {held}")
        for name, n in sorted(r["missing_from_registered"].items()):
            print(f"        would add: {name}  ({n} trips)")

    if args.json_out:
        json.dump(results, open(args.json_out, "w"), indent=1)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
