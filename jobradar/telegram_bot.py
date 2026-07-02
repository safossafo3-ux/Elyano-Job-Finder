"""
Telegram notifier.
Sends a screenshot + caption (English summary + phone with country code) to your chat.

Uses the Bot API directly via httpx — no heavy python-telegram-bot dependency.
"""

import logging
import os
from typing import Optional, Dict, Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


async def send_photo(
    chat_id: str,
    photo_path: str,
    caption: str,
) -> bool:
    """
    Send a photo with caption to Telegram.
    Caption max 1024 chars (Telegram limit).
    """
    if not settings.TELEGRAM_BOT_TOKEN or not chat_id:
        logger.error("Telegram bot token or chat_id missing")
        return False

    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendPhoto")
    caption = caption[:1024]

    if not photo_path or not os.path.exists(photo_path):
        # Fall back to text message
        return await send_text(chat_id, caption)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(photo_path, "rb") as f:
                files = {"photo": (os.path.basename(photo_path), f, "image/png")}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                resp = await client.post(url, data=data, files=files)
                resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send_photo failed: {e}")
        return False


async def send_text(chat_id: str, text: str) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendMessage")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data={
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
            })
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send_text failed: {e}")
        return False


def build_caption(job: Dict[str, Any]) -> str:
    """Format the Telegram caption for a job."""
    country_flag = {
        "RS": "🇷🇸", "BA": "🇧🇦", "ME": "🇲🇪", "BG": "🇧🇬",
        "RO": "🇷🇴", "MK": "🇲🇰", "LV": "🇱🇻", "LT": "🇱🇹",
    }.get(job["country_code"], "🌍")

    category_label = {
        "courier": "Courier (Glovo/Wolt/Bolt/Tazz)",
        "construction": "Construction worker",
        "factory": "Factory worker",
    }.get(job["category"], job["category"])

    summary = job.get("ad_summary_en") or job.get("title") or "(no summary)"
    phone = job.get("phone_normalized") or ""
    company = job.get("company") or "(unknown employer)"
    portal = job.get("portal_name") or ""

    lines = [
        f"{country_flag} <b>{job['country_name']}</b>  •  {category_label}",
        f"<b>Summary:</b> {summary}",
        f"<b>Employer:</b> {company}",
    ]
    if phone:
        lines.append(f"<b>Phone:</b> <code>{phone}</code>")
    else:
        lines.append(f"<b>Phone:</b> (not in ad — see screenshot)")
    if portal:
        lines.append(f"<b>Source:</b> {portal}")
    lines.append(f"<a href=\"{job['url']}\">Open original ad</a>")

    return "\n".join(lines)


async def notify_job(job: Dict[str, Any]) -> bool:
    """Send a single job to your Telegram chat."""
    caption = build_caption(job)
    ok = await send_photo(
        chat_id=settings.TELEGRAM_CHAT_ID,
        photo_path=job.get("screenshot_path", ""),
        caption=caption,
    )
    return ok
