"""Backfill historical OHLCV for all (or selected) active instruments, across one or more intervals.

yfinance history caps are enforced automatically per interval:
  1m → 7 days max, 5m/15m/30m/60m → 60 days, 1h → 730 days, 1d/1wk/1mo → no practical limit.

Run with:
    python -m scripts.backfill_prices
    python -m scripts.backfill_prices --intervals 1d,1h,5m --symbols AAPL,GOLD
    python -m scripts.backfill_prices --intervals 1d --days 365
"""

import argparse
from datetime import date, timedelta

from app.db import SessionLocal
from app.models.ingestion import DataSource
from app.models.instrument import Instrument
from app.services.ingestion_service import ingest_instrument, register_provider
from app.services.providers.yfinance_provider import INTERVAL_MAX_DAYS, YFinanceProvider

DEFAULT_INTERVALS = ["1d", "1h", "5m"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill OHLCV price history.")
    parser.add_argument(
        "--intervals",
        type=str,
        default=",".join(DEFAULT_INTERVALS),
        help=f"Comma-separated intervals to fetch. Default: {','.join(DEFAULT_INTERVALS)}. "
             f"Supported: {', '.join(INTERVAL_MAX_DAYS)}",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="How many days of history to fetch. Defaults to the maximum allowed by yfinance for each interval.",
    )
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated internal symbols; default = all active.")
    parser.add_argument("--source", type=str, default="yfinance")
    args = parser.parse_args()

    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    provider = YFinanceProvider()
    register_provider(provider)

    db = SessionLocal()
    try:
        source = db.query(DataSource).filter_by(name=args.source).one()

        query = db.query(Instrument).filter(Instrument.is_active.is_(True))
        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",")]
            query = query.filter(Instrument.symbol.in_(symbols))
        instruments = query.all()

        end = date.today()

        for interval in intervals:
            max_days = provider.max_days_for_interval(interval)
            days = min(args.days, max_days) if args.days else max_days
            start = end - timedelta(days=days)

            print(f"\n[{interval}] fetching {days} days ({start} to {end}) for {len(instruments)} instruments...")
            for instrument in instruments:
                run = ingest_instrument(db, instrument, source, start=start, end=end, interval=interval)
                status_label = run.status.value
                msg = f"  {instrument.symbol}: {status_label}, rows={run.rows_ingested}"
                if run.error_message:
                    msg += f", error={run.error_message}"
                print(msg)

        print("\nBackfill complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
