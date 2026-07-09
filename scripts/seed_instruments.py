"""One-time/editable seed of exchanges, the yfinance data source, and a starter watchlist.
Also runs a full historical backfill after seeding.

Run with: python -m scripts.seed_instruments
"""

from datetime import date, timedelta

from app.db import SessionLocal
from app.models.ingestion import DataSource, SourceKind
from app.models.instrument import AssetClass, Instrument, InstrumentSourceMapping
from app.models.market import Exchange
from app.services.ingestion_service import ingest_instrument, register_provider
from app.services.providers.yfinance_provider import YFinanceProvider

EXCHANGES = [
    {"code": "COMEX", "name": "Commodity Exchange", "country": "US", "timezone": "America/New_York"},
    {"code": "FOREX", "name": "Foreign Exchange Market", "country": "US", "timezone": "UTC"},
]

# (internal symbol, name, asset_class, exchange_code, sector, currency, yfinance ticker)
WATCHLIST = [
    ("GOLD",   "Gold Futures (Continuous)",    AssetClass.commodity, "COMEX", "Metals", "USD", "GC=F"),
    ("SILVER", "Silver Futures (Continuous)",  AssetClass.commodity, "COMEX", "Metals", "USD", "SI=F"),
    ("USDINR", "US Dollar / Indian Rupee",     AssetClass.currency,  "FOREX", "Forex",  "INR", "INR=X"),
]

# Backfill windows — capped automatically by yfinance per-interval limits
BACKFILL_INTERVALS = [
    ("1d", 1825),   # 5Y daily (yfinance: unlimited)
    ("1h",  700),   # ~2Y hourly (yfinance cap: 730d)
    ("5m",   55),   # ~60d 5-min (yfinance cap: 60d)
    ("1m",    6),   # 6d 1-min  (yfinance cap: 7d)
]


def main() -> None:
    db = SessionLocal()
    provider = YFinanceProvider()
    register_provider(provider)

    try:
        # ── Exchanges ────────────────────────────────────────────────────────
        exchanges_by_code = {}
        for data in EXCHANGES:
            exchange = db.query(Exchange).filter_by(code=data["code"]).one_or_none()
            if exchange is None:
                exchange = Exchange(**data)
                db.add(exchange)
                db.flush()
            exchanges_by_code[data["code"]] = exchange

        # ── Data source ──────────────────────────────────────────────────────
        source = db.query(DataSource).filter_by(name="yfinance").one_or_none()
        if source is None:
            source = DataSource(name="yfinance", kind=SourceKind.free, is_active=True)
            db.add(source)
            db.flush()

        # ── Instruments + source mappings ────────────────────────────────────
        instruments = []
        for symbol, name, asset_class, exchange_code, sector, currency, source_ticker in WATCHLIST:
            instrument = db.query(Instrument).filter_by(symbol=symbol).one_or_none()
            if instrument is None:
                instrument = Instrument(
                    symbol=symbol,
                    name=name,
                    asset_class=asset_class,
                    exchange_id=exchanges_by_code[exchange_code].id,
                    sector=sector,
                    currency=currency,
                    is_active=True,
                )
                db.add(instrument)
                db.flush()

            mapping = (
                db.query(InstrumentSourceMapping)
                .filter_by(instrument_id=instrument.id, source_id=source.id)
                .one_or_none()
            )
            if mapping is None:
                db.add(InstrumentSourceMapping(
                    instrument_id=instrument.id,
                    source_id=source.id,
                    source_ticker=source_ticker,
                ))

            instruments.append(instrument)

        db.commit()
        print(f"Seeded {len(EXCHANGES)} exchanges and {len(instruments)} instruments.")

        # ── Historical backfill ──────────────────────────────────────────────
        print("\nStarting historical backfill...")
        end = date.today()

        for interval, days in BACKFILL_INTERVALS:
            max_days = provider.max_days_for_interval(interval)
            actual_days = min(days, max_days)
            start = end - timedelta(days=actual_days)
            print(f"\n[{interval}] {actual_days} days ({start} → {end})")

            for instrument in instruments:
                run = ingest_instrument(db, instrument, source, start=start, end=end, interval=interval)
                status = run.status.value
                msg = f"  {instrument.symbol}: {status}, rows={run.rows_ingested}"
                if run.error_message:
                    msg += f", error={run.error_message}"
                print(msg)

        print("\nSeed + backfill complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
