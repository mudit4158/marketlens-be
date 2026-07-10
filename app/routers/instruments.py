import uuid

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ingestion import DataSource
from app.models.instrument import AssetClass, Instrument, InstrumentSourceMapping
from app.models.market import Exchange
from app.schemas.instrument import ALLOWED_SECTORS, InstrumentCreate, InstrumentOut

router = APIRouter(prefix="/instruments", tags=["instruments"])

# Maps yfinance quoteType → our AssetClass
_QUOTE_TYPE_MAP: dict[str, AssetClass] = {
    "EQUITY":       AssetClass.equity,
    "ETF":          AssetClass.etf,
    "FUTURE":       AssetClass.future,
    "CURRENCY":     AssetClass.currency,
    "CRYPTOCURRENCY": AssetClass.currency,
    "INDEX":        AssetClass.index,
    "COMMODITY":    AssetClass.commodity,
}


def _autofill_from_yfinance(source_ticker: str) -> dict:
    """Fetch instrument metadata from yfinance. Returns only keys that have values."""
    try:
        info = yf.Ticker(source_ticker).info
    except Exception:
        return {}

    result: dict = {}
    if info.get("longName"):
        result["name"] = info["longName"]
    if info.get("currency"):
        result["currency"] = info["currency"].upper()
    if info.get("quoteType"):
        result["asset_class"] = _QUOTE_TYPE_MAP.get(info["quoteType"].upper())
    if info.get("sector"):
        result["sector"] = info["sector"]
    if info.get("exchange"):
        result["exchange_code"] = info["exchange"]
    return result


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


@router.post("", response_model=InstrumentOut, status_code=201)
def create_instrument(payload: InstrumentCreate, db: Session = Depends(get_db)) -> Instrument:
    # ── Validate source exists ────────────────────────────────────────────────
    source = db.query(DataSource).filter_by(name=payload.source_name).one_or_none()
    if source is None:
        valid = [s.name for s in db.query(DataSource).all()]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown source_name '{payload.source_name}'. Valid values: {valid}",
        )

    # ── Validate symbol uniqueness ────────────────────────────────────────────
    if db.query(Instrument).filter_by(symbol=payload.symbol.upper()).one_or_none():
        raise HTTPException(status_code=409, detail=f"Instrument '{payload.symbol}' already exists.")

    # ── Auto-fill missing fields from yfinance ────────────────────────────────
    auto = _autofill_from_yfinance(payload.source_ticker)

    name = payload.name or auto.get("name") or payload.symbol
    currency = payload.currency or auto.get("currency") or "USD"
    asset_class = payload.asset_class or auto.get("asset_class")
    sector = payload.sector or auto.get("sector")
    exchange_code = payload.exchange_code or auto.get("exchange_code")

    if asset_class is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not determine asset_class automatically for '{payload.source_ticker}'. "
                   f"Please provide it explicitly. Allowed: {[e.value for e in AssetClass]}",
        )

    # ── Validate sector ───────────────────────────────────────────────────────
    if sector and sector not in ALLOWED_SECTORS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sector '{sector}'. Allowed values: {sorted(ALLOWED_SECTORS)}",
        )

    # ── Resolve exchange ──────────────────────────────────────────────────────
    exchange_id = None
    if exchange_code:
        exchange = db.query(Exchange).filter_by(code=exchange_code.upper()).one_or_none()
        if exchange is None:
            raise HTTPException(
                status_code=422,
                detail=f"Exchange '{exchange_code}' not found. Create it first via POST /exchanges.",
            )
        exchange_id = exchange.id

    # ── Create instrument ─────────────────────────────────────────────────────
    instrument = Instrument(
        symbol=payload.symbol.upper(),
        name=name,
        asset_class=asset_class,
        exchange_id=exchange_id,
        sector=sector,
        currency=currency,
        is_active=payload.is_active,
    )
    db.add(instrument)
    db.flush()

    db.add(InstrumentSourceMapping(
        instrument_id=instrument.id,
        source_id=source.id,
        source_ticker=payload.source_ticker,
    ))

    db.commit()
    db.refresh(instrument)
    return instrument
