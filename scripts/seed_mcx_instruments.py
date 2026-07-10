"""Seed MCX Gold and Silver futures instruments (Upstox provider) and backfill history.

Run with: python -m scripts.seed_mcx_instruments

IMPORTANT — contract rollover:
  MCX futures expire every 2 months. When the near-month contract expires, update
  source_ticker in instrument_source_mappings to the next contract key:
    UPDATE instrument_source_mappings
       SET source_ticker = 'MCX_FO|<new_key>'
     WHERE instrument_id = (SELECT id FROM instruments WHERE symbol = 'MCX_GOLD')
       AND source_id     = (SELECT id FROM data_sources WHERE name = 'upstox');
"""

from datetime import date, timedelta

from app.config import get_settings
from app.db import SessionLocal
from app.models.ingestion import DataSource, SourceKind
from app.models.instrument import AssetClass, Instrument, InstrumentSourceMapping
from app.models.market import Exchange
from app.services.ingestion_service import ingest_instrument, register_provider
from app.services.providers.upstox_provider import UpstoxProvider
from app.services.upstox_auth import get_valid_token

# ── Near-month contract keys (update on rollover) ─────────────────────────────
# Current expiry: 2026-08-05
# Next:  MCX_FO|483079 (GOLD26OCTFUT, expiry 2026-10-05)
#        MCX_FO|569003 (GOLDM26OCTFUT, expiry 2026-10-05)
MCX_INSTRUMENTS = [
    # (internal symbol, name, asset_class, sector, currency, upstox instrument_key)
    # Gold: GOLD26AUGFUT (1 kg std), expiry 2026-08-05 → next: MCX_FO|483079 (GOLD26OCTFUT)
    ("MCX_GOLD",   "MCX Gold Futures (Near Month)",         AssetClass.commodity, "Metals", "INR", "MCX_FO|466583"),
    # Silver: SILVERM26AUGFUT (5 kg mini), expiry 2026-08-31 → next: MCX_FO|483080 (SILVERM26NOVFUT)
    ("MCX_SILVER", "MCX Silver Mini Futures (Near Month)",  AssetClass.commodity, "Metals", "INR", "MCX_FO|471726"),
]

# Upstox v3 supported intervals: 1-300 minutes, 1-5 hours, days, weeks, months
# Per-request limits: ≤15min → 1 month, >15min → 1 quarter, hours → 1 quarter
BACKFILL_INTERVALS = [
    ("1d",  3650),  # 10Y daily
    ("1h",    90),  # 1 quarter hourly
    ("5m",    30),  # 1 month 5-min
    ("1m",    30),  # 1 month 1-min
]


def main() -> None:
    settings = get_settings()
    db = SessionLocal()

    try:
        # ── Validate Upstox token ─────────────────────────────────────────────
        token = get_valid_token(db)
        if token is None:
            print("ERROR: No valid Upstox token found.")
            print("  Visit https://marketlenss.duckdns.org/auth/upstox/login to authorize first.")
            return

        provider = UpstoxProvider(token_getter=lambda: get_valid_token(db))
        register_provider(provider)

        # ── MCX exchange ──────────────────────────────────────────────────────
        exchange = db.query(Exchange).filter_by(code="MCX").one_or_none()
        if exchange is None:
            exchange = Exchange(
                code="MCX",
                name="Multi Commodity Exchange of India",
                country="IN",
                timezone="Asia/Kolkata",
            )
            db.add(exchange)
            db.flush()
            print("Created exchange: MCX")

        # ── Upstox data source ────────────────────────────────────────────────
        source = db.query(DataSource).filter_by(name="upstox").one_or_none()
        if source is None:
            source = DataSource(name="upstox", kind=SourceKind.free, is_active=True)
            db.add(source)
            db.flush()
            print("Created data source: upstox")

        # ── Instruments + mappings ────────────────────────────────────────────
        instruments = []
        for symbol, name, asset_class, sector, currency, source_ticker in MCX_INSTRUMENTS:
            if "PLACEHOLDER" in source_ticker:
                print(f"SKIP {symbol}: update source_ticker in MCX_INSTRUMENTS first.")
                continue

            instrument = db.query(Instrument).filter_by(symbol=symbol).one_or_none()
            if instrument is None:
                instrument = Instrument(
                    symbol=symbol,
                    name=name,
                    asset_class=asset_class,
                    exchange_id=exchange.id,
                    sector=sector,
                    currency=currency,
                    is_active=True,
                )
                db.add(instrument)
                db.flush()
                print(f"Created instrument: {symbol}")
            else:
                print(f"Instrument already exists: {symbol}")

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
                print(f"  Mapped {symbol} → {source_ticker}")

            instruments.append(instrument)

        db.commit()

        if not instruments:
            print("\nNo instruments to backfill.")
            return

        # ── Historical backfill ───────────────────────────────────────────────
        print(f"\nBackfilling {len(instruments)} MCX instrument(s) via Upstox...")
        end = date.today()

        for interval, days in BACKFILL_INTERVALS:
            max_days = provider.max_days_for_interval(interval)
            actual_days = min(days, max_days)
            start = end - timedelta(days=actual_days)
            print(f"\n[{interval}] {actual_days} days ({start} → {end})")

            for instrument in instruments:
                run = ingest_instrument(db, instrument, source, start=start, end=end, interval=interval)
                msg = f"  {instrument.symbol}: {run.status.value}, rows={run.rows_ingested}"
                if run.error_message:
                    msg += f", error={run.error_message}"
                print(msg)

        print("\nMCX seed + backfill complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
