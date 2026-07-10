import uuid
from datetime import date

from pydantic import BaseModel


class IngestionRunRequest(BaseModel):
    instrument_symbols: list[str] | None = None  # None = all active instruments
    intervals: list[str] = ["1d", "1h", "5m"]
    days: int | None = None
    source_name: str = "yfinance"


class IngestionRunResult(BaseModel):
    ingestion_run_id: uuid.UUID
    instrument_symbol: str
    interval: str
    rows_ingested: int
    status: str
    error_message: str | None = None


class BackfillRequest(BaseModel):
    symbol: str
    intervals: list[str]
    start_date: date
    end_date: date


class BackfillJobRequest(BaseModel):
    source_name: str = "yfinance"
    requests: list[BackfillRequest]


class BackfillResult(BaseModel):
    symbol: str
    interval: str
    requested_start: date
    actual_start: date        # may differ from requested if capped by provider limit
    end_date: date
    rows_ingested: int
    status: str
    error_message: str | None = None
