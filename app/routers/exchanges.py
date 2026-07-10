from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.market import Exchange
from app.schemas.exchange import ExchangeCreate, ExchangeOut

router = APIRouter(prefix="/exchanges", tags=["exchanges"])


@router.get("", response_model=list[ExchangeOut])
def list_exchanges(db: Session = Depends(get_db)) -> list[Exchange]:
    return db.query(Exchange).order_by(Exchange.code).all()


@router.post("", response_model=ExchangeOut, status_code=201)
def create_exchange(payload: ExchangeCreate, db: Session = Depends(get_db)) -> Exchange:
    existing = db.query(Exchange).filter_by(code=payload.code.upper()).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Exchange '{payload.code}' already exists.")

    exchange = Exchange(
        code=payload.code.upper(),
        name=payload.name,
        country=payload.country,
        timezone=payload.timezone,
    )
    db.add(exchange)
    db.commit()
    db.refresh(exchange)
    return exchange
