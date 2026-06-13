"""One-time data migration: copy the radar_* tables out of the old combined
TelegramSentinel database into this project's standalone radar.db.

Row ids are preserved so the radar_keyword_chats links stay intact.

Usage:
    python scripts/migrate_from_sentinel.py /path/to/sentinel.db [data/radar.db]
"""
import sqlite3
import sys
from pathlib import Path

_TABLES = {
    "radar_keywords": ["id", "keyword", "created_at"],
    "radar_chats": [
        "id", "chat_ref", "title", "chat_id", "status",
        "last_verified_at", "last_seen_msg_id", "last_message_at", "created_at",
    ],
    "radar_blacklist": ["id", "user_id", "created_at"],
    "radar_alert_log": [
        "id", "keyword", "chat_ref", "author_id",
        "message_text", "message_url", "alerted_at",
    ],
    "radar_keyword_chats": ["keyword_id", "chat_id", "created_at"],
}

_SCHEMA = Path(__file__).resolve().parent.parent / "src" / "db" / "migrations" / "001_init.sql"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    source = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/radar.db")
    if not source.exists():
        sys.exit(f"Source DB not found: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(dest)
    db.executescript(_SCHEMA.read_text())
    db.execute("ATTACH DATABASE ? AS src", (str(source),))

    for table, cols in _TABLES.items():
        col_list = ", ".join(cols)
        try:
            cur = db.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) "
                f"SELECT {col_list} FROM src.{table}"
            )
            print(f"{table}: copied {cur.rowcount} row(s)")
        except sqlite3.OperationalError as exc:
            print(f"{table}: skipped ({exc})")

    db.commit()
    db.execute("DETACH DATABASE src")
    db.close()
    print(f"Done → {dest}")


if __name__ == "__main__":
    main()
