import uuid
from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.ingestion import DataSource, IngestionRun, IngestionStatus
from app.models.instrument import Instrument, InstrumentSourceMapping
from app.models.price import PriceBar
from app.services.providers.base import MarketDataProvider

PROVIDERS: dict[str, MarketDataProvider] = {}


def register_provider(provider: MarketDataProvider) -> None:
    PROVIDERS[provider.name] = provider


def get_provider(name: str) -> MarketDataProvider:
    if name not in PROVIDERS:
        raise ValueError(f"No provider registered for data source '{name}'")
    return PROVIDERS[name]


def ingest_instrument(
    db: Session,
    instrument: Instrument,
    source: DataSource,
    start: date,
    end: date,
    interval: str = "1d",
) -> IngestionRun:
    run = IngestionRun(
        id=uuid.uuid4(),
        source_id=source.id,
        instrument_id=instrument.id,
        started_at=datetime.now(timezone.utc),
        status=IngestionStatus.running,
    )
    db.add(run)
    db.flush()

    try:
        mapping = (
            db.query(InstrumentSourceMapping)
            .filter_by(instrument_id=instrument.id, source_id=source.id)
            .one_or_none()
        )
        if mapping is None:
            raise ValueError(
                f"No InstrumentSourceMapping for instrument '{instrument.symbol}' and source '{source.name}'"
            )

        provider = get_provider(source.name)
        bars = provider.fetch_ohlcv(mapping.source_ticker, start=start, end=end, interval=interval)

        rows_ingested = 0
        for bar in bars:
            stmt = insert(PriceBar).values(
                id=uuid.uuid4(),
                instrument_id=instrument.id,
                source_id=source.id,
                ts=bar.ts,
                interval=interval,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                adjusted_close=bar.adjusted_close,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "source_id", "ts", "interval"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "adjusted_close": stmt.excluded.adjusted_close,
                },
            )
            db.execute(stmt)
            rows_ingested += 1

        run.status = IngestionStatus.success
        run.rows_ingested = rows_ingested
    except Exception as exc:  # noqa: BLE001 — recorded on the run row for visibility, not swallowed silently
        run.status = IngestionStatus.failed
        run.error_message = str(exc)
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.commit()

    return run
