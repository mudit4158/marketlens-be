"""Gold analysis: COMEX futures (USD) vs MCX futures (INR) with USD/INR decomposition."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.instrument import Instrument
from app.models.price import PriceBar

router = APIRouter(prefix="/gold", tags=["gold-analysis"])

TROY_OZ_TO_10G = 10 / 31.1035   # used for COMEX→INR conversion display

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
    mcx_premium: float


class GoldSummary(BaseModel):
    comex_usd_latest: float | None
    comex_usd_change_pct: float | None
    usd_inr_latest: float | None
    comex_inr_latest: float | None        # COMEX converted to INR/10g (no duty)
    mcx_inr_latest: float | None          # Actual MCX price INR/10g
    mcx_premium_abs: float | None         # MCX − COMEX_INR (actual spread)
    mcx_premium_pct: float | None
    has_mcx_data: bool
    period_start: str
    period_end: str
    trading_days: int


class GoldAnalysisResponse(BaseModel):
    range: str
    interval: str
    dates: list[str]
    comex_usd: list[float | None]
    usd_inr: list[float | None]
    comex_inr: list[float | None]         # COMEX converted to INR/10g
    mcx_inr: list[float | None]           # Actual MCX price (None when not yet available)
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
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    idx = pd.to_datetime([r.ts for r in rows], utc=True)
    return pd.Series([float(r.close) for r in rows], index=idx, name=symbol)


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 2)


def _val(series: pd.Series, idx: int) -> float | None:
    if series.empty or len(series) <= abs(idx):
        return None
    v = series.iloc[idx]
    return float(v) if pd.notna(v) else None


@router.get("/analysis", response_model=GoldAnalysisResponse)
def gold_analysis(
    range: str = Query(default="1M", pattern="^(1H|3H|12H|2D|5D|1M|6M|1Y|5Y|YTD)$"),
    db: Session = Depends(get_db),
) -> GoldAnalysisResponse:
    """COMEX gold (USD/oz) vs MCX Gold futures (INR/10g) with USD/INR decomposition."""
    cfg = RANGE_CONFIG[range]
    interval: str = cfg["interval"]
    now = datetime.now(timezone.utc)

    if cfg.get("ytd"):
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif "hours" in cfg:
        since = now - timedelta(hours=cfg["hours"])
    else:
        since = now - timedelta(days=cfg["days"])

    gold    = _fetch_closes(db, "GOLD",     since, interval)   # COMEX USD/oz
    usdinr  = _fetch_closes(db, "USDINR",   since, interval)
    mcx_gold = _fetch_closes(db, "MCX_GOLD", since, interval)  # MCX INR/10g

    # Normalise daily bars to midnight UTC for alignment (keeps DatetimeIndex intact)
    if interval == "1d":
        for s in (gold, usdinr, mcx_gold):
            if isinstance(s.index, pd.DatetimeIndex):
                s.index = s.index.normalize().tz_localize(None).tz_localize("UTC")

    empty_summary = GoldSummary(
        comex_usd_latest=None, comex_usd_change_pct=None, usd_inr_latest=None,
        comex_inr_latest=None, mcx_inr_latest=None,
        mcx_premium_abs=None, mcx_premium_pct=None,
        has_mcx_data=False, period_start="", period_end="", trading_days=0,
    )

    if gold.empty and usdinr.empty and mcx_gold.empty:
        return GoldAnalysisResponse(
            range=range, interval=interval, dates=[],
            comex_usd=[], usd_inr=[], comex_inr=[], mcx_inr=[],
            summary=empty_summary, waterfall=[],
        )

    combined = pd.DataFrame({
        "gold": gold,
        "usdinr": usdinr,
        "mcx_gold": mcx_gold,
    }).sort_index()

    # Forward-fill COMEX + FX (liquid, continuous); MCX may have gaps around expiry
    combined[["gold", "usdinr"]] = combined[["gold", "usdinr"]].ffill().bfill()
    combined["mcx_gold"] = combined["mcx_gold"].ffill()

    combined = combined[combined.index >= since]

    # COMEX expressed in INR per 10g (no duty — raw conversion for comparison)
    combined["comex_inr"] = combined["gold"] * TROY_OZ_TO_10G * combined["usdinr"]

    fmt = DATE_FMT.get(interval, "%b %d")
    dates = [ts.strftime(fmt) for ts in combined.index]

    def col(name: str) -> list[float | None]:
        return [round(v, 2) if pd.notna(v) else None for v in combined[name]]

    has_mcx = not combined["mcx_gold"].dropna().empty

    # ── Summary ───────────────────────────────────────────────────────────────
    comex_latest  = _val(combined["gold"], -1)
    comex_first   = _val(combined["gold"], 0)
    inr_latest    = _val(combined["usdinr"], -1)
    cinr_latest   = _val(combined["comex_inr"], -1)
    mcx_latest    = _val(combined["mcx_gold"], -1)
    mcx_prem_abs  = round(mcx_latest - cinr_latest, 2) if (mcx_latest and cinr_latest) else None
    mcx_prem_pct  = _pct(mcx_latest, cinr_latest)

    summary = GoldSummary(
        comex_usd_latest=round(comex_latest, 2) if comex_latest else None,
        comex_usd_change_pct=_pct(comex_latest, comex_first),
        usd_inr_latest=round(inr_latest, 4) if inr_latest else None,
        comex_inr_latest=round(cinr_latest, 2) if cinr_latest else None,
        mcx_inr_latest=round(mcx_latest, 2) if mcx_latest else None,
        mcx_premium_abs=mcx_prem_abs,
        mcx_premium_pct=mcx_prem_pct,
        has_mcx_data=has_mcx,
        period_start=dates[0] if dates else "",
        period_end=dates[-1] if dates else "",
        trading_days=len(combined),
    )

    # ── Waterfall (weekly decomposition, daily interval only) ─────────────────
    waterfall: list[WaterfallEntry] = []
    if interval == "1d" and not combined.empty:
        weekly = combined.resample("W-FRI").agg({
            "gold": "last", "usdinr": "last",
            "comex_inr": "last", "mcx_gold": "last",
        }).dropna(subset=["gold", "usdinr"])

        prev = None
        for ts, row in weekly.iterrows():
            if prev is None:
                prev = row
                continue
            comex_effect = round(
                (row["gold"] - prev["gold"]) * TROY_OZ_TO_10G * prev["usdinr"], 2
            )
            forex_effect = round(
                row["gold"] * TROY_OZ_TO_10G * (row["usdinr"] - prev["usdinr"]), 2
            )
            # MCX premium change: how much MCX moved above/below the COMEX-INR conversion
            mcx_this = row["mcx_gold"] if pd.notna(row["mcx_gold"]) else row["comex_inr"]
            mcx_prev = prev["mcx_gold"] if pd.notna(prev["mcx_gold"]) else prev["comex_inr"]
            mcx_premium = round((mcx_this - row["comex_inr"]) - (mcx_prev - prev["comex_inr"]), 2)

            waterfall.append(WaterfallEntry(
                label=f"{prev.name.strftime('%b %d')}–{ts.strftime('%b %d')}",
                comex_effect=comex_effect,
                forex_effect=forex_effect,
                mcx_premium=mcx_premium,
            ))
            prev = row

    return GoldAnalysisResponse(
        range=range,
        interval=interval,
        dates=dates,
        comex_usd=col("gold"),
        usd_inr=col("usdinr"),
        comex_inr=col("comex_inr"),
        mcx_inr=col("mcx_gold"),
        summary=summary,
        waterfall=waterfall,
    )
