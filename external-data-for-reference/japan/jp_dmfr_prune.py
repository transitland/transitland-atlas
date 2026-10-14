"""Drop feed records whose upstream listing has gone away.

Shared by collect-gtfs-data-jp.py and collect-odpt-gtfs.py. Both mint a
static feed id and, when the source offers GTFS Realtime, a companion
"<static id>~rt" feed; both list the pair on an operator record.
"""

import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# A run that would remove more than this share of a file's static feeds is
# more likely a partial or broken upstream response than mass retirement.
MAX_PRUNE_FRACTION = 0.25


def prune_feeds(dmfr: Dict, is_listed: Callable[[Dict], Optional[bool]]) -> List[str]:
    """Remove static feeds for which is_listed() returns False, plus their
    ~rt companions and operator associations. is_listed() returns None for a
    record it cannot map to an upstream key; those are kept. Operators left
    with no associated feeds are removed. Returns the removed feed ids."""
    feeds = dmfr.get("feeds", [])
    static = [f for f in feeds if f.get("spec") == "gtfs"]
    gone = {f["id"] for f in static if is_listed(f) is False}
    if not gone:
        return []
    if len(gone) > MAX_PRUNE_FRACTION * len(static):
        logger.error(
            f"Refusing to prune {len(gone)} of {len(static)} static feeds; "
            "upstream listing looks incomplete")
        return []
    gone |= {f"{fid}~rt" for fid in gone}
    removed = [f["id"] for f in feeds if f["id"] in gone]
    dmfr["feeds"] = [f for f in feeds if f["id"] not in gone]
    operators = []
    for op in dmfr.get("operators", []):
        op["associated_feeds"] = [
            a for a in op.get("associated_feeds", [])
            if a.get("feed_onestop_id") not in gone]
        if op["associated_feeds"]:
            operators.append(op)
        else:
            logger.info(f"Removing operator with no remaining feeds: {op['onestop_id']}")
    dmfr["operators"] = operators
    for fid in removed:
        logger.info(f"Removing feed no longer listed upstream: {fid}")
    return removed
