"""
Scheduler: runs scans twice a day at SCAN_CRON_HOURS (default 8 and 20 Cairo time)
and exposes an on-demand trigger.
"""

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .scrapers import scrape_all
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)


_scheduler: AsyncIOScheduler | None = None


async def run_scan_and_pipeline(countries=None, categories=None):
    """One full cycle: scrape → analyze → notify."""
    logger.info("Scheduled scan starting…")
    try:
        await scrape_all(countries=countries, categories=categories)
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
    try:
        await run_pipeline()
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
    logger.info("Scheduled scan complete.")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=settings.SCAN_CRON_TZ)
        hours = [h.strip() for h in settings.SCAN_CRON_HOURS.split(",")]
        for h in hours:
            _scheduler.add_job(
                run_scan_and_pipeline,
                CronTrigger(hour=int(h), minute=0),
                id=f"daily_scan_{h}",
                replace_existing=True,
            )
        logger.info(f"Scheduler configured for hours {hours} ({settings.SCAN_CRON_TZ})")
    return _scheduler


def start_scheduler():
    s = get_scheduler()
    if not s.running:
        s.start()
        logger.info("Scheduler started.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
