"""Query the Atlas registry as a database, via transitland-lib.

The questions these scripts ask are relational: which operators does this feed
have, which feed uses this URL, which operators have a feed tagged
unstable_url. Answering them with parallel dicts means maintaining several
indexes and then inverting them, which is where the bugs were.

`transitland sync` already loads DMFR into SQLite, and `validate-feeds.py` in
CI does exactly that, so this reuses it rather than re-parsing the files. That
also means the operator-to-feed links are the ones transitland-lib resolves,
including associations reached through `associated_feeds`, rather than an
approximation of them.

Nothing is persisted: the database is a scratch file unless a path is given.

Requires the `transitland` binary on PATH. Stdlib only, so a PEP 723 script can
import it without declaring a dependency.
"""

import glob
import json
import os
import sqlite3
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

# Feed URL fields holding a superseded URL, which is retained deliberately and
# is not in use. Everything else in `urls` counts as active.
HISTORIC_SUFFIX = "_historic"

# NTD publishes ids zero-padded to five characters. Atlas has historically been
# inconsistent, so both sides are normalised before joining.
NTD_WIDTH = 5


def load(feeds_dir: str, db_path: str | None = None) -> sqlite3.Connection:
    """Sync every *.dmfr.json under `feeds_dir` into SQLite and return a connection."""
    if db_path is None:
        db_path = os.path.join(tempfile.mkdtemp(prefix="atlas-registry-"), "registry.db")
    files = sorted(glob.glob(os.path.join(feeds_dir, "*.dmfr.json")))
    if not files:
        raise SystemExit(f"no DMFR files found in {feeds_dir}")
    proc = subprocess.run(
        ["transitland", "sync", "--hide-unseen", "--hide-unseen-operators",
         f"--dburl=sqlite3://{db_path}", *files],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"transitland sync failed: {proc.stderr.strip()[:400]}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def feed_exists(db, feed_id: str) -> bool:
    return db.execute("SELECT 1 FROM current_feeds WHERE onestop_id = ?",
                      (feed_id,)).fetchone() is not None


def operator_exists(db, onestop_id: str) -> bool:
    return db.execute("SELECT 1 FROM current_operators WHERE onestop_id = ?",
                      (onestop_id,)).fetchone() is not None


def feeds_using(db, url: str) -> set[str]:
    """Feeds actively using this URL. Superseded URLs are deliberately excluded."""
    rows = db.execute(
        "SELECT DISTINCT f.onestop_id AS id FROM current_feeds f, json_each(f.urls) j "
        f"WHERE j.key NOT LIKE '%{HISTORIC_SUFFIX}' AND j.value = ?", (url,))
    return {r["id"] for r in rows}


def operator_feeds(db, onestop_id: str, spec: str | None = None) -> set[str]:
    """Feeds this operator is associated with, optionally of one spec only.

    Filtering by spec answers "does this agency have realtime at all", which is
    not the same question as "is this realtime URL registered": an agency can
    have a gtfs-rt feed built from a different vendor endpoint entirely.
    """
    sql = ("SELECT f.onestop_id AS id FROM current_operators_in_feed oif "
           "JOIN current_operators o ON o.id = oif.operator_id "
           "JOIN current_feeds f ON f.id = oif.feed_id WHERE o.onestop_id = ?")
    params: tuple = (onestop_id,)
    if spec:
        sql += " AND f.spec = ?"
        params += (spec,)
    return {r["id"] for r in db.execute(sql, params)}


def operators_of(db, feed_id: str) -> set[str]:
    rows = db.execute(
        "SELECT o.onestop_id AS id FROM current_operators_in_feed oif "
        "JOIN current_operators o ON o.id = oif.operator_id "
        "JOIN current_feeds f ON f.id = oif.feed_id WHERE f.onestop_id = ?", (feed_id,))
    return {r["id"] for r in rows}


def unstable_feeds_of(db, onestop_id: str) -> set[str]:
    rows = db.execute(
        "SELECT f.onestop_id AS id FROM current_operators_in_feed oif "
        "JOIN current_operators o ON o.id = oif.operator_id "
        "JOIN current_feeds f ON f.id = oif.feed_id "
        "WHERE o.onestop_id = ? AND json_extract(f.feed_tags, '$.unstable_url') = 'true'",
        (onestop_id,))
    return {r["id"] for r in rows}


def normalise_url(url: str) -> str:
    """Collapse the differences that do not change which file is served.

    Scheme, host case, a trailing slash and path case are all routinely
    inconsistent between what an agency declares to NTD and what Atlas records.
    Matching on the raw string reports those as unregistered URLs: over the NTD
    release, normalising this way moved 70 agencies out of the unmatched bucket.

    Path case is folded too, which is not strictly safe -- some servers are
    case-sensitive -- but this is only ever used to decide whether two URLs are
    worth a human comparison, never to build a URL to fetch.
    """
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return urlunsplit(("", host, parts.path.rstrip("/"), parts.query, "")).lower()


def normalise_ntd_id(value: str) -> str:
    """NTD publishes five-character zero-padded ids; Atlas has not been consistent."""
    value = (value or "").strip()
    return value.zfill(NTD_WIDTH) if value.isdigit() else value


def split_ids(value) -> list[str]:
    """Split a tag holding several external ids into one id each.

    An operator that absorbed several NTD reporters, or a realtime feed standing
    for three of another registry's per-endpoint records, carries them in one
    string. Both a comma and a semicolon separate them: no external id we carry
    contains either character, so accepting both costs nothing and means a tag
    written with the wrong one still joins instead of silently matching nothing.

    Empty segments are dropped, so trailing separators and stray whitespace are
    harmless.
    """
    if not value:
        return []
    out = []
    for part in str(value).replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _url_index(db, historic: bool) -> dict[str, set[str]]:
    """Normalised URL -> feeds, over either the active or the superseded fields.

    `static_historic` holds an *array*, so iterating `urls` alone yields the
    whole JSON array as a single value. Each element has to be unnested or the
    superseded URLs never match anything.
    """
    op = "LIKE" if historic else "NOT LIKE"
    out: dict[str, set[str]] = {}
    for row in db.execute(
            "SELECT f.onestop_id AS id, j.value AS value, j.type AS kind "
            f"FROM current_feeds f, json_each(f.urls) j WHERE j.key {op} '%{HISTORIC_SUFFIX}'"):
        values = []
        if row["kind"] == "array":
            try:
                values = [v for v in json.loads(row["value"]) if isinstance(v, str)]
            except (TypeError, ValueError):
                values = []
        elif row["value"]:
            values = [row["value"]]
        for url in values:
            out.setdefault(normalise_url(url), set()).add(row["id"])
    return out


def active_urls(db) -> dict[str, set[str]]:
    """Normalised active URL -> feeds using it. Superseded URLs are excluded."""
    return _url_index(db, historic=False)


def historic_urls(db) -> dict[str, set[str]]:
    """Normalised superseded URL -> feeds that record it. A source still
    declaring one of these is behind us, not ahead."""
    return _url_index(db, historic=True)


def operators_by_ntd_id(db) -> dict[str, set[str]]:
    """Normalised `us_ntd_id` -> operators carrying it.

    The tag holds a separated list on operators that absorbed several
    reporters, so each id is indexed separately. See `split_ids`.
    """
    out: dict[str, set[str]] = {}
    for row in db.execute("SELECT onestop_id, operator_tags FROM current_operators "
                          "WHERE operator_tags IS NOT NULL"):
        try:
            tags = json.loads(row["operator_tags"])
        except (TypeError, ValueError):
            continue
        raw = tags.get("us_ntd_id") if isinstance(tags, dict) else None
        if not raw:
            continue
        for part in split_ids(raw):
            key = normalise_ntd_id(part)
            if key:
                out.setdefault(key, set()).add(row["onestop_id"])
    return out


def feeds_by_calitp_dataset(db) -> dict[str, set[str]]:
    """Cal-ITP dataset record id -> feeds carrying it.

    The tag holds a separated list on realtime feeds, because that source treats
    each endpoint as its own dataset while a feed here holds all three.
    """
    out: dict[str, set[str]] = {}
    for row in db.execute("SELECT onestop_id, feed_tags FROM current_feeds WHERE feed_tags IS NOT NULL"):
        try:
            tags = json.loads(row["feed_tags"])
        except (TypeError, ValueError):
            continue
        raw = tags.get("calitp_dataset_id") if isinstance(tags, dict) else None
        for part in split_ids(raw):
            out.setdefault(part, set()).add(row["onestop_id"])
    return out


def operators_by_calitp_org(db) -> dict[str, set[str]]:
    """Cal-ITP organization record id -> operators carrying it."""
    out: dict[str, set[str]] = {}
    for row in db.execute("SELECT onestop_id, operator_tags FROM current_operators "
                          "WHERE operator_tags IS NOT NULL"):
        try:
            tags = json.loads(row["operator_tags"])
        except (TypeError, ValueError):
            continue
        raw = tags.get("calitp_organization_id") if isinstance(tags, dict) else None
        for part in split_ids(raw):
            out.setdefault(part, set()).add(row["onestop_id"])
    return out


def malformed_ntd_ids(db) -> list[tuple[str, str]]:
    """Operators whose `us_ntd_id` will not join a live NTD extract as written.

    Returns (operator, raw tag value). Unpadded numerics are the common case and
    are silently corrected by `normalise_ntd_id`; they are still worth fixing at
    the source, since anything joining on the raw tag misses them.
    """
    bad = []
    for row in db.execute("SELECT onestop_id, operator_tags FROM current_operators "
                          "WHERE operator_tags IS NOT NULL"):
        try:
            tags = json.loads(row["operator_tags"])
        except (TypeError, ValueError):
            continue
        raw = tags.get("us_ntd_id") if isinstance(tags, dict) else None
        for part in split_ids(raw):
            if part.isdigit() and len(part) != NTD_WIDTH:
                bad.append((row["onestop_id"], str(raw)))
                break
    return sorted(bad)


def file_of_feed(db) -> dict[str, str]:
    return {r["onestop_id"]: r["file"] for r in
            db.execute("SELECT onestop_id, file FROM current_feeds")}


def feed_count(db) -> int:
    return db.execute("SELECT count(*) AS n FROM current_feeds").fetchone()["n"]
