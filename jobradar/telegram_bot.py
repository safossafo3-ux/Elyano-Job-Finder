"""
Telegram bot for JobRadar.

PHASE 2 FLOW (simple username login):
  1. User opens dashboard → clicks "Login with Telegram"
  2. Modal says: "Send /start to @YourBot, then enter your Telegram username here"
  3. User sends /start to the bot
  4. Bot immediately registers them (stores telegram_user_id, chat_id, username)
     and replies: "You're registered! Now go back to the dashboard and enter
     your Telegram username to log in."
  5. User enters their username on the dashboard → instant login.

The old 6-digit code flow is gone. Username is the only credential.
"""

import asyncio
import logging
from typing import Optional

import httpx

from .config import settings
from .database import register_telegram_user, get_user_by_telegram_id

logger = logging.getLogger(__name__)


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


async def send_text(chat_id: int, text: str) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
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


async def send_photo(chat_id: int, photo_path: str, caption: str) -> bool:
    import os
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    if not photo_path or not os.path.exists(photo_path):
        return await send_text(chat_id, caption)
    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendPhoto")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(photo_path, "rb") as f:
                files = {"photo": (os.path.basename(photo_path), f, "image/png")}
                data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
                resp = await client.post(url, data=data, files=files)
                resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send_photo failed: {e}")
        return False


def build_caption(job: dict) -> str:
    """Format the Telegram caption for a job."""
    flags = {
        "RS":"🇷🇸","BA":"🇧🇦","ME":"🇲🇪","MK":"🇲🇰","AL":"🇦🇱",
        "BG":"🇧🇬","RO":"🇷🇴","GR":"🇬🇷","HR":"🇭🇷","SI":"🇸🇮",
        "AT":"🇦🇹","DE":"🇩🇪","CH":"🇨🇭","FR":"🇫🇷","BE":"🇧🇪",
        "NL":"🇳🇱","ES":"🇪🇸","PT":"🇵🇹","IT":"🇮🇹","SE":"🇸🇪",
        "NO":"🇳🇴","DK":"🇩🇰","FI":"🇫🇮","IS":"🇮🇸","IE":"🇮🇪",
        "GB":"🇬🇧","PL":"🇵🇱","CZ":"🇨🇿","SK":"🇸🇰","HU":"🇭🇺",
        "LV":"🇱🇻","LT":"🇱🇹","LU":"🇱🇺","MT":"🇲🇹","CY":"🇨🇾",
        "EE":"🇪🇪","RU":"🇷🇺","UA":"🇺🇦","BY":"🇧🇾","MD":"🇲🇩",
        "GE":"🇬🇪","AM":"🇦🇲","AE":"🇦🇪","SA":"🇸🇦","QA":"🇶🇦",
        "KW":"🇰🇼","BH":"🇧🇭","OM":"🇴🇲","IL":"🇮🇱","JO":"🇯🇴",
        "JP":"🇯🇵","KR":"🇰🇷","CN":"🇨🇳","IN":"🇮🇳","PK":"🇵🇰",
        "BD":"🇧🇩","SG":"🇸🇬","HK":"🇭🇰","MY":"🇲🇾","TH":"🇹🇭",
        "PH":"🇵🇭","ID":"🇮🇩","VN":"🇻🇳","TW":"🇹🇼","LK":"🇱🇰",
        "ZA":"🇿🇦","EG":"🇪🇬","NG":"🇳🇬","KE":"🇰🇪","MA":"🇲🇦",
        "TN":"🇹🇳","GH":"🇬🇭","ET":"🇪🇹","US":"🇺🇸","CA":"🇨🇦",
        "MX":"🇲🇽","BR":"🇧🇷","AR":"🇦🇷","CL":"🇨🇱","CO":"🇨🇴",
        "PE":"🇵🇪","CR":"🇨🇷","PA":"🇵🇦","UY":"🇺🇾","AU":"🇦🇺",
        "NZ":"🇳🇿",
    }
    flag = flags.get(job.get("country_code",""), "🌍")

    category_label = {
        "courier": "🛵 Courier (Glovo/Wolt/Bolt/Tazz)",
        "construction": "🏗️ Construction worker",
        "factory": "🏭 Factory worker",
    }.get(job.get("category",""), job.get("category",""))

    summary = job.get("ad_summary_en") or job.get("title") or "(no summary)"
    phone = job.get("phone_normalized") or ""
    company = job.get("company") or "(unknown employer)"
    portal = job.get("portal_name") or ""

    lines = [
        f"{flag} <b>{job.get('country_name','')}</b>  •  {category_label}",
        f"<b>Summary:</b> {summary}",
        f"<b>Employer:</b> {company}",
    ]
    if phone:
        lines.append(f"<b>Phone:</b> <code>{phone}</code>")
    else:
        lines.append(f"<b>Phone:</b> (not in ad — see screenshot)")
    if portal:
        lines.append(f"<b>Source:</b> {portal}")
    lines.append(f"<a href=\"{job.get('url','')}\">Open original ad</a>")
    return "\n".join(lines)


