import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.instrument import AssetClass, Instrument
from app.models.price import PriceBar
from app.schemas.price import PriceBarOut

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("", response_model=list[PriceBarOut])
def get_prices(
    instrument_id: uuid.UUID | None = Query(default=None),
    symbol: str | None = Query(default=None),
    asset_class: AssetClass | None = Query(default=None),
    exchange_id: uuid.UUID | None = Query(default=None),
    interval: str = Query(default="1d"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=1000, le=10000),
    db: Session = Depends(get_db),
) -> list[PriceBar]:
    query = db.query(PriceBar).filter(PriceBar.interval == interval)

    if instrument_id is not None:
        query = query.filter(PriceBar.instrument_id == instrument_id)
    elif symbol is not None:
        instrument = db.query(Instrument).filter(Instrument.symbol == symbol).one_or_none()
        if instrument is None:
            raise HTTPException(status_code=404, detail=f"Instrument '{symbol}' not found")
        query = query.filter(PriceBar.instrument_id == instrument.id)
    elif asset_class is not None or exchange_id is not None:
        instrument_query = db.query(Instrument.id)
        if asset_class is not None:
            instrument_query = instrument_query.filter(Instrument.asset_class == asset_class)
        if exchange_id is not None:
            instrument_query = instrument_query.filter(Instrument.exchange_id == exchange_id)
        query = query.filter(PriceBar.instrument_id.in_(instrument_query))

    if start is not None:
        query = query.filter(PriceBar.ts >= start)
    if end is not None:
        query = query.filter(PriceBar.ts <= end)

    return query.order_by(PriceBar.ts.desc()).limit(limit).all()
