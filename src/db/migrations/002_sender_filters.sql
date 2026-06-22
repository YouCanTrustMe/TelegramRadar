-- Per keyword×chat sender filtering: each link gets a mode ('all' alerts from
-- everyone except muted senders; 'allowlist' alerts only from allowed senders),
-- backed by a rules table of allow/mute entries. Suppressed matches are kept in
-- radar_alert_log with status='muted' (the quiet log) instead of being sent.
ALTER TABLE radar_keyword_chats ADD COLUMN sender_mode TEXT NOT NULL DEFAULT 'all';

CREATE TABLE IF NOT EXISTS radar_sender_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (keyword_id, chat_id, sender_id),
    FOREIGN KEY (keyword_id) REFERENCES radar_keywords(id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES radar_chats(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_radar_sender_rules_kc ON radar_sender_rules(keyword_id, chat_id);

ALTER TABLE radar_alert_log ADD COLUMN status TEXT NOT NULL DEFAULT 'sent';
ALTER TABLE radar_alert_log ADD COLUMN author_name TEXT;
