-- Alerts whose delivery to the admin was not confirmed are parked here and
-- resent with backoff instead of being dropped. message_url is UNIQUE so a
-- source message can never be queued twice, no matter how often it is retried.
CREATE TABLE IF NOT EXISTS radar_pending_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_url TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    reply_markup TEXT,
    log_payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_radar_pending_due ON radar_pending_alerts(next_attempt_at);
