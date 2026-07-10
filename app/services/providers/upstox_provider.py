"""Upstox MarketDataProvider implementation for MCX instruments.

Upstox uses ISIN-based instrument keys for futures. MCX Gold and Silver
are rolled monthly; we resolve the near-month contract automatically
using the Upstox instruments master CSV.

Interval mapping (our canonical → Upstox):
  1m  → 1minute
  5m  → 5minute
  1h  → 60minute
  1d  → day

History limits (Upstox documented):
  Intraday (1-300 min): up to 6 months
  EOD (day/week/month): up to 10 years
"""

from datetime import date, datetime, timedelta, timezone

from collections.abc import Callable
from urllib.parse import quote

import httpx

from app.services.providers.base import MarketDataProvider, NormalizedBar

# V3 API: unit and interval are separate. Unit = minutes/hours/days/weeks/months.
# Our canonical interval → (unit, interval_value)
# Retrieval limits per request: minutes ≤15 → 1 month, minutes >15 → 1 quarter,
# hours → 1 quarter, days → 1 decade.
_INTERVAL_MAP: dict[str, tuple[str, int]] = {
    "1m":  ("minutes", 1),
    "5m":  ("minutes", 5),
    "15m": ("minutes", 15),
    "30m": ("minutes", 30),
    "1h":  ("hours",   1),
    "1d":  ("days",    1),
    "1wk": ("weeks",   1),
    "1mo": ("months",  1),
}

# Max days we request per backfill call (stays within per-request retrieval limits)
_INTERVAL_MAX_DAYS: dict[str, int] = {
    "1m":  30,    # 1-month limit for ≤15min intervals
    "5m":  30,
    "15m": 30,
    "30m": 90,    # 1-quarter limit for >15min intervals
    "1h":  90,
    "1d":  3650,  # 1 decade
    "1wk": 3650,
    "1mo": 3650,
}

# Upstox instrument keys for MCX near-month futures.
# These are the continuous / near-month front keys used in the Upstox v2 API.
# The symbol here is the "instrument_key" from the Upstox instruments master.
# Format: MCX_FO|<exchange_token>
# We hardcode the currently active near-month contract keys here.
# When rollover happens, the InstrumentSourceMapping source_ticker should be
# updated via the API (POST /instruments or a migration).
_BASE_URL = "https://api.upstox.com/v3"


class UpstoxProvider(MarketDataProvider):
    name = "upstox"

    def __init__(self, token_getter: Callable[[], str | None]) -> None:
        """
        token_getter: a zero-arg callable that returns the current access token
        (or None if expired/missing). Called fresh on every fetch so the provider
        always uses the latest token without needing a restart.
        """
        self._token_getter = token_getter

    def _get_token(self) -> str:
        token = self._token_getter()
        if token is None:
            raise RuntimeError(
                "Upstox access token is missing or expired. "
                "Visit /auth/upstox/login to re-authorize."
            )
        return token

    def max_days_for_interval(self, interval: str) -> int:
        return _INTERVAL_MAX_DAYS.get(interval, 180)

    def fetch_ohlcv(
        self, source_ticker: str, start: date, end: date, interval: str = "1d"
    ) -> list[NormalizedBar]:
        """
        source_ticker is the Upstox instrument_key, e.g. "MCX_FO|441141"
        """
        interval_params = _INTERVAL_MAP.get(interval)
        if interval_params is None:
            raise ValueError(f"Unsupported interval '{interval}' for Upstox provider.")
        unit, interval_value = interval_params

        # Enforce history limit
        max_days = self.max_days_for_interval(interval)
        earliest = date.today() - timedelta(days=max_days)
        if start < earliest:
            start = earliest
        if start >= end:
            return []

        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

        # V3 URL: /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}
        # Instrument keys contain '|' which must be percent-encoded in URL paths.
        encoded_key = quote(source_ticker, safe="")
        url = (
            f"{_BASE_URL}/historical-candle/{encoded_key}"
            f"/{unit}/{interval_value}/{end.isoformat()}/{start.isoformat()}"
        )

        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        candles = data.get("data", {}).get("candles", [])
        bars: list[NormalizedBar] = []
        for c in candles:
            # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
            ts = datetime.fromisoformat(c[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(NormalizedBar(
                ts=ts,
                open=float(c[1]),
                high=float(c[2]),
                low=float(c[3]),
                close=float(c[4]),
                volume=float(c[5]) if c[5] is not None else None,
                adjusted_close=None,
            ))
        # Upstox returns newest first — reverse to chronological order
        bars.reverse()
        return bars
