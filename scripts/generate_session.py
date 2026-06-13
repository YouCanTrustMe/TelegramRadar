"""Interactive one-time login for the radar userbot account.

Run this once on a machine where you can enter the phone-confirmation code.
It creates sessions/radar_userbot.session, which the container then reuses.

Reads TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE from .env.

Usage:
    python scripts/generate_session.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram import Client  # noqa: E402

from src.config import settings  # noqa: E402

Path("sessions").mkdir(exist_ok=True)

with Client(
    "sessions/radar_userbot",
    api_id=settings.telegram_api_id,
    api_hash=settings.telegram_api_hash,
    phone_number=settings.telegram_phone,
) as app:
    me = app.get_me()
    print(f"Logged in as {me.first_name} (@{me.username}) id={me.id}")
    print("Session saved to sessions/radar_userbot.session")
