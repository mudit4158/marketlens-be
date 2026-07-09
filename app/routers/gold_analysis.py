"""Gold-specific analysis endpoint: COMEX vs computed MCX proxy, USD/INR decomposition."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.instrument import Instrument
from app.models.price import PriceBar

router = APIRouter(prefix="/gold", tags=["gold-analysis"])

# Troy oz → grams conversion; MCX quotes per 10g.
TROY_OZ_TO_10G = 10 / 31.1035

# Approximate India import duty + GST + other levies as a multiplier on the converted price.
INDIA_DUTY_FACTOR = 1.18

RANGE_CONFIG: dict[str, dict] = {
    "1H":  {"interval": "5m",  "hours": 1},
    "3H":  {"interval": "5m",  "hours": 3},
    "12H": {"interval": "5m",  "hours": 12},
    "2D":  {"interval": "1h",  "days": 2},
    "5D":  {"interval": "1h",  "days": 5},
    "1M":  {"interval": "1d",  "days": 30},
    "6M":  {"interval": "1d",  "days": 180},
    "1Y":  {"interval": "1d",  "days": 365},
    "5Y":  {"interval": "1d",  "days": 1825},
    "YTD": {"interval": "1d",  "ytd": True},
}

DATE_FMT: dict[str, str] = {
    "5m": "%b %d %H:%M",
    "1h": "%b %d %H:%M",
    "1d": "%b %d",
}


class WaterfallEntry(BaseModel):
    label: str
    comex_effect: float
    forex_effect: float
    premium_residual: float


class GoldSummary(BaseModel):
    comex_latest: float | None
    comex_change_pct: float | None
    usd_inr_latest: float | None
    comex_inr_latest: float | None
    comex_inr_duty_latest: float | None
    india_premium: float | None
    india_premium_pct: float | None
    period_start: str
    period_end: str
    trading_days: int


class GoldAnalysisResponse(BaseModel):
    range: str
    interval: str
    dates: list[str]
    comex_usd: list[float | None]
    usd_inr: list[float | None]
    comex_inr: list[float | None]
    comex_inr_duty: list[float | None]
    summary: GoldSummary
    waterfall: list[WaterfallEntry]


def _fetch_closes(db: Session, symbol: str, since: datetime, interval: str) -> pd.Series:
    inst = db.query(Instrument).filter(Instrument.symbol == symbol).one_or_none()
    if inst is None:
        return pd.Series(dtype=float)
    rows = (
        db.query(PriceBar)
        .filter(
            PriceBar.instrument_id == inst.id,
            PriceBar.interval == interval,
            PriceBar.ts >= since,
        )
        .order_by(PriceBar.ts.asc())
        .all()
    )
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r.ts for r in rows], utc=True)
    return pd.Series([float(r.close) for r in rows], index=idx, name=symbol)


def _pct(a: float, b: float | None) -> float | None:
    if b is None or b == 0:
        return None
    return round((a - b) / b * 100, 2)


@router.get("/analysis", response_model=GoldAnalysisResponse)
def gold_analysis(
    range: str = Query(default="1M", pattern="^(1H|3H|12H|2D|5D|1M|6M|1Y|5Y|YTD)$"),
    db: Session = Depends(get_db),
) -> GoldAnalysisResponse:
    """Return COMEX gold, USD/INR, and derived ₹-converted series for the requested time range."""
    cfg = RANGE_CONFIG[range]
    interval: str = cfg["interval"]
    now = datetime.now(timezone.utc)

    if cfg.get("ytd"):
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif "hours" in cfg:
        since = now - timedelta(hours=cfg["hours"])
    else:
        since = now - timedelta(days=cfg["days"])

    gold = _fetch_closes(db, "GOLD", since, interval)
    usdinr = _fetch_closes(db, "USDINR", since, interval)

    # For daily bars: align on date only (COMEX and FX settle at different times).
    if interval == "1d":
        gold.index = gold.index.normalize()
        usdinr.index = usdinr.index.normalize()

    combined = pd.DataFrame({"gold": gold, "usdinr": usdinr}).sort_index()
    combined = combined.ffill().bfill()

    # Filter to since *after* backfill (backfill may pull a little before since).
    combined = combined[combined.index >= since]

    combined["comex_inr"] = combined["gold"] * TROY_OZ_TO_10G * combined["usdinr"]
    combined["comex_inr_duty"] = combined["comex_inr"] * INDIA_DUTY_FACTOR

    fmt = DATE_FMT.get(interval, "%b %d")
    dates = [ts.strftime(fmt) for ts in combined.index]

    def col(name: str) -> list[float | None]:
        return [round(v, 2) if pd.notna(v) else None for v in combined[name]]

    # ── summary ──────────────────────────────────────────────────────────────
    latest_gold = float(combined["gold"].iloc[-1]) if not combined.empty else None
    first_gold  = float(combined["gold"].iloc[0])  if not combined.empty else None
    latest_inr  = float(combined["usdinr"].iloc[-1]) if not combined.empty else None
    latest_cinr = float(combined["comex_inr"].iloc[-1]) if not combined.empty else None
    latest_cduty = float(combined["comex_inr_duty"].iloc[-1]) if not combined.empty else None
    india_prem  = round(latest_cduty - latest_cinr, 2) if (latest_cduty and latest_cinr) else None
    india_prem_pct = _pct(latest_cduty, latest_cinr)

    period_start = dates[0] if dates else ""
    period_end   = dates[-1] if dates else ""

    summary = GoldSummary(
        comex_latest=round(latest_gold, 2) if latest_gold else None,
        comex_change_pct=_pct(latest_gold, first_gold) if latest_gold else None,
        usd_inr_latest=round(latest_inr, 4) if latest_inr else None,
        comex_inr_latest=round(latest_cinr, 2) if latest_cinr else None,
        comex_inr_duty_latest=round(latest_cduty, 2) if latest_cduty else None,
        india_premium=india_prem,
        india_premium_pct=india_prem_pct,
        period_start=period_start,
        period_end=period_end,
        trading_days=len(combined),
    )

    # ── waterfall (weekly for 1d; skip for intraday) ─────────────────────────
    waterfall: list[WaterfallEntry] = []
    if interval == "1d" and not combined.empty:
        weekly = combined.resample("W-FRI").agg({"gold": "last", "usdinr": "last", "comex_inr": "last"}).dropna()
        prev = None
        for ts, row in weekly.iterrows():
            if prev is None:
                prev = row
                continue
            # MCX change approximated as comex_inr change.
            total_change = round(row["comex_inr"] - prev["comex_inr"], 2)
            # COMEX contribution: delta gold price expressed in ₹ (holding FX constant).
            comex_effect = round((row["gold"] - prev["gold"]) * TROY_OZ_TO_10G * prev["usdinr"], 2)
            # FX contribution: delta FX applied to new COMEX price.
            forex_effect = round(row["gold"] * TROY_OZ_TO_10G * (row["usdinr"] - prev["usdinr"]), 2)
            # Premium residual = what's left (duty/premium changes).
            premium_residual = round(total_change - comex_effect - forex_effect, 2)
            label = f"{prev.name.strftime('%b %d')}–{ts.strftime('%b %d')}"
            waterfall.append(WaterfallEntry(
                label=label,
                comex_effect=comex_effect,
                forex_effect=forex_effect,
                premium_residual=premium_residual,
            ))
            prev = row

    return GoldAnalysisResponse(
        range=range,
        interval=interval,
        dates=dates,
        comex_usd=col("gold"),
        usd_inr=col("usdinr"),
        comex_inr=col("comex_inr"),
        comex_inr_duty=col("comex_inr_duty"),
        summary=summary,
        waterfall=waterfall,
    )
