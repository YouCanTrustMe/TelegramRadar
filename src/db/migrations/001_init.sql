CREATE TABLE IF NOT EXISTS radar_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS radar_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_ref TEXT NOT NULL UNIQUE,
    title TEXT,
    chat_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    last_verified_at TEXT,
    last_seen_msg_id INTEGER,
    last_message_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS radar_blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS radar_alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    chat_ref TEXT NOT NULL,
    author_id INTEGER,
    message_text TEXT,
    message_url TEXT,
    alerted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS radar_keyword_chats (
    keyword_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (keyword_id, chat_id),
    FOREIGN KEY (keyword_id) REFERENCES radar_keywords(id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES radar_chats(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_radar_kw_chats_chat ON radar_keyword_chats(chat_id);
CREATE INDEX IF NOT EXISTS idx_radar_kw_chats_kw ON radar_keyword_chats(keyword_id);
