#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema"]
# ///

"""Validate the experimental evaluations/ sidecar.

Checks internal consistency of each evaluation file and its cross-references
into feeds/. Offline and deterministic by design: it never contacts the
Transitland API or fetches a feed URL, so a feed that happens to be erroring
cannot fail this lint. Re-checking whether a recorded finding still holds is a
separate job.

Errors fail the build. Warnings are advisory and are printed but do not.
"""

import glob
import json
import os
import sys
from datetime import date

import jsonschema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDS_DIR = os.path.join(ROOT, "feeds")
EVAL_DIR = os.path.join(ROOT, "evaluations")

errors: list[str] = []
warnings: list[str] = []
notes: list[str] = []


def err(f: str, msg: str) -> None:
    errors.append(f"{os.path.relpath(f, ROOT)}: {msg}")


def warn(f: str, msg: str) -> None:
    warnings.append(f"{os.path.relpath(f, ROOT)}: {msg}")


def note(f: str, msg: str) -> None:
    notes.append(f"{os.path.relpath(f, ROOT)}: {msg}")


def parse_date(value: str):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- load feeds

operators: set[str] = set()
feed_ids: set[str] = set()
# every URL the registry actively uses -> the feeds that use it
registered_urls: dict[str, set[str]] = {}
# operator -> feeds tagged unstable_url
unstable_by_operator: dict[str, list[str]] = {}
feed_operators: dict[str, list[str]] = {}

for path in sorted(glob.glob(os.path.join(FEEDS_DIR, "*.dmfr.json"))):
    try:
        registry = json.load(open(path))
    except json.JSONDecodeError as e:
        err(path, f"invalid JSON: {e}")
        continue

    def note_operator(op, feed_id=None):
        oid = op.get("onestop_id")
        if not oid:
            return
        operators.add(oid)
        if feed_id:
            feed_operators.setdefault(feed_id, []).append(oid)
        for assoc in op.get("associated_feeds") or []:
            fid = assoc.get("feed_onestop_id")
            if fid:
                feed_operators.setdefault(fid, []).append(oid)

    for feed in registry.get("feeds", []):
        fid = feed.get("id")
        if not fid:
            continue
        feed_ids.add(fid)
        for key, value in (feed.get("urls") or {}).items():
            if key.endswith("_historic"):
                continue  # historic URLs are deliberately not current
            for url in [value] if isinstance(value, str) else value or []:
                registered_urls.setdefault(url, set()).add(fid)
        if (feed.get("tags") or {}).get("unstable_url") == "true":
            unstable_by_operator.setdefault(fid, []).append(fid)
        for op in feed.get("operators") or []:
            note_operator(op, fid)
    for op in registry.get("operators") or []:
        note_operator(op)

# map unstable feeds onto their operators
unstable_operators: dict[str, list[str]] = {}
for fid in list(unstable_by_operator):
    for oid in feed_operators.get(fid, []):
        unstable_operators.setdefault(oid, []).append(fid)

# operator -> feeds associated with it, so "in use" can be judged per operator
operator_feeds: dict[str, set[str]] = {}
for fid, oids in feed_operators.items():
    for oid in oids:
        operator_feeds.setdefault(oid, set()).add(fid)

# --------------------------------------------------------------- load schema

schema_path = os.path.join(EVAL_DIR, "schema.json")
if not os.path.exists(schema_path):
    print(f"ERROR: {schema_path} not found")
    sys.exit(1)
schema = json.load(open(schema_path))
validator = jsonschema.Draft202012Validator(schema)

# ------------------------------------------------------------ check each file

eval_files = sorted(glob.glob(os.path.join(EVAL_DIR, "o-*.json"))
                    + glob.glob(os.path.join(EVAL_DIR, "f-*.json")))
seen_urls: dict[str, str] = {}
today = date.today()
overdue: list[str] = []

