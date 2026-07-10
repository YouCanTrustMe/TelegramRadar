from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Radar userbot — its own Telegram account, independent from the main collector
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str

    # Radar bot — its own bot token (two processes can't share one token's getUpdates)
    telegram_bot_token: str
    telegram_admin_id: int

    database_path: str = "data/radar.db"
    radar_timezone: str = "Europe/Berlin"

    # Weekly quiet-log digest of muted matches (cron day-of-week + hour, local tz)
    radar_digest_day: str = "mon"
    radar_digest_hour: int = 9

    # Keyword obfuscation handling. Fuzzy trades precision for recall: it cannot
    # tell a typo from a real word one edit away, and fires on both.
    radar_match_leet: bool = True
    radar_match_fuzzy: bool = True
    radar_match_translit: bool = True
    radar_match_merge_min_len: int = 5


settings = Settings()
