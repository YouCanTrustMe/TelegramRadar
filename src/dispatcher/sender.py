import asyncio
import json
import logging

import aiohttp
from pyrogram import Client

from src.config import settings

log = logging.getLogger(__name__)

# Transport failures are logged to a child logger that AdminAlertLogHandler
# ignores: reporting "the Bot API is unreachable" through the Bot API only
# multiplies the outage. They stay in stdout and data/logs/radar.log.
log_transport = logging.getLogger(f"{__name__}.transport")

bot = Client(
    "sessions/radar_bot",
    bot_token=settings.telegram_bot_token,
    api_id=settings.telegram_api_id,
    api_hash=settings.telegram_api_hash,
)

_BOT_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)

_SEND_RETRIES = 3
_SEND_BACKOFF = 2

# Only failures that happened before the request reached Telegram may be retried;
# anything raised while awaiting the response (read/total timeout, dropped
# connection) is ambiguous — Telegram may have already accepted the message, and
# a retry then delivers a duplicate. sendMessage has no idempotency key.
_RETRYABLE = (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError)

_session: aiohttp.ClientSession | None = None


class SendFailed(Exception):
    """sendMessage did not return 200; whether Telegram delivered it is unknown."""


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT)
    return _session


def _retry_after(body: str, fallback: int) -> int:
    try:
        return int(json.loads(body).get("parameters", {}).get("retry_after", fallback))
    except (ValueError, TypeError):
        return fallback


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def send_to(
    chat_id: int,
    text: str,
    disable_notification: bool = False,
    reply_markup: dict | None = None,
    retry: bool = True,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": disable_notification,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    attempts = _SEND_RETRIES if retry else 1
    for attempt in range(1, attempts + 1):
        try:
            async with _get_session().post(f"{_BOT_API}/sendMessage", json=payload) as resp:
                if resp.status == 200:
                    return
                body = await resp.text()
                if resp.status == 429 and attempt < attempts:
                    retry_after = _retry_after(body, _SEND_BACKOFF * attempt)
                    log.warning(
                        "Bot API sendMessage rate-limited (attempt %d/%d), retrying in %ss: %s",
                        attempt, attempts, retry_after, body,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                log.error("Bot API sendMessage (send_to) failed: %s %s", resp.status, body)
                raise SendFailed(f"HTTP {resp.status}: {body[:200]}")
        except _RETRYABLE as exc:
            if attempt < attempts:
                delay = _SEND_BACKOFF * attempt
                log_transport.warning(
                    "Bot API sendMessage attempt %d/%d could not connect (chat=%s), retrying in %ss: %r",
                    attempt, attempts, chat_id, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            log_transport.error(
                "Bot API sendMessage gave up after %d attempts (chat=%s, not delivered): %r",
                attempts, chat_id, exc,
            )
            raise SendFailed(f"could not connect after {attempts} attempts: {exc!r}") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log_transport.error(
                "Bot API sendMessage failed after the request was sent (chat=%s, attempt %d/%d): %r "
                "— not retrying, Telegram may have accepted it",
                chat_id, attempt, attempts, exc,
            )
            raise SendFailed(f"no response from Bot API: {exc!r}") from exc
        except SendFailed:
            raise
        except Exception as exc:
            # Anything else out of the client (a closed session, an OSError aiohttp
            # did not wrap) is still a failed send. Letting it escape as itself
            # would skip the resend queue and lose the alert outright.
            log_transport.exception(
                "Bot API sendMessage raised an unexpected error (chat=%s, attempt %d/%d)",
                chat_id, attempt, attempts,
            )
            raise SendFailed(f"unexpected sender error: {exc!r}") from exc


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
