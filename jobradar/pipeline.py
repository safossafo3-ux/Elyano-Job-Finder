"""
Pipeline — multi-user edition.
  - Real-time analyze + notify per-user when a job is discovered
  - Batch fallback for stragglers
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from .database import (
    get_job_by_url, update_job_analysis, mark_job_notified_for_user,
    get_unnotified_jobs_for_user, list_users,
)
from .llm import analyze_ad, normalize_phone
from .telegram_bot import notify_job, send_text
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
    "nur inländische", "ustaalizao", "réservé",
    "egyptians only", "saudi nationals only", "qatari nationals",
    "uae nationals only", "gcc nationals",
]


def defensive_foreigners_check(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in FOREIGNER_PHRASES)


async def analyze_and_notify_single(
    country_code: str,
    ad_text: str,
    job_url: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Analyze a job, then notify all users (or specific user) who should see it."""
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
        # DON'T return here — fall through to notification. Previously, when
        # the LLM was down or rate-limited, jobs were saved to the DB but
        # NEVER notified to the user. Now we notify even without analysis,
        # using the raw ad text as the caption. The user can decide if the
        # job is relevant — better to over-notify than to silently drop.
        logger.info(f"LLM analysis failed for {job_url[:60]} — notifying anyway with raw ad text")
        # Skip the normal analysis path and go straight to notification
        rejects = False
        # Refresh job from DB to get the updated analysis fields
        job = get_job_by_url(job_url)
    else:
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
            # RELAXED: only drop jobs the LLM is CONFIDENT are irrelevant (banners,
            # CVs, completely unrelated content). Previously this filter was too
            # aggressive — the LLM was marking legitimate jobs as irrelevant
            # because they contained the word "manager" or didn't perfectly match
            # the courier/factory/driver categories. That caused ALL notifications
            # to be silently dropped, so users never received any jobs.
            # Now we only drop if the ad text is very short (likely a banner/nav
            # element) OR the LLM also returned an empty summary.
            summary = (result.get("summary_en") or "").strip()
            if len(ad_text) < 60 or not summary:
                return {"status": "irrelevant"}
            # Otherwise keep the job — it's better to over-notify than to silently
            # drop everything.
            logger.info(f"Keeping job despite is_relevant=False (has summary + ad_text > 60 chars): {job_url[:60]}")

    if rejects:
        return {"status": "rejected_foreigners"}

    # Notify the triggering user (if any) + admin
    notified_count = 0
    if settings.REALTIME_NOTIFY:
        # Refresh job from DB to get analyzed fields
        job = get_job_by_url(job_url)

        targets = []
        if user_id:
            # Notify the user who triggered the scan
            for u in list_users():
                if u["id"] == user_id:
                    targets.append(u)
                    break
        else:
            # Scheduled scan — notify all users
            targets = list_users()

        # Also notify the admin fallback chat
        admin_chat = settings.TELEGRAM_CHAT_ID
        if admin_chat:
            # Avoid double-sending if the admin is already in targets with the same chat_id
            already_in_targets = any(
                u.get("telegram_chat_id") and str(u["telegram_chat_id"]) == str(admin_chat)
                for u in targets
            )
            if not already_in_targets:
                ok = await notify_job(job, admin_chat)
                if ok:
                    notified_count += 1

        for u in targets:
            # Skip users with no telegram_chat_id (email/password-only users
            # can't receive Telegram notifications — they should set up
            # email notifications in settings instead, or link their Telegram).
            chat_id = u.get("telegram_chat_id")
            if not chat_id:
                logger.debug(f"Skipping notification for user {u.get('id')} — no telegram_chat_id (email-only user)")
                continue
            ok = await notify_job(job, chat_id)
            if ok:
                mark_job_notified_for_user(u["id"], job["id"])
                notified_count += 1
            await asyncio.sleep(0.8)  # Telegram rate limit safety

    return {"status": "notified", "count": notified_count} if notified_count else {"status": "analyzed_not_notified"}


async def notify_pending_for_user(user_id: int, limit: int = 30) -> int:
    """Send pending notifications to a specific user."""
    from .database import get_unnotified_jobs_for_user
    jobs = get_unnotified_jobs_for_user(user_id, limit=limit)
    if not jobs:
        return 0
    sent = 0
    for job in jobs:
        ok = await notify_job(job, user_chat_id_override=None)
        if ok:
            mark_job_notified_for_user(user_id, job["id"])
            sent += 1
            await asyncio.sleep(1.0)
    return sent


async def run_pipeline():
    """Batch fallback: notify all users of pending jobs."""
    sent_total = 0
    for user in list_users():
        sent_total += await notify_pending_for_user(user["id"])
    logger.info(f"Pipeline (batch): sent {sent_total} notifications.")
    return {"notified": sent_total}
