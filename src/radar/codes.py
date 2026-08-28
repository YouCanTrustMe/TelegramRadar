"""Housekeeping for the seen-code table.

Every drop code the radar sees is remembered so a repost stays quiet. Sightings
are kept for twice the dedup window: a row must outlive the window it guards, or
purging it would let an already-alerted code ring again.
"""
import logging

from src.config import settings
from src.db.radar import purge_seen_codes

log = logging.getLogger(__name__)

_RETENTION_FACTOR = 2


async def purge_stale_codes() -> None:
    keep_days = settings.radar_code_dedup_days * _RETENTION_FACTOR
    removed = await purge_seen_codes(keep_days)
    log.info("Seen codes purged: removed=%d retention=%dd", removed, keep_days)
