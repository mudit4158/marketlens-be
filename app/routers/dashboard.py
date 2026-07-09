"""Dashboard-ready endpoints that go beyond raw OHLCV retrieval.

Designed to be consumed directly by chart components — computed server-side so the
frontend only needs simple fetch+render, no data-wrangling logic.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.instrument import AssetClass, Instrument
from app.models.price import PriceBar
from app.scheduler import get_scheduler_status

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ─── response shapes ────────────────────────────────────────────────────────

class InstrumentSummary(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    asset_class: str
    sector: str | None
    currency: str
    latest_close: float | None
    latest_ts: datetime | None
    change_pct_1d: float | None
    change_pct_1w: float | None
    change_pct_1m: float | None


class OHLCVBar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None


class ChartData(BaseModel):
    symbol: str
    interval: str
    bars: list[OHLCVBar]
    sma_20: dict[str, float | None]   # {ts_iso: value}
    sma_50: dict[str, float | None]
    sma_200: dict[str, float | None]


class HeatmapEntry(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    asset_class: str
    sector: str | None
    change_pct: float | None


class ComparePoint(BaseModel):
    ts: datetime
    values: dict[str, float | None]   # symbol → normalized price (base=100 at first common date)


# ─── helpers ────────────────────────────────────────────────────────────────

def _load_bars_df(db: Session, instrument_id: str, since: datetime, interval: str = "1d") -> pd.DataFrame:
    rows = (
        db.query(PriceBar)
        .filter(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == interval,
            PriceBar.ts >= since,
        )
        .order_by(PriceBar.ts.asc())
        .all()
    )
    if not rows:
        return pd.DataFrame()
    data = [
        {"ts": r.ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
        for r in rows
    ]
    df = pd.DataFrame(data).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 4)


# ─── endpoints ──────────────────────────────────────────────────────────────

@router.get("/summary", response_model=list[InstrumentSummary])
def summary(
    asset_class: AssetClass | None = Query(default=None),
    sector: str | None = Query(default=None),
    exchange_id: str | None = Query(default=None),
    interval: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> list[InstrumentSummary]:
    """Latest price + 1d/1w/1m % change for all (or filtered) active instruments."""
    query = db.query(Instrument).filter(Instrument.is_active.is_(True))
    if asset_class:
        query = query.filter(Instrument.asset_class == asset_class)
    if sector:
        query = query.filter(Instrument.sector == sector)
    if exchange_id:
        query = query.filter(Instrument.exchange_id == exchange_id)
    instruments = query.all()

    # Fetch ~35 days of bars per instrument to cover 1-month % change.
    since = datetime.now(timezone.utc) - timedelta(days=35)
    results = []
    for inst in instruments:
        df = _load_bars_df(db, str(inst.id), since, interval)
        if df.empty:
            latest_close = latest_ts = change_1d = change_1w = change_1m = None
        else:
            latest_close = float(df["close"].iloc[-1])
            latest_ts = df.index[-1].to_pydatetime()
            close_1d_ago = float(df["close"].iloc[-2]) if len(df) >= 2 else None
            close_1w_ago = float(df["close"].iloc[-6]) if len(df) >= 6 else None
            close_1m_ago = float(df["close"].iloc[0]) if len(df) >= 20 else None
            change_1d = _pct_change(latest_close, close_1d_ago)
            change_1w = _pct_change(latest_close, close_1w_ago)
            change_1m = _pct_change(latest_close, close_1m_ago)

        results.append(InstrumentSummary(
            instrument_id=str(inst.id),
            symbol=inst.symbol,
            name=inst.name,
            asset_class=inst.asset_class.value,
            sector=inst.sector,
            currency=inst.currency,
            latest_close=latest_close,
            latest_ts=latest_ts,
            change_pct_1d=change_1d,
            change_pct_1w=change_1w,
            change_pct_1m=change_1m,
        ))
    return results


@router.get("/chart/{symbol}", response_model=ChartData)
def chart(
    symbol: str,
    days: Annotated[int, Query(ge=1, le=1825)] = 90,
    interval: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> ChartData:
    """OHLCV bars + SMA-20/50/200 for a single instrument, ready to render as a candlestick+overlay chart."""
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).one_or_none()
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument '{symbol}' not found")

    # Fetch extra leading bars so SMA-200 is computed accurately within the requested window.
    since = datetime.now(timezone.utc) - timedelta(days=days + 220)
    df = _load_bars_df(db, str(instrument.id), since, interval)

    if df.empty:
        return ChartData(symbol=symbol, interval=interval, bars=[], sma_20={}, sma_50={}, sma_200={})

    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()

    # Trim to the actually requested window for response.
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df[df.index >= cutoff]

    def sma_dict(col: str) -> dict[str, float | None]:
        return {
            ts.isoformat(): (round(v, 4) if pd.notna(v) else None)
            for ts, v in df[col].items()
        }

    bars = [
        OHLCVBar(ts=ts.to_pydatetime(), open=row["open"], high=row["high"],
                 low=row["low"], close=row["close"], volume=row.get("volume"))
        for ts, row in df.iterrows()
    ]
    return ChartData(
        symbol=symbol,
        interval=interval,
        bars=bars,
        sma_20=sma_dict("sma_20"),
        sma_50=sma_dict("sma_50"),
        sma_200=sma_dict("sma_200"),
    )


@router.get("/heatmap", response_model=list[HeatmapEntry])
def heatmap(
    period: Annotated[str, Query(pattern="^(1d|1w|1m|3m|6m|1y)$")] = "1w",
    asset_class: AssetClass | None = Query(default=None),
    interval: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> list[HeatmapEntry]:
    """% change over a period for all instruments — use this to render colour-coded heatmaps by sector/asset class."""
    period_days = {"1d": 1, "1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}
    n = period_days[period]

    query = db.query(Instrument).filter(Instrument.is_active.is_(True))
    if asset_class:
        query = query.filter(Instrument.asset_class == asset_class)
    instruments = query.all()

    since = datetime.now(timezone.utc) - timedelta(days=n + 5)
    results = []
    for inst in instruments:
        df = _load_bars_df(db, str(inst.id), since, interval)
        if df.empty or len(df) < 2:
            change = None
        else:
            latest = float(df["close"].iloc[-1])
            base = float(df["close"].iloc[max(0, len(df) - n - 1)])
            change = _pct_change(latest, base)
        results.append(HeatmapEntry(
            instrument_id=str(inst.id),
            symbol=inst.symbol,
            name=inst.name,
            asset_class=inst.asset_class.value,
            sector=inst.sector,
            change_pct=change,
        ))
    return sorted(results, key=lambda x: (x.change_pct is None, x.change_pct or 0), reverse=True)


@router.get("/compare", response_model=list[ComparePoint])
def compare(
    symbols: Annotated[str, Query(description="Comma-separated symbols, e.g. AAPL,MSFT,GOLD")],
    days: Annotated[int, Query(ge=1, le=1825)] = 90,
    interval: str = Query(default="1d"),
    db: Session = Depends(get_db),
) -> list[ComparePoint]:
    """Normalised price series (base=100 at the first common date) for multiple symbols — use for comparison line charts."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(symbol_list) < 1:
        raise HTTPException(status_code=422, detail="Provide at least one symbol.")
    if len(symbol_list) > 10:
        raise HTTPException(status_code=422, detail="Maximum 10 symbols per comparison.")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    series: dict[str, pd.Series] = {}
    for sym in symbol_list:
        inst = db.query(Instrument).filter(Instrument.symbol == sym).one_or_none()
        if inst is None:
            raise HTTPException(status_code=404, detail=f"Instrument '{sym}' not found")
        df = _load_bars_df(db, str(inst.id), since, interval)
        if not df.empty:
            series[sym] = df["close"]

    if not series:
        return []

    combined = pd.DataFrame(series).sort_index()
    # Normalise each series to 100 at the first date where ALL series have data.
    combined = combined.dropna(how="any")
    if combined.empty:
        return []

    base = combined.iloc[0]
    normalised = (combined / base * 100).round(4)

    points = []
    for ts, row in normalised.iterrows():
        points.append(ComparePoint(
            ts=ts.to_pydatetime(),
            values={sym: (float(row[sym]) if pd.notna(row[sym]) else None) for sym in normalised.columns},
        ))
    return points


@router.get("/scheduler/status")
def scheduler_status() -> dict:
    """Returns whether the periodic ingestion scheduler is running and when the next job fires."""
    return get_scheduler_status()
