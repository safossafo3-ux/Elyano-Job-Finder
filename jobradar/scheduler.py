"""Scheduler — Phase 3.

Runs:
  - Daily scan at hours specified by SCAN_CRON_HOURS (default 8,20)
  - Daily scheduled-search digests at 09:00 (for saved searches with frequency='daily')
  - Weekly scheduled-search digests on Mondays at 09:00 (for frequency='weekly')
  - Telegram bot polling (asyncio task)
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings, COUNTRIES, CATEGORIES
from .scrapers import scrape_all
from .pipeline import run_pipeline
from .telegram_bot import poll_updates, send_text
from .database import (
    list_scheduled_searches, touch_saved_search_notified, log_digest,
    list_jobs, get_user_settings, list_users,
)
from .email_notify import send_email, build_job_alert_html

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


async def run_scheduled_searches(frequency: str = "daily"):
    """For every saved search with the given schedule frequency, find new matching jobs
    and notify the user (via Telegram and/or email, based on their settings)."""
    logger.info(f"Running scheduled-search digests (frequency={frequency})")
    searches = list_scheduled_searches(frequency)
    if not searches:
        logger.info(f"No saved searches with frequency={frequency}")
        return

    # Cutoff: 24h for daily, 7d for weekly
    cutoff_dt = datetime.utcnow() - (timedelta(days=1) if frequency == "daily" else timedelta(days=7))

    for s in searches:
        try:
            user_id = s["user_id"]
            chat_id = s.get("telegram_chat_id")
            user_settings = get_user_settings(user_id)

            # Find jobs discovered after the cutoff that match the saved search
            country_codes = s["countries"] or None
            category_keys = s["categories"] or None
            keywords = (s.get("keywords") or "").strip().lower()

            matching = list_jobs(country_codes=country_codes, categories=category_keys,
                                 limit=500)
            # Filter by cutoff
            new_jobs = []
            for j in matching:
                try:
                    discovered = datetime.fromisoformat(j["discovered_at"])
                except Exception:
                    continue
                if discovered < cutoff_dt:
                    continue
                if keywords and keywords not in (j.get("title", "") + " " +
                                                  j.get("ad_summary_en", "")).lower():
                    continue
                new_jobs.append(j)

            if not new_jobs:
                logger.info(f"Saved search #{s['id']} ('{s['name']}'): no new jobs since cutoff")
                touch_saved_search_notified(s["id"])
                continue

            # Compose messages
            count = len(new_jobs)
            job_list_text = "\n".join(
                f"• {j.get('title','(untitled)')} — {j.get('company','?')} ({j.get('country_name','?')})"
                for j in new_jobs[:10]
            )
            extra_text = f"\n… +{count-10} more" if count > 10 else ""
            tg_msg = (
                f"🔔 <b>{s['name']}</b> — {count} new job(s)\n\n"
                f"{job_list_text}{extra_text}\n\n"
                f"Open the dashboard to see details."
            )

            # Send Telegram if enabled
            if user_settings.get("notify_telegram") and chat_id:
                await send_text(chat_id, tg_msg)
                log_digest(user_id, s["id"], count, "telegram")

            # Send email if enabled and address present
            if user_settings.get("notify_email") and user_settings.get("email"):
                html = build_job_alert_html(new_jobs, saved_search_name=s["name"])
                send_email(
                    to_email=user_settings["email"],
                    subject=f"JobRadar — {count} new job(s) for '{s['name']}'",
                    html_body=html,
                    text_body=tg_msg,
                    user_id=user_id,
                )
                log_digest(user_id, s["id"], count, "email")

            touch_saved_search_notified(s["id"])
            logger.info(f"Saved search #{s['id']} ('{s['name']}'): notified {count} new jobs")
        except Exception as e:
            logger.error(f"Error processing scheduled search #{s['id']}: {e}")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=settings.SCAN_CRON_TZ)
        hours = [h.strip() for h in settings.SCAN_CRON_HOURS.split(",")]
        for h in hours:
            try:
                _scheduler.add_job(
                    run_scan_and_pipeline,
                    CronTrigger(hour=int(h), minute=0),
                    id=f"daily_scan_{h}",
                    replace_existing=True,
                )
            except ValueError:
                logger.warning(f"Invalid scan hour: {h}")
        # Phase 3: daily scheduled-search digests at 09:00
        _scheduler.add_job(
            run_scheduled_searches,
            CronTrigger(hour=9, minute=0),
            args=["daily"],
            id="daily_search_digest",
            replace_existing=True,
        )
        # Phase 3: weekly scheduled-search digests on Mondays at 09:00
        _scheduler.add_job(
            run_scheduled_searches,
            CronTrigger(day_of_week="mon", hour=9, minute=0),
            args=["weekly"],
            id="weekly_search_digest",
            replace_existing=True,
        )
        logger.info(f"Scheduler configured: scans at hours {hours} ({settings.SCAN_CRON_TZ}); "
                    f"digests daily@09:00 and weekly Mon@09:00")
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
