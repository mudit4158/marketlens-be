from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import NamedTuple


class NormalizedBar(NamedTuple):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    adjusted_close: float | None


class MarketDataProvider(ABC):
    """Common interface every data source (free or paid) must implement.

    Swapping or adding a provider means implementing this interface and
    registering a `DataSource` row + `InstrumentSourceMapping` rows — no
    changes needed to `PriceBar` or the ingestion service.
    """

    name: str

    @abstractmethod
    def fetch_ohlcv(
        self, source_ticker: str, start: date, end: date, interval: str = "1d"
    ) -> list[NormalizedBar]:
        raise NotImplementedError
