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
import os
import sqlite3
import subprocess
import tempfile

# Feed URL fields holding a superseded URL, which is retained deliberately and
# is not in use. Everything else in `urls` counts as active.
HISTORIC_SUFFIX = "_historic"


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


def operator_feeds(db, onestop_id: str) -> set[str]:
    rows = db.execute(
        "SELECT f.onestop_id AS id FROM current_operators_in_feed oif "
        "JOIN current_operators o ON o.id = oif.operator_id "
        "JOIN current_feeds f ON f.id = oif.feed_id WHERE o.onestop_id = ?", (onestop_id,))
    return {r["id"] for r in rows}


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


def file_of_feed(db) -> dict[str, str]:
    return {r["onestop_id"]: r["file"] for r in
            db.execute("SELECT onestop_id, file FROM current_feeds")}


def feed_count(db) -> int:
    return db.execute("SELECT count(*) AS n FROM current_feeds").fetchone()["n"]
