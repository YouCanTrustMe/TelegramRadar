import logging

import aiohttp
from pyrogram import Client

from src.config import settings

log = logging.getLogger(__name__)

bot = Client(
    "sessions/radar_bot",
    bot_token=settings.telegram_bot_token,
    api_id=settings.telegram_api_id,
    api_hash=settings.telegram_api_hash,
)

_BOT_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def send_to(chat_id: int, text: str, disable_notification: bool = False) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": disable_notification,
    }
    async with _get_session().post(f"{_BOT_API}/sendMessage", json=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            log.error("Bot API sendMessage (send_to) failed: %s %s", resp.status, body)


async def send_document(chat_id: int, file_path: str, filename: str | None = None) -> None:
    url = f"{_BOT_API}/sendDocument"
    with open(file_path, "rb") as f:
        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        data.add_field("document", f, filename=filename or file_path.rsplit("/", 1)[-1])
        async with _get_session().post(url, data=data) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error("Bot API sendDocument failed: %s %s", resp.status, body)
                raise RuntimeError(f"sendDocument failed: {resp.status}")
    log.debug("Document sent: %s -> chat=%s", file_path, chat_id)
