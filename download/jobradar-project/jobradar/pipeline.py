"""
Pipeline that:
  1. (Real-time) analyzes + notifies each new job the moment it's discovered
  2. (Fallback batch) analyzes + notifies any older jobs that slipped through
"""

import asyncio
import logging
from typing import Dict, Any

from .database import (
    get_unanalyzed_jobs, update_job_analysis, mark_job_notified,
    get_unnotified_eligible_jobs, get_job_by_url,
)
from .llm import analyze_ad, normalize_phone
from .telegram_bot import notify_job
from .config import COUNTRIES, settings

logger = logging.getLogger(__name__)


FOREIGNER_PHRASES = [
    "samo državljani", "samo drzavljani", "only citizens", "only nationals",
    "samо građani", "stranci ne", "ne zapošljava strance",
    "ne zaposliva tujce", "nu angaja străini", "nu accepta straini",
    "tik valstybės", "tik piliečiai", "tik piederīgie",
    "requires work permit", "must have citizenship", "local candidates only",
    "nur für staatsbürger", "nur staatsangehörige",
    "solo ciudadanos", "solo nacionales", "alleen voor burgers",
    "kun for borgere", "endast medborgare", "vain kansalaiset",
    "ainult kodanikud", "csak magyar", "numai cetateni",
]


def defensive_foreigners_check(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in FOREIGNER_PHRASES)


# ---------------------------------------------------------------------------
# Real-time per-job analysis + notification
# ---------------------------------------------------------------------------

async def analyze_and_notify_single(country_code: str, ad_text: str,
                                    job_url: str) -> Dict[str, Any]:
    """
    Analyze one freshly-scraped job and (if eligible) immediately notify Telegram.
    Returns a small status dict for logging.
    """
    if not ad_text:
        return {"status": "skip", "reason": "empty text"}

    result = await analyze_ad(country_code, ad_text)
    job = get_job_by_url(job_url)
    if not job:
        return {"status": "skip", "reason": "job not in DB"}

    if not result:
        update_job_analysis(
            job["id"],
            ad_summary_en="(analysis failed)",
            rejects_foreigners=False,
            phone_raw="",
            phone_normalized="",
            has_phone=False,
        )
        return {"status": "analysis_failed"}

    rejects = bool(result.get("rejects_foreigners", False)) or defensive_foreigners_check(ad_text)

    phone_raw = result.get("phone_raw", "")
    phone_norm = result.get("phone_normalized", "")
    if phone_raw and not phone_norm:
        country = COUNTRIES.get(country_code)
        if country:
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

    if result.get("is_relevant") is False:
        mark_job_notified(job["id"])
        return {"status": "irrelevant"}

    if rejects:
        return {"status": "rejected_foreigners"}

    # Real-time Telegram notification
    if settings.REALTIME_NOTIFY and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        job = get_job_by_url(job_url)  # refresh to get analyzed fields
        ok = await notify_job(job)
        if ok:
            mark_job_notified(job["id"])
            return {"status": "notified"}
        return {"status": "notify_failed"}
    else:
        return {"status": "analyzed_not_notified"}


# ---------------------------------------------------------------------------
# Batch fallback (used by scheduled runs to clean up stragglers)
# ---------------------------------------------------------------------------

async def analyze_pending_jobs(limit: int = 100) -> int:
    jobs = get_unanalyzed_jobs(limit=limit)
    if not jobs:
        return 0
    success = 0
    for job in jobs:
        cc = job["country_code"]
        ad_text = job.get("full_text") or job.get("title") or ""
        if not ad_text:
            continue
        res = await analyze_and_notify_single(cc, ad_text, job["url"])
        if res["status"] in {"notified", "analyzed_not_notified"}:
            success += 1
        await asyncio.sleep(0.3)
    return success


async def notify_pending_jobs(limit: int = 30) -> int:
    jobs = get_unnotified_eligible_jobs(limit=limit)
    if not jobs:
        return 0
    sent = 0
    for job in jobs:
        ok = await notify_job(job)
        if ok:
            mark_job_notified(job["id"])
            sent += 1
            await asyncio.sleep(1.0)
    return sent


async def run_pipeline():
    """Batch fallback: analyze → notify (cleans up any stragglers)."""
    logger.info("Pipeline (batch): analyzing pending jobs…")
    analyzed = await analyze_pending_jobs()
    logger.info(f"Pipeline (batch): analyzed {analyzed} jobs.")
    logger.info("Pipeline (batch): sending Telegram notifications…")
    sent = await notify_pending_jobs()
    logger.info(f"Pipeline (batch): sent {sent} notifications.")
    return {"analyzed": analyzed, "notified": sent}
