import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PriceBarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instrument_id: uuid.UUID
    source_id: uuid.UUID
    ts: datetime
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    adjusted_close: float | None
