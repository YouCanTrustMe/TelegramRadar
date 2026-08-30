-- Some tokens have the shape of a drop code without being one: "10LVL" out of
-- "6-10LVL FaceIT" is five uppercase characters with a digit, exactly like a real
-- code. Muting the sender is not the answer — the official channel that posts
-- these is also the one that posts genuine codes — so a single code value can be
-- blocked outright. A blocked row is kept forever and never alerts again.
ALTER TABLE radar_seen_codes ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_radar_seen_codes_blocked ON radar_seen_codes(blocked);
