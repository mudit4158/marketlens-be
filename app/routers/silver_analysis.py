"""Silver analysis: COMEX Silver (USD/oz) vs MCX Silver (INR/kg) with USD/INR decomposition."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.gold_analysis import (
    DATE_FMT,
    RANGE_CONFIG,
    GoldAnalysisResponse,
    GoldSummary,
    WaterfallEntry,
    _fetch_closes,
    _pct,
    _val,
)

router = APIRouter(prefix="/silver", tags=["silver-analysis"])

# COMEX Silver $/oz → INR/kg: 1 kg = 1000g / 31.1035 g/troy_oz
TROY_OZ_TO_KG = 1000 / 31.1035


@router.get("/analysis", response_model=GoldAnalysisResponse)
def silver_analysis(
    range: str = Query(default="1M", pattern="^(1H|3H|12H|2D|5D|1M|6M|1Y|5Y|YTD)$"),
    db: Session = Depends(get_db),
) -> GoldAnalysisResponse:
    """COMEX silver (USD/oz) vs MCX Silver futures (INR/kg) with USD/INR decomposition."""
    cfg = RANGE_CONFIG[range]
    interval: str = cfg["interval"]
    now = datetime.now(timezone.utc)

    if cfg.get("ytd"):
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif "hours" in cfg:
        since = now - timedelta(hours=cfg["hours"])
    else:
        since = now - timedelta(days=cfg["days"])

    silver     = _fetch_closes(db, "SILVER",     since, interval)   # COMEX USD/oz
    usdinr     = _fetch_closes(db, "USDINR",     since, interval)
    mcx_silver = _fetch_closes(db, "MCX_SILVER", since, interval)   # MCX INR/kg

    if interval == "1d":
        for s in (silver, usdinr, mcx_silver):
            if isinstance(s.index, pd.DatetimeIndex) and not s.empty:
                s.index = s.index.floor("D")

    empty_summary = GoldSummary(
        comex_usd_latest=None, comex_usd_change_pct=None, usd_inr_latest=None,
        comex_inr_latest=None, mcx_inr_latest=None,
        mcx_premium_abs=None, mcx_premium_pct=None,
        has_mcx_data=False, period_start="", period_end="", trading_days=0,
    )

    if silver.empty and usdinr.empty and mcx_silver.empty:
        return GoldAnalysisResponse(
            commodity="silver", range=range, interval=interval,
            timestamps=[], dates=[],
            comex_usd=[], usd_inr=[], comex_inr=[], mcx_inr=[],
            summary=empty_summary, waterfall=[],
        )

    combined = pd.DataFrame({
        "silver": silver,
        "usdinr": usdinr,
        "mcx_silver": mcx_silver,
    }).sort_index()

    combined[["silver", "usdinr"]] = combined[["silver", "usdinr"]].ffill().bfill()
    combined["mcx_silver"] = combined["mcx_silver"].ffill()
    combined = combined[combined.index >= since]

    # COMEX Silver in INR/kg
    combined["comex_inr"] = combined["silver"] * TROY_OZ_TO_KG * combined["usdinr"]

    fmt = DATE_FMT.get(interval, "%b %d")
    dates = [ts.strftime(fmt) for ts in combined.index]
    timestamps = [ts.isoformat() for ts in combined.index]

    def col(name: str) -> list[float | None]:
        return [round(v, 2) if pd.notna(v) else None for v in combined[name]]

    has_mcx = not combined["mcx_silver"].dropna().empty

    silver_latest  = _val(combined["silver"], -1)
    silver_first   = _val(combined["silver"], 0)
    inr_latest     = _val(combined["usdinr"], -1)
    cinr_latest    = _val(combined["comex_inr"], -1)
    mcx_latest     = _val(combined["mcx_silver"], -1)
    mcx_prem_abs   = round(mcx_latest - cinr_latest, 2) if (mcx_latest and cinr_latest) else None
    mcx_prem_pct   = _pct(mcx_latest, cinr_latest)

    summary = GoldSummary(
        comex_usd_latest=round(silver_latest, 4) if silver_latest else None,
        comex_usd_change_pct=_pct(silver_latest, silver_first),
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

    # Waterfall (daily only)
    waterfall: list[WaterfallEntry] = []
    if interval == "1d" and not combined.empty and isinstance(combined.index, pd.DatetimeIndex):
        weekly = combined.resample("W-FRI").agg({
            "silver": "last", "usdinr": "last",
            "comex_inr": "last", "mcx_silver": "last",
        }).dropna(subset=["silver", "usdinr"])

        prev = None
        for ts, row in weekly.iterrows():
            if prev is None:
                prev = row
                continue
            comex_effect = round(
                (row["silver"] - prev["silver"]) * TROY_OZ_TO_KG * prev["usdinr"], 2
            )
            forex_effect = round(
                row["silver"] * TROY_OZ_TO_KG * (row["usdinr"] - prev["usdinr"]), 2
            )
            mcx_this = row["mcx_silver"] if pd.notna(row["mcx_silver"]) else row["comex_inr"]
            mcx_prev = prev["mcx_silver"] if pd.notna(prev["mcx_silver"]) else prev["comex_inr"]
            mcx_premium = round((mcx_this - row["comex_inr"]) - (mcx_prev - prev["comex_inr"]), 2)

            waterfall.append(WaterfallEntry(
                label=f"{prev.name.strftime('%b %d')}–{ts.strftime('%b %d')}",
                comex_effect=comex_effect,
                forex_effect=forex_effect,
                mcx_premium=mcx_premium,
            ))
            prev = row

    return GoldAnalysisResponse(
        commodity="silver",
        range=range,
        interval=interval,
        timestamps=timestamps,
        dates=dates,
        comex_usd=col("silver"),
        usd_inr=col("usdinr"),
        comex_inr=col("comex_inr"),
        mcx_inr=col("mcx_silver"),
        summary=summary,
        waterfall=waterfall,
    )
