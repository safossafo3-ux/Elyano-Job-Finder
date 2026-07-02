"""
Pipeline that:
  1. Takes unanalyzed jobs from DB
  2. Runs Gemini analysis on each (translate, summarize, extract phone, detect "no foreigners")
  3. Stores results back in DB
  4. Sends Telegram notifications for eligible jobs (rejects_foreigners=False)
"""

import asyncio
import logging
from typing import List, Dict, Any

from .database import (
    get_unanalyzed_jobs, update_job_analysis, mark_job_notified,
    get_unnotified_eligible_jobs,
)
from .llm import analyze_ad, normalize_phone
from .telegram_bot import notify_job
from .config import COUNTRIES

logger = logging.getLogger(__name__)


# Phrases Gemini should look for; we also do a defensive regex check
FOREIGNER_PHRASES = [
    "samo državljani", "samo drzavljani", "only citizens", "only nationals",
    "samо građani", "stranci ne", "ne zapošljava strance", "ne zapošljava strance",
    "ne zaposljava tujce", "nu angaja străini", "nu accepta straini",
    "tik valstybės", "tik piliečiai", "tik piederīgie",
    "requires work permit", "must have citizenship", "local candidates only",
]


def defensive_foreigners_check(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in FOREIGNER_PHRASES)


async def analyze_pending_jobs(limit: int = 100) -> int:
    """
    Run Gemini analysis on unanalyzed jobs.
    Returns count of successfully analyzed.
    """
    jobs = get_unanalyzed_jobs(limit=limit)
    if not jobs:
        return 0

    success = 0
    for job in jobs:
        cc = job["country_code"]
        country = COUNTRIES.get(cc)
        if not country:
            continue

        ad_text = job.get("full_text") or job.get("title") or ""
        if not ad_text:
            continue

        result = await analyze_ad(cc, ad_text)
        if not result:
            # Mark as analyzed-but-empty so we don't retry forever
            update_job_analysis(
                job["id"],
                ad_summary_en="(analysis failed)",
                rejects_foreigners=False,
                phone_raw="",
                phone_normalized="",
                has_phone=False,
            )
            continue

        # Defensive: also run our regex check
        rejects = bool(result.get("rejects_foreigners", False)) or defensive_foreigners_check(ad_text)

        phone_raw = result.get("phone_raw", "")
        phone_norm = result.get("phone_normalized", "")
        if phone_raw and not phone_norm:
            phone_norm = normalize_phone(phone_raw, country.dial_code)
        has_phone = bool(phone_norm)

        update_job_analysis(
            job["id"],
            ad_summary_en=result.get("summary_en", "")[:300],
            rejects_foreigners=rejects,
            phone_raw=phone_raw,
            phone_normalized=phone_norm,
            has_phone=has_phone,
        )

        # Skip irrelevant jobs (e.g. the page was a banner, not a real ad)
        if result.get("is_relevant") is False:
            mark_job_notified(job["id"])  # mark notified so we don't ping about it
            continue

        success += 1
        # Be polite to Gemini
        await asyncio.sleep(0.3)

    return success


async def notify_pending_jobs(limit: int = 30) -> int:
    """Send Telegram messages for eligible, unnotified jobs."""
    jobs = get_unnotified_eligible_jobs(limit=limit)
    if not jobs:
        return 0

    sent = 0
    for job in jobs:
        ok = await notify_job(job)
        if ok:
            mark_job_notified(job["id"])
            sent += 1
            # Rate limit: Telegram allows ~30 msg/sec, but we'll be safe
            await asyncio.sleep(1.0)
    return sent


async def run_pipeline():
    """Full pipeline: analyze → notify."""
    logger.info("Pipeline: analyzing pending jobs…")
    analyzed = await analyze_pending_jobs()
    logger.info(f"Pipeline: analyzed {analyzed} jobs.")

    logger.info("Pipeline: sending Telegram notifications…")
    sent = await notify_pending_jobs()
    logger.info(f"Pipeline: sent {sent} notifications.")
    return {"analyzed": analyzed, "notified": sent}
