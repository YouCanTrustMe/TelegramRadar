import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
from src.radar.digest import send_muted_digest
from src.radar.verify import verify_radar_chats

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=settings.radar_timezone)

    _scheduler.add_job(
        verify_radar_chats,
        CronTrigger(hour=3, minute=30, timezone=settings.radar_timezone),
        id="radar_verify",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        send_muted_digest,
        CronTrigger(
            day_of_week=settings.radar_digest_day,
            hour=settings.radar_digest_hour,
            timezone=settings.radar_timezone,
        ),
        id="radar_muted_digest",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    log.info(
        "Scheduler started (radar_verify daily 03:30; muted_digest %s %02d:00 %s)",
        settings.radar_digest_day, settings.radar_digest_hour, settings.radar_timezone,
    )
