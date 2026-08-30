"""Radar storage: keywords, monitored chats, keyword↔chat links, per-source
sender filtering rules, the alert/quiet log, the undelivered-alert queue and
chat-silence tracking."""
import aiosqlite

from src.db.base import get_db


async def get_radar_keywords() -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute("SELECT * FROM radar_keywords ORDER BY keyword") as cur:
            return await cur.fetchall()


async def add_radar_keyword(keyword: str, kind: str = "text") -> bool:
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO radar_keywords (keyword, kind) VALUES (?, ?)", (keyword, kind)
            )
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


async def adopt_alert_log_history(entry_id: int, chat_ref: str) -> int:
    """Claim log rows still keyed by an old @username for this chat.

    The log used to be keyed by chat_ref alone, so renaming a chat orphaned its
    whole history from the per-chat views. Called on every rename."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE radar_alert_log SET chat_db_id = ? WHERE chat_ref = ? AND chat_db_id IS NULL",
            (entry_id, chat_ref),
        )
        await db.commit()
        return cur.rowcount


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
    chat_db_id: int | None = None,
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO radar_alert_log "
            "(keyword, chat_ref, author_id, message_text, message_url, author_name, status, chat_db_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (keyword, chat_ref, author_id, message_text, message_url, author_name, status, chat_db_id),
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


async def remove_sender_rule(rule_id: int) -> tuple[int, int] | None:
    """Deletes one rule, returning the (keyword_id, chat_id) it governed if it
    was an allow rule — the only case that can empty an allowlist."""
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id, chat_id, action FROM radar_sender_rules WHERE id = ?",
            (rule_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await db.execute("DELETE FROM radar_sender_rules WHERE id = ?", (rule_id,))
        await db.commit()
        if row["action"] != "allow":
            return None
        return row["keyword_id"], row["chat_id"]


async def clear_sender_rules(keyword_id: int, chat_id: int) -> int:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM radar_sender_rules WHERE keyword_id = ? AND chat_id = ?",
            (keyword_id, chat_id),
        )
        await db.commit()
        return cur.rowcount


async def reset_empty_allowlists(pairs: list[tuple[int, int]]) -> int:
    """Put back to 'all' those keyword×chat pairs left in allowlist mode with
    nobody on the list — an empty allowlist alerts on nothing, silently.

    Only the pairs whose last allow rule was just removed are considered. A sweep
    over the whole table would reach keywords the admin never touched, and would
    undo an allowlist they had only just switched on but not yet populated."""
    if not pairs:
        return 0
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE radar_keyword_chats SET sender_mode = 'all' "
            "WHERE sender_mode = 'allowlist' "
            f"  AND (keyword_id, chat_id) IN (VALUES {','.join(['(?,?)'] * len(pairs))}) "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM radar_sender_rules r "
            "    WHERE r.keyword_id = radar_keyword_chats.keyword_id "
            "      AND r.chat_id = radar_keyword_chats.chat_id AND r.action = 'allow')",
            [v for pair in pairs for v in pair],
        )
        await db.commit()
        return cur.rowcount


async def get_sender_rules_for(keyword_id: int, chat_id: int) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_sender_rules WHERE keyword_id = ? AND chat_id = ? "
            "ORDER BY action, id",
            (keyword_id, chat_id),
        ) as cur:
            return await cur.fetchall()


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
    keyword: str, chat_db_id: int, limit: int = 10
) -> list[aiosqlite.Row]:
    """Distinct senders who recently triggered this keyword in this chat (for the picker)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT l.author_id, MAX(l.author_name) AS author_name, "
            "MAX(l.id) AS last_id, COUNT(*) AS cnt, "
            # MAX over the URL is a lexicographic max: message 9 beats 100.
            "(SELECT l2.message_url FROM radar_alert_log l2 "
            " WHERE l2.keyword = l.keyword AND l2.chat_db_id = l.chat_db_id "
            "   AND l2.author_id = l.author_id ORDER BY l2.id DESC LIMIT 1) AS last_url "
            "FROM radar_alert_log l "
            "WHERE l.keyword = ? AND l.chat_db_id = ? AND l.author_id IS NOT NULL "
            "GROUP BY l.author_id ORDER BY last_id DESC LIMIT ?",
            (keyword, chat_db_id, limit),
        ) as cur:
            return await cur.fetchall()


