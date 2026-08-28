-- Code-pattern keywords. A keyword row with kind='code' matches any fixed-length
-- uppercase alphanumeric token instead of a literal word, and stores its length
-- spec in `keyword` (e.g. "code:5" or "code:10,17"). Existing rows are 'text'.
ALTER TABLE radar_keywords ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';

-- A drop code is single-use, so the second sighting of one is noise. Every code
-- the radar sees is remembered globally; a repeat inside the dedup window is
-- counted here and never alerted again.
CREATE TABLE IF NOT EXISTS radar_seen_codes (
    code TEXT PRIMARY KEY,
    chat_ref TEXT,
    message_url TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    hits INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_radar_seen_codes_last ON radar_seen_codes(last_seen_at);

-- History was keyed by chat_ref, which changes when a chat is renamed: every
-- rename silently split the per-chat views (recent senders, quiet log, digest)
-- into a before and an after. Rows now carry the stable radar_chats.id instead.
ALTER TABLE radar_alert_log ADD COLUMN chat_db_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_radar_alert_log_chat ON radar_alert_log(chat_db_id, keyword);

UPDATE radar_alert_log SET chat_db_id = (
    SELECT c.id FROM radar_chats c WHERE c.chat_ref = radar_alert_log.chat_ref
) WHERE chat_db_id IS NULL;
