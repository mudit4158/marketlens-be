from datetime import date, timedelta

import yfinance as yf

from app.services.providers.base import MarketDataProvider, NormalizedBar

# yfinance hard limits on how far back intraday history is available.
# Requesting beyond these silently returns empty data, so we cap automatically.
INTERVAL_MAX_DAYS: dict[str, int] = {
    "1m":  6,     # yfinance hard cap: 7 days; use 6 to avoid boundary edge
    "2m":  55,
    "5m":  55,    # yfinance cap: 60 days; buffer avoids strict boundary rejections
    "15m": 55,
    "30m": 55,
    "60m": 55,
    "90m": 55,
    "1h":  700,   # yfinance cap: 730 days; buffer avoids off-by-one boundary errors
    "1d":  10 * 365,
    "1wk": 10 * 365,
    "1mo": 10 * 365,
}


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def max_days_for_interval(self, interval: str) -> int:
        """Return the maximum number of lookback days yfinance supports for this interval."""
        return INTERVAL_MAX_DAYS.get(interval, 60)

    def fetch_ohlcv(
        self, source_ticker: str, start: date, end: date, interval: str = "1d"
    ) -> list[NormalizedBar]:
        # Enforce yfinance's per-interval history cap.
        max_days = self.max_days_for_interval(interval)
        earliest_allowed = date.today() - timedelta(days=max_days)
        if start < earliest_allowed:
            start = earliest_allowed

        if start >= end:
            return []

        # yfinance's `end` is exclusive, so push one day forward to include the requested end date.
        df = yf.Ticker(source_ticker).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval=interval,
            auto_adjust=False,
        )

        if df.empty:
            return []

        bars: list[NormalizedBar] = []
        for ts, row in df.iterrows():
            volume = row.get("Volume")
            adj_close = row.get("Adj Close")
            bars.append(
                NormalizedBar(
                    ts=ts.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(volume) if volume is not None else None,
                    adjusted_close=float(adj_close) if adj_close is not None else None,
                )
            )
        return bars
