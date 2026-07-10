"""Periodic ingestion scheduler.

Runs inside the FastAPI process via APScheduler. Kicked off in main.py's lifespan context.
Each scheduled run fetches the last N days of data for all active instruments using the
configured source, and upserts into price_bars — so re-runs are always idempotent.
"""

import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.models.ingestion import DataSource
from app.models.instrument import Instrument
from app.services.ingestion_service import ingest_instrument

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_upstox_token_refresh() -> None:
    """Refresh the Upstox access token using stored credentials + TOTP.
    Runs every 12 hours so the token never goes stale between the daily 3:30 AM IST expiry.
    """
    from app.services.upstox_auth import auto_refresh_token

    db = SessionLocal()
    try:
        success = auto_refresh_token(db)
        if not success:
            logger.warning("Upstox token refresh failed or skipped — check credentials config.")
    except Exception:
        logger.exception("Unhandled error in Upstox token refresh job.")
    finally:
        db.close()


def _run_ingestion_job() -> None:
    from app.services.providers.yfinance_provider import YFinanceProvider

    settings = get_settings()
    intervals = settings.parsed_intervals()
    provider = YFinanceProvider()

    db = SessionLocal()
    try:
        source = db.query(DataSource).filter_by(name=settings.scheduler_source_name, is_active=True).one_or_none()
        if source is None:
            logger.error("Scheduled ingestion: DataSource '%s' not found or inactive.", settings.scheduler_source_name)
            return

        instruments = db.query(Instrument).filter(Instrument.is_active.is_(True)).all()
        if not instruments:
            logger.warning("Scheduled ingestion: no active instruments to fetch.")
            return

        end = date.today()
        logger.info(
            "Scheduled ingestion starting: source=%s, instruments=%d, intervals=%s",
            source.name, len(instruments), intervals,
        )

        for interval in intervals:
            # Each interval has its own max lookback — use 2× the cron cadence (5 days) as the
            # rolling window so we always patch any gaps from missed runs, capped by the provider's limit.
            max_days = provider.max_days_for_interval(interval)
            lookback = min(5, max_days)
            start = end - timedelta(days=lookback)

            success, failed = 0, 0
            for instrument in instruments:
                run = ingest_instrument(db, instrument, source, start=start, end=end, interval=interval)
                if run.status.value == "success":
                    success += 1
                else:
                    failed += 1
                    logger.error("Ingestion failed for %s [%s]: %s", instrument.symbol, interval, run.error_message)

            logger.info("Interval %s done: %d succeeded, %d failed.", interval, success, failed)

        logger.info("Scheduled ingestion complete.")
    except Exception:
        logger.exception("Unhandled error in scheduled ingestion job.")
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false.")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    if settings.scheduler_interval_minutes > 0:
        trigger = IntervalTrigger(minutes=settings.scheduler_interval_minutes, timezone="UTC")
        trigger_desc = f"every {settings.scheduler_interval_minutes} minutes"
    else:
        trigger = CronTrigger(
            hour=settings.scheduler_cron_hour,
            minute=settings.scheduler_cron_minute,
            day_of_week=settings.scheduler_cron_day_of_week,
            timezone="UTC",
        )
        trigger_desc = f"{settings.scheduler_cron_hour}:{settings.scheduler_cron_minute} UTC on {settings.scheduler_cron_day_of_week}"

    _scheduler.add_job(
        _run_ingestion_job,
        trigger=trigger,
        id="periodic_ingestion",
        name="Periodic OHLCV ingestion",
        misfire_grace_time=300,
        replace_existing=True,
    )

    # Upstox token refresh — runs at 04:00 and 16:00 IST (= 22:30 and 10:30 UTC)
    # Tokens expire at 03:30 IST, so the 04:00 IST run gets a fresh token every day.
    _scheduler.add_job(
        _run_upstox_token_refresh,
        trigger=CronTrigger(hour="22,10", minute="30", timezone="UTC"),
        id="upstox_token_refresh",
        name="Upstox OAuth2 token refresh",
        misfire_grace_time=600,
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started: ingestion runs %s, intervals=%s.", trigger_desc, settings.parsed_intervals())
    logger.info("Upstox token refresh scheduled at 04:00 and 16:00 IST daily.")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None


def get_scheduler_status() -> dict:
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_utc": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"running": True, "jobs": jobs}
