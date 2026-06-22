"""Radar storage: keywords, monitored chats, keyword↔chat links, per-source
sender filtering rules, the alert/quiet log and chat-silence tracking."""
import aiosqlite

from src.db.base import get_db


async def get_radar_keywords() -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute("SELECT * FROM radar_keywords ORDER BY keyword") as cur:
            return await cur.fetchall()


async def add_radar_keyword(keyword: str) -> bool:
    async with get_db() as db:
        try:
            await db.execute("INSERT INTO radar_keywords (keyword) VALUES (?)", (keyword,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_radar_keyword(keyword_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM radar_keywords WHERE id = ?", (keyword_id,))
        await db.commit()
        return cur.rowcount > 0


async def get_radar_chats() -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute("SELECT * FROM radar_chats ORDER BY id") as cur:
            return await cur.fetchall()


async def add_radar_chat(chat_ref: str, title: str | None, chat_id: int | None = None) -> bool:
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO radar_chats (chat_ref, title, chat_id) VALUES (?, ?, ?)",
                (chat_ref, title, chat_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_radar_chat(chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM radar_chats WHERE id = ?", (chat_id,))
        await db.commit()
        return cur.rowcount > 0


async def update_radar_chat_status(entry_id: int, status: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_chats SET status = ?, last_verified_at = datetime('now') WHERE id = ?",
            (status, entry_id),
        )
        await db.commit()


async def update_radar_chat_resolved(entry_id: int, chat_id: int, chat_ref: str, title: str | None) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_chats SET chat_id = ?, chat_ref = ?, title = COALESCE(?, title), "
            "status = 'active', last_verified_at = datetime('now') WHERE id = ?",
            (chat_id, chat_ref, title, entry_id),
        )
        await db.commit()


async def log_radar_alert(
    keyword: str,
    chat_ref: str,
    author_id: int | None,
    message_text: str,
    message_url: str,
    author_name: str | None = None,
    status: str = "sent",
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO radar_alert_log "
            "(keyword, chat_ref, author_id, message_text, message_url, author_name, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (keyword, chat_ref, author_id, message_text, message_url, author_name, status),
        )
        await db.commit()


async def get_recent_radar_alerts(limit: int = 3) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_alert_log ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return await cur.fetchall()


async def link_keyword_chat(keyword_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO radar_keyword_chats (keyword_id, chat_id) VALUES (?, ?)",
                (keyword_id, chat_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def unlink_keyword_chat(keyword_id: int, chat_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM radar_keyword_chats WHERE keyword_id = ? AND chat_id = ?",
            (keyword_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_keyword_chat_links() -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id, chat_id FROM radar_keyword_chats"
        ) as cur:
            return await cur.fetchall()


async def get_chats_for_keyword(keyword_id: int) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT c.* FROM radar_chats c "
            "JOIN radar_keyword_chats l ON l.chat_id = c.id "
            "WHERE l.keyword_id = ? ORDER BY c.id",
            (keyword_id,),
        ) as cur:
            return await cur.fetchall()


async def get_keyword_ids_for_chat(chat_id: int) -> set[int]:
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id FROM radar_keyword_chats WHERE chat_id = ?",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
            return {r["keyword_id"] for r in rows}


async def get_silent_radar_chats(threshold_hours: int = 120) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            """SELECT id, chat_ref, title, last_message_at,
                      CAST((julianday('now') - julianday(last_message_at)) * 24 AS INTEGER) AS hours_silent
               FROM radar_chats
               WHERE status = 'active'
                 AND last_message_at IS NOT NULL
                 AND last_message_at < datetime('now', ?)
               ORDER BY last_message_at ASC""",
            (f"-{threshold_hours} hours",),
        ) as cur:
            return await cur.fetchall()


async def update_radar_last_message_at(entry_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_chats SET last_message_at = datetime('now') WHERE id = ?",
            (entry_id,),
        )
        await db.commit()


# --- sender filtering (per keyword×chat) ---

async def get_keyword_chat_modes() -> dict[tuple[int, int], str]:
    """{(keyword_id, chat_id): sender_mode} for every link; default 'all'."""
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id, chat_id, sender_mode FROM radar_keyword_chats"
        ) as cur:
            rows = await cur.fetchall()
            return {(r["keyword_id"], r["chat_id"]): r["sender_mode"] for r in rows}


async def get_all_sender_rules() -> dict[tuple[int, int, int], str]:
    """{(keyword_id, chat_id, sender_id): action} where action is 'allow' or 'mute'."""
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id, chat_id, sender_id, action FROM radar_sender_rules"
        ) as cur:
            rows = await cur.fetchall()
            return {(r["keyword_id"], r["chat_id"], r["sender_id"]): r["action"] for r in rows}


async def set_keyword_chat_mode(keyword_id: int, chat_id: int, mode: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_keyword_chats SET sender_mode = ? WHERE keyword_id = ? AND chat_id = ?",
            (mode, keyword_id, chat_id),
        )
        await db.commit()


async def add_sender_rule(
    keyword_id: int, chat_id: int, sender_id: int, action: str, label: str | None
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO radar_sender_rules (keyword_id, chat_id, sender_id, action, label) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(keyword_id, chat_id, sender_id) DO UPDATE SET "
            "action = excluded.action, label = excluded.label",
            (keyword_id, chat_id, sender_id, action, label),
        )
        await db.commit()


async def remove_sender_rule(rule_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM radar_sender_rules WHERE id = ?", (rule_id,))
        await db.commit()
        return cur.rowcount > 0


async def get_sender_rules_for(keyword_id: int, chat_id: int) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_sender_rules WHERE keyword_id = ? AND chat_id = ? "
            "ORDER BY action, id",
            (keyword_id, chat_id),
        ) as cur:
            return await cur.fetchall()


async def mute_sender_in_chat(chat_id: int, sender_id: int, label: str | None) -> int:
    """Mute a sender for every keyword linked to the chat (the alert '🔇' button)."""
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO radar_sender_rules (keyword_id, chat_id, sender_id, action, label) "
            "SELECT keyword_id, chat_id, ?, 'mute', ? FROM radar_keyword_chats WHERE chat_id = ? "
            "ON CONFLICT(keyword_id, chat_id, sender_id) DO UPDATE SET "
            "action = 'mute', label = excluded.label",
            (sender_id, label, chat_id),
        )
        await db.commit()
        return cur.rowcount


async def allow_only_sender_in_chat(chat_id: int, sender_id: int, label: str | None) -> int:
    """Switch every keyword link of the chat to allowlist and allow this sender (the '✅' button)."""
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_keyword_chats SET sender_mode = 'allowlist' WHERE chat_id = ?",
            (chat_id,),
        )
        cur = await db.execute(
            "INSERT INTO radar_sender_rules (keyword_id, chat_id, sender_id, action, label) "
            "SELECT keyword_id, chat_id, ?, 'allow', ? FROM radar_keyword_chats WHERE chat_id = ? "
            "ON CONFLICT(keyword_id, chat_id, sender_id) DO UPDATE SET "
            "action = 'allow', label = excluded.label",
            (sender_id, label, chat_id),
        )
        await db.commit()
        return cur.rowcount


async def get_author_label(author_id: int) -> str | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT author_name FROM radar_alert_log "
            "WHERE author_id = ? AND author_name IS NOT NULL ORDER BY id DESC LIMIT 1",
            (author_id,),
        ) as cur:
            row = await cur.fetchone()
            return row["author_name"] if row else None


async def get_recent_trigger_senders(
    keyword: str, chat_ref: str, limit: int = 10
) -> list[aiosqlite.Row]:
    """Distinct senders who recently triggered this keyword in this chat (for the picker)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT author_id, MAX(author_name) AS author_name, "
            "MAX(id) AS last_id, COUNT(*) AS cnt "
            "FROM radar_alert_log "
            "WHERE keyword = ? AND chat_ref = ? AND author_id IS NOT NULL "
            "GROUP BY author_id ORDER BY last_id DESC LIMIT ?",
            (keyword, chat_ref, limit),
        ) as cur:
            return await cur.fetchall()


# --- quiet log (suppressed matches) ---

async def get_muted_alerts(limit: int = 20) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_alert_log WHERE status = 'muted' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def get_muted_summary_since(days: int = 7) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword, chat_ref, COUNT(*) AS cnt, MAX(author_name) AS sample_author "
            "FROM radar_alert_log "
            "WHERE status = 'muted' AND alerted_at >= datetime('now', ?) "
            "GROUP BY keyword, chat_ref ORDER BY cnt DESC",
            (f"-{days} days",),
        ) as cur:
            return await cur.fetchall()