for path in eval_files:
    try:
        doc = json.load(open(path))
    except json.JSONDecodeError as e:
        err(path, f"invalid JSON: {e}")
        continue

    for e in sorted(validator.iter_errors(doc), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        err(path, f"schema: {loc}: {e.message}")

    # A file is keyed by an operator where one exists, otherwise by a feed,
    # because most feeds rely on generated operators.
    oid = doc.get("operator_onestop_id")
    subject_feed = doc.get("feed_onestop_id")
    subject = oid or subject_feed
    if not subject:
        continue

    stem = os.path.basename(path).removesuffix(".json")
    if stem != subject:
        err(path, f"filename does not match {'operator' if oid else 'feed'}_onestop_id {subject!r}")
    if oid and oid not in operators:
        err(path, f"operator {oid!r} does not exist in feeds/")
    if subject_feed:
        if subject_feed not in feed_ids:
            err(path, f"feed {subject_feed!r} does not exist in feeds/")
        elif feed_operators.get(subject_feed):
            err(path, f"feed {subject_feed!r} has operator record(s) "
                      f"{sorted(set(feed_operators[subject_feed]))}; key this file on the operator instead")

    urls_here: set[str] = set()
    for i, cand in enumerate(doc.get("candidates") or []):
        url = cand.get("url", "")
        where = f"candidates[{i}] {url}"

        if url in urls_here:
            err(path, f"{where}: duplicate url within this file")
        urls_here.add(url)
        if url in seen_urls and seen_urls[url] != path:
            warn(path, f"{where}: also evaluated in {os.path.basename(seen_urls[url])}")
        seen_urls.setdefault(url, path)

        for fid in cand.get("relates_to") or []:
            if fid not in feed_ids:
                err(path, f"{where}: relates_to feed {fid!r} does not exist in feeds/")

        decided = parse_date(cand.get("decided_on", ""))
        if decided is None:
            err(path, f"{where}: decided_on is not a valid date")
        elif decided > today:
            err(path, f"{where}: decided_on {decided} is in the future")

        if "recheck_after" in cand:
            recheck = parse_date(cand["recheck_after"])
            if recheck is None:
                err(path, f"{where}: recheck_after is not a valid date")
            else:
                if decided and recheck < decided:
                    err(path, f"{where}: recheck_after {recheck} precedes decided_on {decided}")
                if recheck <= today:
                    overdue.append(f"{oid}  {url}  (due {recheck}, {cand.get('decision')})")

        # Contradictions between the sidecar and the registry, judged per
        # operator: a decision here is about this agency only, and the same URL
        # may legitimately be another agency's registered feed.
        decision = cand.get("decision")
        using_feeds = registered_urls.get(url, set())
        scope = operator_feeds.get(oid, set()) if oid else {subject_feed}
        ours = using_feeds & scope
        theirs = using_feeds - ours

        if decision in ("not_used", "deferred", "unavailable") and ours:
            err(path, f"{where}: recorded as {decision!r} but in use by this operator's {', '.join(sorted(ours))}")
        if decision == "used" and not ours:
            err(path, f"{where}: recorded as 'used' but no feed in scope uses this url")
        if theirs and decision != "used":
            note(path, f"{where}: registered for another operator as {', '.join(sorted(theirs))}")

    for i, w in enumerate(doc.get("watch") or []):
        where = f"watch[{i}] {w.get('page','')}"
        for fid in w.get("publishes") or []:
            if fid not in feed_ids:
                err(path, f"{where}: publishes feed {fid!r} does not exist in feeds/")
        if "last_checked" in w and parse_date(w["last_checked"]) is None:
            err(path, f"{where}: last_checked is not a valid date")

    # advisory: unstable_url feeds with nowhere recorded to look for a replacement
    if oid and oid in unstable_operators and not (doc.get("watch") or []):
        feeds = ", ".join(sorted(set(unstable_operators[oid])))
        warn(path, f"operator has unstable_url feeds ({feeds}) but no watch entry")

# ----------------------------------------------------------------- reporting

print(f"checked {len(eval_files)} evaluation file(s) against {len(feed_ids)} feeds")

if overdue:
    print(f"\n{len(overdue)} candidate(s) due for recheck:")
    for line in sorted(overdue):
        print(f"  {line}")

if notes:
    print(f"\n{len(notes)} note(s):")
    for line in notes:
        print(f"  {line}")

if warnings:
    print(f"\n{len(warnings)} warning(s):")
    for line in warnings:
        print(f"  {line}")

if errors:
    print(f"\n{len(errors)} error(s):")
    for line in errors:
        print(f"  {line}")
    sys.exit(1)

print("\nok -- evaluations valid")