async def get_recent_senders_for_keyword(keyword_id: int, limit: int = 15) -> list[aiosqlite.Row]:
    """Who last tripped this keyword, across every chat it watches.

    Answers "who am I going to hear from about this word" at the keyword itself,
    rather than only inside one chat's filter editor."""
    async with get_db() as db:
        async with db.execute(
            "SELECT l.author_id, MAX(l.author_name) AS author_name, l.chat_db_id, "
            "       MAX(c.title) AS chat_title, "
            "       COALESCE(MAX(c.chat_ref), MAX(l.chat_ref)) AS chat_ref, "
            "       COUNT(*) AS cnt, MAX(l.id) AS last_id, "
            "       (SELECT l2.message_url FROM radar_alert_log l2 "
            "         WHERE l2.keyword = l.keyword AND l2.chat_db_id = l.chat_db_id "
            "           AND l2.author_id = l.author_id ORDER BY l2.id DESC LIMIT 1) AS last_url, "
            "       MAX(r.action) AS action "
            "FROM radar_alert_log l "
            "JOIN radar_keywords k ON k.keyword = l.keyword "
            "JOIN radar_chats c ON c.id = l.chat_db_id "
            "LEFT JOIN radar_sender_rules r "
            "       ON r.keyword_id = k.id AND r.chat_id = l.chat_db_id AND r.sender_id = l.author_id "
            "WHERE k.id = ? AND l.author_id IS NOT NULL "
            "GROUP BY l.author_id, l.chat_db_id ORDER BY last_id DESC LIMIT ?",
            (keyword_id, limit),
        ) as cur:
            return await cur.fetchall()


# --- muted senders across every keyword and chat ---

async def get_sender_rule_summary(action: str) -> list[aiosqlite.Row]:
    """One row per sender holding rules of this action, most rules first."""
    async with get_db() as db:
        async with db.execute(
            "SELECT sender_id, MAX(label) AS label, COUNT(*) AS cnt, MAX(created_at) AS last_at "
            "FROM radar_sender_rules WHERE action = ? "
            "GROUP BY sender_id ORDER BY cnt DESC, last_at DESC",
            (action,),
        ) as cur:
            return await cur.fetchall()


async def get_rules_for_sender(sender_id: int) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT r.*, k.keyword, c.title AS chat_title, c.chat_ref "
            "FROM radar_sender_rules r "
            "JOIN radar_keywords k ON k.id = r.keyword_id "
            "JOIN radar_chats c ON c.id = r.chat_id "
            "WHERE r.sender_id = ? ORDER BY r.action, k.keyword",
            (sender_id,),
        ) as cur:
            return await cur.fetchall()


async def remove_sender_rules_for(sender_id: int, action: str) -> tuple[int, list[tuple[int, int]]]:
    """Deletes every rule of one action for a sender. Returns how many went, and
    the keyword×chat pairs affected when they were allow rules."""
    async with get_db() as db:
        async with db.execute(
            "SELECT keyword_id, chat_id FROM radar_sender_rules "
            "WHERE sender_id = ? AND action = ?",
            (sender_id, action),
        ) as cur:
            pairs = [(r["keyword_id"], r["chat_id"]) for r in await cur.fetchall()]
        cur = await db.execute(
            "DELETE FROM radar_sender_rules WHERE sender_id = ? AND action = ?",
            (sender_id, action),
        )
        await db.commit()
        return cur.rowcount, (pairs if action == "allow" else [])


# --- quiet log (suppressed matches) ---

async def get_muted_alerts(limit: int = 20) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_alert_log WHERE status = 'muted' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def get_muted_alerts_count() -> int:
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM radar_alert_log WHERE status = 'muted'"
        ) as cur:
            return (await cur.fetchone())[0]


async def get_muted_summary_since(days: int = 7) -> list[aiosqlite.Row]:
    """Muted matches of the last N days, grouped by keyword and chat.

    Grouping is on the stable chat id, not chat_ref: keyed by the @username a
    rename split one group into two. keyword_id/chat_db_id come along so the
    digest can offer a button straight into that group's filter editor."""
    window = f"-{days} days"
    async with get_db() as db:
        async with db.execute(
            "SELECT l1.keyword, k.id AS keyword_id, l1.chat_db_id, "
            "       MAX(c.title) AS chat_title, "
"       COALESCE(MAX(c.chat_ref), MAX(l1.chat_ref)) AS chat_ref, COUNT(*) AS cnt, "
            "(SELECT l2.author_name FROM radar_alert_log l2 "
            " WHERE l2.keyword = l1.keyword AND l2.chat_db_id IS l1.chat_db_id "
            "   AND (l2.chat_db_id IS NOT NULL OR l2.chat_ref = l1.chat_ref) "
            "   AND l2.status = 'muted' AND l2.alerted_at >= datetime('now', ?) "
            " ORDER BY l2.id DESC LIMIT 1) AS sample_author, "
            "(SELECT l2.message_url FROM radar_alert_log l2 "
            " WHERE l2.keyword = l1.keyword AND l2.chat_db_id IS l1.chat_db_id "
            "   AND (l2.chat_db_id IS NOT NULL OR l2.chat_ref = l1.chat_ref) "
            "   AND l2.status = 'muted' AND l2.alerted_at >= datetime('now', ?) "
            " ORDER BY l2.id DESC LIMIT 1) AS sample_url "
            "FROM radar_alert_log l1 "
            "LEFT JOIN radar_keywords k ON k.keyword = l1.keyword "
            "LEFT JOIN radar_chats c ON c.id = l1.chat_db_id "
            "WHERE l1.status = 'muted' AND l1.alerted_at >= datetime('now', ?) "
            # Rows no rename ever re-keyed have a NULL chat id, which groups them
            # all into one nameless pile; fall back to the ref they were logged under.
            "GROUP BY l1.keyword, l1.chat_db_id, "
            "         CASE WHEN l1.chat_db_id IS NULL THEN l1.chat_ref END "
            "ORDER BY cnt DESC",
            (window, window, window),
        ) as cur:
            return await cur.fetchall()


