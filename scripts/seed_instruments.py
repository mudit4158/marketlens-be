"""One-time/editable seed of exchanges, the yfinance data source, and a starter watchlist.

Run with: python -m scripts.seed_instruments
"""

from app.db import SessionLocal
from app.models.ingestion import DataSource, SourceKind
from app.models.instrument import AssetClass, Instrument, InstrumentSourceMapping
from app.models.market import Exchange

EXCHANGES = [
    {"code": "NASDAQ", "name": "Nasdaq Stock Market", "country": "US", "timezone": "America/New_York"},
    {"code": "NYSE", "name": "New York Stock Exchange", "country": "US", "timezone": "America/New_York"},
    {"code": "NSE", "name": "National Stock Exchange of India", "country": "IN", "timezone": "Asia/Kolkata"},
    {"code": "COMEX", "name": "Commodity Exchange", "country": "US", "timezone": "America/New_York"},
    {"code": "NYMEX", "name": "New York Mercantile Exchange", "country": "US", "timezone": "America/New_York"},
    {"code": "FOREX", "name": "Foreign Exchange Market", "country": "US", "timezone": "UTC"},
]

# (internal symbol, name, asset_class, exchange_code, sector, currency, yfinance ticker)
WATCHLIST = [
    ("AAPL", "Apple Inc.", AssetClass.equity, "NASDAQ", "Technology", "USD", "AAPL"),
    ("MSFT", "Microsoft Corp.", AssetClass.equity, "NASDAQ", "Technology", "USD", "MSFT"),
    ("RELIANCE", "Reliance Industries Ltd.", AssetClass.equity, "NSE", "Energy", "INR", "RELIANCE.NS"),
    ("TCS", "Tata Consultancy Services Ltd.", AssetClass.equity, "NSE", "Technology", "INR", "TCS.NS"),
    ("GOLD", "Gold Futures (Continuous)", AssetClass.commodity, "COMEX", "Metals", "USD", "GC=F"),
    ("CRUDE_OIL", "Crude Oil WTI Futures (Continuous)", AssetClass.commodity, "NYMEX", "Energy", "USD", "CL=F"),
    ("SILVER", "Silver Futures (Continuous)", AssetClass.commodity, "COMEX", "Metals", "USD", "SI=F"),
    ("USDINR", "US Dollar / Indian Rupee", AssetClass.currency, "FOREX", "Forex", "INR", "INR=X"),
]


def main() -> None:
    db = SessionLocal()
    try:
        exchanges_by_code = {}
        for data in EXCHANGES:
            exchange = db.query(Exchange).filter_by(code=data["code"]).one_or_none()
            if exchange is None:
                exchange = Exchange(**data)
                db.add(exchange)
                db.flush()
            exchanges_by_code[data["code"]] = exchange

        source = db.query(DataSource).filter_by(name="yfinance").one_or_none()
        if source is None:
            source = DataSource(name="yfinance", kind=SourceKind.free, is_active=True)
            db.add(source)
            db.flush()

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
                db.add(
                    InstrumentSourceMapping(
                        instrument_id=instrument.id, source_id=source.id, source_ticker=source_ticker
                    )
                )

        db.commit()
        print(f"Seeded {len(EXCHANGES)} exchanges and {len(WATCHLIST)} instruments.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
