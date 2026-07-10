from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    environment: str = "development"
    log_level: str = "INFO"

    # Database
    database_url: str

    # CORS — comma-separated exact origins; env vars are always strings, so avoid
    # pydantic-settings' list-typed env parsing and split this ourselves in main.py.
    cors_allowed_origins: str = ""
    cors_allowed_origin_regex: str | None = None

    # API key — if set, every request must include X-API-Key: <value>.
    # Leave empty in development (no enforcement). Set in production .env.app.
    api_key: str = ""

    # Scheduler — two modes:
    #   interval mode: set scheduler_interval_minutes > 0  (e.g. 10 = every 10 min)
    #   cron mode:     set scheduler_interval_minutes = 0, use cron fields below
    scheduler_enabled: bool = True
    scheduler_interval_minutes: int = 10  # minutes between runs; 0 = use cron mode instead
    scheduler_cron_hour: str = "22"
    scheduler_cron_minute: str = "0"
    scheduler_cron_day_of_week: str = "mon-fri"  # weekdays only; set "*" for all days
    # Comma-separated list of intervals to fetch on each scheduled run.
    # yfinance caps: 1m→7d, 2m/5m/15m/30m/60m/90m→60d, 1h→730d, 1d/1wk/1mo→unlimited.
    # The scheduler respects these caps automatically — no manual days config needed per interval.
    scheduler_ingestion_intervals: str = "1d,1h,5m,1m"
    scheduler_source_name: str = "yfinance"

    # Upstox OAuth2 credentials (set in production via Secret Manager / .env)
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    # Redirect URI registered in the Upstox developer portal
    upstox_redirect_uri: str = "https://marketlenss.duckdns.org/auth/upstox/callback"

    def parsed_intervals(self) -> list[str]:
        return [i.strip() for i in self.scheduler_ingestion_intervals.split(",") if i.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
