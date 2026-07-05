"""
Telegram bot for JobRadar.

LOGIN FLOW (Phase 3 — username + 6-digit code via bot):
  1. User opens dashboard → "Login with Telegram" → enters their Telegram username
  2. Dashboard calls POST /api/auth/request-code with {username}
  3. Backend looks up the user (must have previously sent /start to the bot),
     generates a 6-digit code, and SENDS IT TO THE USER'S TELEGRAM CHAT via the bot.
  4. User enters the code on the dashboard
  5. Dashboard calls POST /api/auth/verify-code with {code} → session created → logged in

Bot /start command:
  - Registers/refreshes the user's telegram_user_id + chat_id + username
  - Tells them to go back to the dashboard and log in
"""

import asyncio
import logging
from typing import Optional

import httpx

from .config import settings
from .database import (
    register_telegram_user, get_user_by_telegram_id,
    get_user_by_username, create_login_code,
)

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
        "driver": "🚚 Driver (Truck / Taxi / Uber / Van)",
        "warehouse": "📦 Warehouse (Picker / Packer / Forklift)",
        "hospitality": "🍳 Hospitality (Cook / Waiter / Kitchen)",
        "cleaning": "🧹 Cleaning (Hotel / Office / Domestic)",
        "caregiving": "🧑‍🤝‍🧑 Caregiver (Elderly / Childcare / Nurse)",
        "sales": "🛍️ Sales & Retail (Shop / Cashier)",
        "security": "👮 Security guard",
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
        # Register the user IMMEDIATELY. They can now log in by username + code.
        existing = get_user_by_telegram_id(tg_user_id)
        register_telegram_user(tg_user_id, chat_id, username, first_name)

        bot_link = settings.WEBAPP_PUBLIC_URL or "(the dashboard URL)"
        bot_username = settings.TELEGRAM_BOT_USERNAME or "our_bot"

        # Parse the start payload — format: /start login_<requested_username>
        # If the user came from the deep-link on the website, we may be able to
        # auto-send them a login code right now if their actual Telegram username
        # doesn't match what they typed on the website.
        payload = ""
        if " " in text:
            payload = text.split(" ", 1)[1].strip()

        auto_sent_code = False
        if payload.startswith("login_"):
            # They came from the website "Send me a code" button.
            requested_username = payload[len("login_"):].lstrip("@").strip()
            if requested_username and requested_username.lower() != (username or "").lower():
                await send_text(chat_id,
                    f"📝 Quick note: you typed <code>{requested_username}</code> on the dashboard, "
                    f"but your actual Telegram username is <code>@{username or '(none)'}</code>.\n\n"
                    f"Going forward, please use your real Telegram username on the dashboard."
                )
            # Auto-send the login code right now — they've effectively completed /start.
            try:
                result = await send_login_code_to_user(username or requested_username)
                if result.get("ok"):
                    await send_text(chat_id,
                        f"✅ <b>You're all set, {first_name or username or 'friend'}!</b>\n\n"
                        f"I just sent your 6-digit login code above. Enter it on the dashboard to sign in.\n"
                        f"🌐 Dashboard: {bot_link}"
                    )
                    auto_sent_code = True
                else:
                    err = result.get("error", "")
                    await send_text(chat_id,
                        f"⚠️ Couldn't auto-send your login code: {err}\n\n"
                        f"👉 Go back to the dashboard and click \"Send me a code\" again."
                    )
            except Exception as e:
                logger.error(f"Auto-send code failed: {e}")

        if not username and not auto_sent_code:
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

        if not auto_sent_code:
            display_name = f"@{username}" if username else (first_name or "friend")
            await send_text(chat_id,
                f"✅ <b>You're registered, {first_name or display_name}!</b>\n\n"
                f"Your Telegram username <code>@{username}</code> is now your JobRadar login.\n\n"
                f"👉 Go to the dashboard, enter <code>{username}</code>, "
                f"and I'll send a 6-digit login code right here.\n"
                f"🌐 Dashboard: {bot_link}\n\n"
                f"I'll also send you job alerts here automatically once a scan finds matches."
            )
        return {"status": "registered", "existing_user": bool(existing), "auto_sent_code": auto_sent_code}

    elif text == "/help":
        await send_text(chat_id,
            "<b>JobRadar Bot</b>\n\n"
            "Commands:\n"
            "/start — Register / refresh your account\n"
            "/help — Show this help\n\n"
            "<b>How to log in:</b>\n"
            "1. Send me /start (you just did!)\n"
            "2. Open the dashboard\n"
            "3. Enter your Telegram username\n"
            "4. I'll send you a 6-digit code here\n"
            "5. Enter the code on the dashboard to sign in\n\n"
            "Once logged in, you'll receive job alerts here automatically."
        )
        return {"status": "help_sent"}

    # Any other text: treat as a hint
    elif text:
        bot_link = settings.WEBAPP_PUBLIC_URL or "(the dashboard URL)"
        await send_text(chat_id,
            f"👋 Hi {first_name or 'there'}! I'm the JobRadar bot.\n\n"
            f"To get started, send me <code>/start</code> — that registers your account.\n"
            f"Then visit the dashboard: {bot_link}"
        )
        return {"status": "hint_sent"}

    return None


