import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
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

    _scheduler.start()
    log.info("Scheduler started (radar_verify daily at 03:30 %s)", settings.radar_timezone)
