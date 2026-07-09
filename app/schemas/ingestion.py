import uuid

from pydantic import BaseModel


class IngestionRunRequest(BaseModel):
    instrument_symbols: list[str] | None = None  # None = all active instruments
    # intervals to fetch — defaults to the three most useful for intraday + daily analysis.
    # days is optional; if omitted, the provider's max lookback per interval is used automatically.
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
