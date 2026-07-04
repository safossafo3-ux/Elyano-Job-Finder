"""Scheduler."""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .scrapers import scrape_all
from .pipeline import run_pipeline
from .telegram_bot import poll_updates

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_poller_task = None


async def run_scan_and_pipeline(countries=None, categories=None, user_id=None):
    logger.info("Scheduled scan starting…")
    try:
        await scrape_all(countries=countries, categories=categories, user_id=user_id)
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


async def _start_poller():
    """Start the Telegram bot polling task."""
    global _poller_task
    if _poller_task is None or _poller_task.done():
        _poller_task = asyncio.create_task(poll_updates())


def start_scheduler():
    s = get_scheduler()
    if not s.running:
        s.start()
        logger.info("Scheduler started.")
    # Also start Telegram polling
    asyncio.ensure_future(_start_poller())


def stop_scheduler():
    global _scheduler, _poller_task
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    if _poller_task:
        _poller_task.cancel()