# --- undelivered alert queue ---

async def enqueue_pending_alert(
    message_url: str, body: str, reply_markup: str | None, log_payload: str
) -> bool:
    """Park an unconfirmed alert for a later resend. Returns False if already queued."""
    async with get_db() as db:
        async with db.execute(
            "INSERT OR IGNORE INTO radar_pending_alerts "
            "(message_url, body, reply_markup, log_payload) VALUES (?, ?, ?, ?)",
            (message_url, body, reply_markup, log_payload),
        ) as cur:
            queued = cur.rowcount > 0
        await db.commit()
        return queued


async def get_due_pending_alerts(limit: int = 10) -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM radar_pending_alerts WHERE next_attempt_at <= datetime('now') "
            "ORDER BY id LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def count_pending_alerts() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM radar_pending_alerts") as cur:
            return (await cur.fetchone())[0]


async def drop_pending_alert(entry_id: int) -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM radar_pending_alerts WHERE id = ?", (entry_id,))
        await db.commit()


async def defer_pending_alert(entry_id: int, delay_minutes: int, error: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE radar_pending_alerts SET attempts = attempts + 1, last_error = ?, "
            "next_attempt_at = datetime('now', ?) WHERE id = ?",
            (error[:200], f"+{delay_minutes} minutes", entry_id),
        )
        await db.commit()


# --- seen drop codes (global dedup) ---

async def filter_unseen_codes(codes: list[str], days: int) -> set[str]:
    """Of these codes, the ones worth alerting on: not seen inside the dedup
    window, and not blocked outright as never having been a code."""
    if not codes:
        return set()
    placeholders = ",".join("?" * len(codes))
    async with get_db() as db:
        async with db.execute(
            f"SELECT code FROM radar_seen_codes WHERE code IN ({placeholders}) "
            "AND (blocked = 1 OR last_seen_at >= datetime('now', ?))",
            (*codes, f"-{days} days"),
        ) as cur:
            skip = {r["code"] for r in await cur.fetchall()}
    return {c for c in codes if c not in skip}


async def record_seen_codes(codes: list[str], chat_ref: str, message_url: str) -> None:
    """Remember every code sighting, alerted or not, so a repost stays quiet."""
    if not codes:
        return
    async with get_db() as db:
        await db.executemany(
            "INSERT INTO radar_seen_codes (code, chat_ref, message_url) VALUES (?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET hits = hits + 1, last_seen_at = datetime('now')",
            [(c, chat_ref, message_url) for c in codes],
        )
        await db.commit()


async def count_repeat_codes() -> int:
    """How many distinct codes the dedup has silenced at least one repeat of.

    Counting sightings instead would never stop growing: a code that keeps being
    reposted keeps refreshing its own `last_seen_at`, so retention never retires
    it and its `hits` climbs forever. Counting codes is bounded by the table."""
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM radar_seen_codes WHERE hits > 1"
        ) as cur:
            return (await cur.fetchone())[0]


async def purge_seen_codes(days: int) -> int:
    """Retire stale sightings. A blocked code is kept: dropping it would let the
    thing the admin said is not a code start alerting again."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM radar_seen_codes "
            "WHERE blocked = 0 AND last_seen_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return cur.rowcount


async def block_code(code: str) -> None:
    """Mark a token as never having been a code. Blocking one the radar has not
    recorded yet still works, so the block can precede the sighting."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO radar_seen_codes (code, blocked, hits) VALUES (?, 1, 0) "
            "ON CONFLICT(code) DO UPDATE SET blocked = 1",
            (code,),
        )
        await db.commit()


async def unblock_code(code: str) -> bool:
    """Forget the token entirely rather than just clearing the flag.

    Blocking writes a row whose `last_seen_at` is the moment of the block, so
    merely un-flagging it would leave the dedup window suppressing a code the
    admin has just asked to hear about again — and may never have been shown."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM radar_seen_codes WHERE code = ? AND blocked = 1", (code,)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_blocked_codes() -> list[aiosqlite.Row]:
    async with get_db() as db:
        async with db.execute(
            "SELECT code, chat_ref, hits, last_seen_at FROM radar_seen_codes "
            "WHERE blocked = 1 ORDER BY last_seen_at DESC"
        ) as cur:
            return await cur.fetchall()
