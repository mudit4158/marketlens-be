from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ingestion import DataSource
from app.models.instrument import Instrument
from app.schemas.ingestion import IngestionRunRequest, IngestionRunResult
from app.services.ingestion_service import ingest_instrument
from app.services.providers.yfinance_provider import YFinanceProvider

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_provider = YFinanceProvider()


@router.post("/run", response_model=list[IngestionRunResult])
def run_ingestion(request: IngestionRunRequest, db: Session = Depends(get_db)) -> list[IngestionRunResult]:
    source = db.query(DataSource).filter_by(name=request.source_name).one()

    query = db.query(Instrument).filter(Instrument.is_active.is_(True))
    if request.instrument_symbols:
        query = query.filter(Instrument.symbol.in_(request.instrument_symbols))
    instruments = query.all()

    end = date.today()
    results: list[IngestionRunResult] = []

    for interval in request.intervals:
        max_days = _provider.max_days_for_interval(interval)
        days = min(request.days, max_days) if request.days else max_days
        start = end - timedelta(days=days)

        for instrument in instruments:
            run = ingest_instrument(db, instrument, source, start=start, end=end, interval=interval)
            results.append(
                IngestionRunResult(
                    ingestion_run_id=run.id,
                    instrument_symbol=instrument.symbol,
                    interval=interval,
                    rows_ingested=run.rows_ingested,
                    status=run.status.value,
                    error_message=run.error_message,
                )
            )
    return results
