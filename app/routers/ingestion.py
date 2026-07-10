from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ingestion import DataSource
from app.models.instrument import Instrument
from app.schemas.ingestion import (
    BackfillJobRequest,
    BackfillResult,
    IngestionRunRequest,
    IngestionRunResult,
)
from app.services.ingestion_service import ingest_instrument
from app.services.providers.yfinance_provider import YFinanceProvider

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_provider = YFinanceProvider()


@router.post("/run", response_model=list[IngestionRunResult])
def run_ingestion(request: IngestionRunRequest, db: Session = Depends(get_db)) -> list[IngestionRunResult]:
    """Trigger an ad-hoc ingestion run for active instruments (rolling window, same as the scheduler)."""
    source = db.query(DataSource).filter_by(name=request.source_name).one_or_none()
    if source is None:
        valid = [s.name for s in db.query(DataSource).all()]
        raise HTTPException(status_code=422, detail=f"Unknown source '{request.source_name}'. Valid: {valid}")

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
            results.append(IngestionRunResult(
                ingestion_run_id=run.id,
                instrument_symbol=instrument.symbol,
                interval=interval,
                rows_ingested=run.rows_ingested,
                status=run.status.value,
                error_message=run.error_message,
            ))
    return results


@router.post("/backfill", response_model=list[BackfillResult])
def backfill(request: BackfillJobRequest, db: Session = Depends(get_db)) -> list[BackfillResult]:
    """
    Ad-hoc historical backfill with per-symbol control over intervals and date ranges.
    Provider caps are enforced automatically — actual_start in the response shows
    the clamped start date when the requested range exceeds the provider's limit.
    """
    source = db.query(DataSource).filter_by(name=request.source_name).one_or_none()
    if source is None:
        valid = [s.name for s in db.query(DataSource).all()]
        raise HTTPException(status_code=422, detail=f"Unknown source '{request.source_name}'. Valid: {valid}")

    results: list[BackfillResult] = []

    for req in request.requests:
        instrument = db.query(Instrument).filter_by(symbol=req.symbol.upper()).one_or_none()
        if instrument is None:
            results.append(BackfillResult(
                symbol=req.symbol,
                interval="—",
                requested_start=req.start_date,
                actual_start=req.start_date,
                end_date=req.end_date,
                rows_ingested=0,
                status="error",
                error_message=f"Instrument '{req.symbol}' not found.",
            ))
            continue

        for interval in req.intervals:
            max_days = _provider.max_days_for_interval(interval)
            earliest_allowed = date.today() - timedelta(days=max_days)

            # Clamp start to provider limit
            actual_start = max(req.start_date, earliest_allowed)

            if actual_start >= req.end_date:
                results.append(BackfillResult(
                    symbol=req.symbol,
                    interval=interval,
                    requested_start=req.start_date,
                    actual_start=actual_start,
                    end_date=req.end_date,
                    rows_ingested=0,
                    status="skipped",
                    error_message=f"Requested range is outside provider limit for '{interval}' "
                                  f"(max {max_days} days back). Earliest allowed: {earliest_allowed}.",
                ))
                continue

            run = ingest_instrument(
                db, instrument, source,
                start=actual_start,
                end=req.end_date,
                interval=interval,
            )
            results.append(BackfillResult(
                symbol=req.symbol,
                interval=interval,
                requested_start=req.start_date,
                actual_start=actual_start,
                end_date=req.end_date,
                rows_ingested=run.rows_ingested,
                status=run.status.value,
                error_message=run.error_message,
            ))

    return results