# ---------------------------------------------------------------------------
# Login code dispatch — called from the webapp when user requests a code
# ---------------------------------------------------------------------------

async def send_login_code_to_user(username: str) -> dict:
    """Generate a 6-digit login code and send it to the user's Telegram chat.

    Returns: {"ok": bool, "error": str?, "deep_link": str?}
    If the user hasn't sent /start to the bot yet, we return a deep_link
    that the website can show as a clickable button.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return {"ok": False,
                "error": "The Telegram bot is not configured on the server. "
                         "Please contact the administrator."}

    clean_username = (username or "").strip().lstrip("@").strip()
    if not clean_username:
        return {"ok": False, "error": "Please enter a valid Telegram username."}

    bot_username = settings.TELEGRAM_BOT_USERNAME
    if not bot_username:
        return {"ok": False,
                "error": "TELEGRAM_BOT_USERNAME is not configured on the server."}

    user = get_user_by_username(clean_username)

    if not user:
        # Build a simple deep link to open the bot in Telegram. We use the bare
        # t.me/<bot> URL (no ?start= param) because the start parameter was
        # causing 404s in some Telegram clients. The user just needs to press
        # "Start" in the bot chat manually.
        deep_link = f"https://t.me/{bot_username}"
        return {
            "ok": False,
            "needs_start": True,
            "deep_link": deep_link,
            "bot_username": bot_username,
            "error": (
                f"You haven't connected your Telegram to @{bot_username} yet. "
                f"Tap the button below to open the bot in Telegram, then press "
                f"Start. After that, come back here and click \"Send me a code\" again."
            ),
        }

    chat_id = int(user.get("telegram_chat_id") or 0)
    if not chat_id:
        deep_link = f"https://t.me/{bot_username}"
        return {
            "ok": False,
            "needs_start": True,
            "deep_link": deep_link,
            "bot_username": bot_username,
            "error": "Your account is missing a chat_id. Tap the button below to refresh.",
        }

    # Create the 6-digit code (also expires old ones)
    code = create_login_code(
        telegram_user_id=int(user["telegram_user_id"]),
        telegram_chat_id=chat_id,
        username=user.get("username") or "",
        first_name=user.get("first_name") or "",
    )

    display = f"@{user.get('username')}" if user.get("username") else (user.get("first_name") or "there")
    msg = (
        f"🔐 <b>JobRadar Login Code</b>\n\n"
        f"Hi {display}, here is your one-time login code:\n\n"
        f"<code>{code}</code>\n\n"
        f"⏱️ It expires in 10 minutes.\n"
        f"Enter it on the dashboard to sign in.\n\n"
        f"If you didn't request this code, just ignore this message."
    )
    sent = await send_text(chat_id, msg)
    if not sent:
        return {"ok": False, "error": "Failed to send code via Telegram. Try again in a moment."}
    return {"ok": True}


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