async def notify_job(job: dict, chat_id_override: Optional[int] = None) -> bool:
    """Send a single job alert. If chat_id_override is given, send to that chat;
    otherwise send to the admin chat from settings."""
    caption = build_caption(job)
    chat_id = chat_id_override or int(settings.TELEGRAM_CHAT_ID or 0)
    if not chat_id:
        logger.error("No chat_id to notify")
        return False
    return await send_photo(
        chat_id=chat_id,
        photo_path=job.get("screenshot_path",""),
        caption=caption,
    )


# ---------------------------------------------------------------------------
# Bot webhook/polling for /start
# ---------------------------------------------------------------------------

async def handle_update(update: dict) -> Optional[dict]:
    """Process a single Telegram update (webhook or polling)."""
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    chat_id = message.get("chat", {}).get("id")
    user = message.get("from") or {}
    tg_user_id = user.get("id")
    username = user.get("username") or ""
    first_name = user.get("first_name") or ""

    if not chat_id or not tg_user_id:
        return None

    if text.startswith("/start"):
        # Register the user IMMEDIATELY. They can now log in by username.
        existing = get_user_by_telegram_id(tg_user_id)
        register_telegram_user(tg_user_id, chat_id, username, first_name)

        bot_link = ""
        if settings.WEBAPP_PUBLIC_URL:
            bot_link = settings.WEBAPP_PUBLIC_URL
        else:
            bot_link = "(the dashboard URL)"

        if not username:
            await send_text(chat_id,
                f"👋 Welcome to JobRadar, {first_name or 'friend'}!\n\n"
                f"⚠️ <b>Important:</b> Your Telegram account doesn't have a public username set, "
                f"so you can't log in to the dashboard yet.\n\n"
                f"To fix this:\n"
                f"1. Open Telegram Settings → Username\n"
                f"2. Pick a username (e.g. <code>mustafa_ahmed</code>)\n"
                f"3. Send /start to me again\n\n"
                f"Then visit the dashboard and log in with your username."
            )
            return {"status": "no_username"}

        display_name = f"@{username}"
        await send_text(chat_id,
            f"✅ <b>You're registered, {first_name or display_name}!</b>\n\n"
            f"Your Telegram username <code>@{username}</code> is now your JobRadar login.\n\n"
            f"👉 Go to the dashboard and enter <code>{username}</code> to sign in.\n"
            f"🌐 Dashboard: {bot_link}\n\n"
            f"I'll send you job alerts here automatically once a scan finds matches."
        )
        return {"status": "registered", "existing_user": bool(existing)}

    elif text == "/help":
        await send_text(chat_id,
            "<b>JobRadar Bot</b>\n\n"
            "Commands:\n"
            "/start — Register / refresh your account\n"
            "/help — Show this help\n\n"
            "<b>How to log in:</b>\n"
            "1. Send me /start\n"
            "2. Open the dashboard\n"
            "3. Enter your Telegram username\n\n"
            "Once logged in, you'll receive job alerts here automatically."
        )
        return {"status": "help_sent"}

    return None


async def poll_updates():
    """Long-poll Telegram for updates. Run as a background task."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not set — polling disabled")
        return

    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="getUpdates")
    offset = 0
    logger.info("Telegram polling started")
    while True:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, data={"offset": offset, "timeout": 50})
                resp.raise_for_status()
                data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    await handle_update(update)
                except Exception as e:
                    logger.error(f"Error handling Telegram update: {e}")
        except asyncio.CancelledError:
            logger.info("Telegram polling cancelled")
            break
        except Exception as e:
            logger.error(f"Telegram poll error: {e}")
            await asyncio.sleep(5)
