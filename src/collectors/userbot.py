import asyncio
import logging

from pyrogram import Client

from src.config import settings

log = logging.getLogger(__name__)

userbot = Client(
    "sessions/radar_userbot",
    api_id=settings.telegram_api_id,
    api_hash=settings.telegram_api_hash,
    phone_number=settings.telegram_phone,
)


async def keep_userbot_online() -> None:
    """Ping Telegram every 4 min so the radar account shows as online while it polls."""
    from pyrogram.raw.functions.account import UpdateStatus
    log.info("Userbot online keepalive started (interval=240s)")
    while True:
        try:
            await userbot.invoke(UpdateStatus(offline=False))
            log.debug("Userbot online status refreshed")
        except Exception as exc:
            log.warning("Online keepalive ping failed: %s", exc)
        await asyncio.sleep(240)
