import uuid

from pydantic import BaseModel, ConfigDict

from app.models.instrument import AssetClass


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
