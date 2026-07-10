import uuid

from pydantic import BaseModel, ConfigDict

from app.models.instrument import AssetClass

ALLOWED_SECTORS = {
    "Metals", "Energy", "Forex", "Technology", "Finance",
    "Agriculture", "Indices", "Crypto", "Real Estate", "Other",
}


class InstrumentCreate(BaseModel):
    symbol: str
    source_name: str
    source_ticker: str
    exchange_code: str | None = None      # auto-detected from yfinance if omitted
    asset_class: AssetClass | None = None  # auto-detected from yfinance if omitted
    name: str | None = None               # auto-fetched from yfinance if omitted
    sector: str | None = None             # auto-fetched from yfinance if omitted
    currency: str | None = None           # auto-fetched from yfinance if omitted
    is_active: bool = True


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: str
    asset_class: AssetClass
    exchange_id: uuid.UUID | None
    sector: str | None
    industry: str | None
    currency: str
    is_active: bool
