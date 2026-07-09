# MarketLens Backend

FastAPI service that ingests OHLCV price data for stocks and commodities into a generic, source-agnostic schema (Postgres + TimescaleDB), so a future dashboard and suggestion engine can query it consistently regardless of which data provider (free or paid) supplied a given bar.

## Stack
- FastAPI + SQLAlchemy 2.0 + Alembic
- PostgreSQL with the TimescaleDB extension (`price_bars` is a hypertable partitioned on `ts`)
- `yfinance` as the first (free) data provider — see `app/services/providers/` for the swappable provider interface

## Local setup

```bash
# 1. Start TimescaleDB
docker compose up -d

# 2. Create venv + install deps
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS/Linux

# 3. Copy env and adjust if needed (defaults match docker-compose.yml)
cp .env.example .env

# 4. Run migrations (creates tables + converts price_bars into a hypertable)
.venv/Scripts/python -m alembic upgrade head

# 5. Seed exchanges + a starter watchlist (stocks + commodities)
.venv/Scripts/python -m scripts.seed_instruments

# 6. Backfill price history
.venv/Scripts/python -m scripts.backfill_prices --days 365

# 7. Run the API
.venv/Scripts/python -m uvicorn app.main:app --reload
```

## API
- `GET /health`
- `GET /instruments?asset_class=&exchange_id=&sector=&is_active=`
- `GET /prices?symbol=&instrument_id=&asset_class=&exchange_id=&interval=&start=&end=&limit=`
- `POST /ingestion/run` — body: `{"instrument_symbols": ["AAPL"], "days": 30, "interval": "1d", "source_name": "yfinance"}`

## Adding a new (e.g. paid) data provider
1. Implement `app/services/providers/base.py`'s `MarketDataProvider` interface in a new file.
2. Register it: add a `DataSource` row (`name`, `kind="paid"`) and call `register_provider(...)` in `app/main.py`.
3. Add `InstrumentSourceMapping` rows mapping your internal `Instrument.symbol` to that provider's ticker convention.

No changes to `PriceBar` or the ingestion service are needed — this is the point of the source-agnostic schema.
