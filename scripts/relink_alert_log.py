"""Re-key alert-log history that a past chat rename orphaned.

The log used to be keyed by chat_ref alone, so every rename split a chat's
history in two: the rows written under the old @username stopped belonging to
any chat, and dropped out of the quiet log, the digest and the sender pickers.
Migration 004 adopts what it can match by name, and verify.py now re-keys on
every future rename — but rows whose @username is long gone need to be pointed
at their chat by hand, because nothing in the data still says where they came
from.

    python scripts/relink_alert_log.py                     # report orphans
    python scripts/relink_alert_log.py @oldname=7 ...      # assign them
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402


def main(args: list[str]) -> int:
    db = sqlite3.connect(settings.database_path)
    db.row_factory = sqlite3.Row

    chats = {r["id"]: r for r in db.execute("SELECT id, chat_ref, title FROM radar_chats")}
    if not args:
        print(f"{settings.database_path}\n\nWatched chats:")
        for c in chats.values():
            print(f"  id={c['id']:<4} {c['chat_ref']:<24} {c['title'] or ''}")
        rows = db.execute(
            "SELECT chat_ref, COUNT(*) c, MAX(alerted_at) last FROM radar_alert_log "
            "WHERE chat_db_id IS NULL GROUP BY chat_ref ORDER BY c DESC"
        ).fetchall()
        if not rows:
            print("\nNo orphaned rows.")
            return 0
        print("\nOrphaned log rows (no chat):")
        for r in rows:
            print(f"  {r['chat_ref']:<24} {r['c']:>5} rows   last {r['last']}")
        print("\nAssign the ones that belong to a watched chat, e.g.:")
        print(f"  python {Path(__file__).name} {rows[0]['chat_ref']}=<chat id>")
        return 0

    total = 0
    for arg in args:
        ref, _, id_s = arg.partition("=")
        if not id_s.isdigit() or int(id_s) not in chats:
            print(f"✗ {arg}: expected @oldref=<id of a watched chat>")
            return 1
        entry_id = int(id_s)
        cur = db.execute(
            "UPDATE radar_alert_log SET chat_db_id = ? WHERE chat_ref = ? AND chat_db_id IS NULL",
            (entry_id, ref),
        )
        print(f"✓ {ref} → id={entry_id} ({chats[entry_id]['chat_ref']}): {cur.rowcount} row(s)")
        total += cur.rowcount
    db.commit()
    print(f"\nRe-keyed {total} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
