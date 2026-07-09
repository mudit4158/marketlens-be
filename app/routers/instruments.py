import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.instrument import AssetClass, Instrument
from app.schemas.instrument import InstrumentOut

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("", response_model=list[InstrumentOut])
def list_instruments(
    asset_class: AssetClass | None = Query(default=None),
    exchange_id: uuid.UUID | None = Query(default=None),
    sector: str | None = Query(default=None),
    is_active: bool | None = Query(default=True),
    db: Session = Depends(get_db),
) -> list[Instrument]:
    query = db.query(Instrument)
    if asset_class is not None:
        query = query.filter(Instrument.asset_class == asset_class)
    if exchange_id is not None:
        query = query.filter(Instrument.exchange_id == exchange_id)
    if sector is not None:
        query = query.filter(Instrument.sector == sector)
    if is_active is not None:
        query = query.filter(Instrument.is_active == is_active)
    return query.order_by(Instrument.symbol).all()
