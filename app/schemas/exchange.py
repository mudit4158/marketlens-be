import uuid

from pydantic import BaseModel, ConfigDict


class ExchangeCreate(BaseModel):
    code: str
    name: str
    country: str
    timezone: str = "UTC"


class ExchangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    country: str
    timezone: str
