# TelegramRadar

Real-time keyword radar for Telegram chats. A standalone userbot + bot pair that
watches a set of chats and DMs the admin the moment a monitored keyword appears.

Split out of the TelegramSentinel project so it runs as its own process, on its
own Telegram account and bot token, against its own database.

## What it does

- Polls a watchlist of chats every 60s via a Pyrogram userbot (`get_chat_history`)
- Matches messages against per-chat keywords, with obfuscation-resistant
  normalization (NFKC, homoglyphs, stretched letters, separators)
- Alerts the admin over the Bot API with the matched keyword, author, link and an
  expandable quote of the message
- Blacklisted senders still match but are delivered silently
- A daily verify job heals renamed/missing chats and warns on lost membership

All configuration is done through the bot UI: `/radar` opens the menu
(Keywords · Watchlist · Blacklist · Status).

## Architecture

```
[watchlist chats] → radar_collector (userbot, 60s) → matcher → handlers → admin DM (Bot API)
                                                                      ↑
scheduler (03:30 daily) → verify → heal/alert ──────────────────────┘
```

- **userbot** (`sessions/radar_userbot`) — reads chats. Own Telegram account.
- **bot** (`sessions/radar_bot`) — admin UI + alert delivery. Own bot token.
- **SQLite** at `data/radar.db` — `radar_keywords`, `radar_chats`,
  `radar_keyword_chats`, `radar_blacklist`, `radar_alert_log`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill api id/hash/phone (radar account), bot token, admin id

# one-time interactive login for the userbot account
python scripts/generate_session.py

# optional: import existing radar config from the old combined DB
python scripts/migrate_from_sentinel.py /path/to/sentinel.db data/radar.db

python main.py
```

The radar account must be a member of every watched chat. Adding a chat through
the bot (`/radar` → Watchlist → Add) makes the userbot join it automatically;
chats imported from the migration script must be joined by that account.

## Docker

```bash
docker compose up --build -d
```

Data persists in `./data`, the session files in `./sessions` (generate the
userbot session once interactively before the first deploy).
