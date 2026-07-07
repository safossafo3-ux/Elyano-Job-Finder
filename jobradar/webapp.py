"""
FastAPI dashboard + API — Phase 3.

Login flow:
  POST /api/auth/request-code  {username}     → bot sends 6-digit code to user's Telegram
  POST /api/auth/verify-code   {code}         → validates, sets session cookie, returns user

Phase 3 features:
  - Saved searches (with scheduling: off/daily/weekly)
  - Favorites
  - Application tracking (kanban-style)
  - User settings (notify_telegram, notify_email, email, resume_path, email_digest)
  - Resume upload (PDF/DOC, stored locally)
  - Activity log
  - Email log
  - Statistics (per-user + global admin)
  - CSV export of jobs
  - Admin panel (admin usernames from env)
  - Rate limiting (per-user + per-IP)
"""

import asyncio
import csv
import io
import json
import logging
import os
import shutil
from typing import Optional, List

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Response, Cookie, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import settings, COUNTRIES, CATEGORIES, REGIONS, _project_root, countries_by_region, get_keyword, is_admin
from .database import (
    init_db, list_jobs, get_job, set_job_status, count_jobs,
    get_user_by_session, list_users,
    log_scan_start, log_scan_finish,
    # Phase 2
    get_user_by_username, create_session_for_user,
    create_saved_search, list_saved_searches, delete_saved_search, touch_saved_search,
    add_favorite, remove_favorite, list_favorites, is_favorite,
    upsert_application, remove_application, list_applications, VALID_APP_STATUSES,
    get_user_settings, update_user_settings,
    # Phase 3
    consume_login_code,
    log_activity, list_activity,
    list_email_log,
    user_stats, global_stats,
    set_saved_search_schedule, list_scheduled_searches, touch_saved_search_notified,
    VALID_SCHEDULE_FREQUENCIES,
    set_resume_path, list_scan_log,
    # Phase 4: live scan status
    count_jobs_since,
    # Multi-auth: email/password + Google OAuth
    get_user_by_email, create_email_user, hash_password, verify_password,
    # New unified registration framework (2026-07)
    create_registration_code, peek_registration_code, consume_registration_code,
    create_user_with_credentials,
)
from .scheduler import start_scheduler, stop_scheduler, run_scan_and_pipeline, run_scheduled_searches
from .telegram_bot import send_text, send_login_code_to_user, send_registration_code_to_user
from .rate_limit import RateLimitMiddleware
from .email_notify import send_email, build_job_alert_html

logger = logging.getLogger(__name__)

BASE_DIR = _project_root()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="JobRadar Global", version="3.1.0")

_static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Phase 3: rate limit middleware
app.add_middleware(RateLimitMiddleware)


# ---------------------------------------------------------------------------
# Phase 4: In-memory live scan status (not persisted — fine for dashboard UX)
# ---------------------------------------------------------------------------

# Tracks the currently-running scan (if any). Set when a scan starts,
# updated as it progresses, cleared when it finishes.
_LIVE_SCAN = {
    "running": False,
    "started_at": None,        # ISO timestamp
    "started_at_epoch_ms": 0,  # for "jobs discovered since" counting
    "finished_at": None,
    "countries": None,         # list of country codes or None for all
    "categories": None,        # list of category keys or None for all
    "triggered_by": None,      # username of triggering user (or "scheduler")
    "error": None,
}


def _mark_scan_started(countries=None, categories=None, user=None):
    from datetime import datetime
    _LIVE_SCAN.update({
        "running": True,
        "started_at": datetime.utcnow().isoformat(),
        "started_at_epoch_ms": int(datetime.utcnow().timestamp() * 1000),
        "finished_at": None,
        "countries": countries,
        "categories": categories,
        "triggered_by": (user.get("username") if isinstance(user, dict) else None) or "scheduler",
        "error": None,
    })


def _mark_scan_finished(error=None):
    from datetime import datetime
    _LIVE_SCAN.update({
        "running": False,
        "finished_at": datetime.utcnow().isoformat(),
        "error": error,
    })


@app.on_event("startup")
async def _startup():
    init_db()
    start_scheduler()


@app.on_event("shutdown")
async def _shutdown():
    stop_scheduler()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("session")
    if not token:
        return None
    return get_user_by_session(token)


def _set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        max_age=60*60*24*30,  # 30 days
        samesite="lax",
        secure=False,  # Railway terminates TLS — fine
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    init_db()
    user = await get_current_user(request)
    stats = count_jobs()
    # Admin check: support both telegram username and email
    user_identifier = ""
    if user:
        user_identifier = user.get("email") or user.get("username") or ""
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "regions": REGIONS,
            "countries": COUNTRIES,
            "categories": CATEGORIES,
            "stats": stats,
            "user": user,
            "is_admin": bool(user and is_admin(user_identifier)),
            "telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
            "webapp_public_url": settings.WEBAPP_PUBLIC_URL,
            "google_oauth_client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "google_oauth_enabled": bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET),
        },
    )


# ---------------------------------------------------------------------------
# Auth API — username + 6-digit code via Telegram bot
# ---------------------------------------------------------------------------

class RequestCodeRequest(BaseModel):
    username: str


class VerifyCodeRequest(BaseModel):
    code: str


@app.post("/api/auth/request-code")
async def api_request_code(req: RequestCodeRequest):
    """Step 1: user enters their Telegram username → bot sends a 6-digit code
    to that user's Telegram chat.

    If the user hasn't sent /start to the bot yet, returns a `needs_start: true`
    flag with a `deep_link` that the frontend can show as a button."""
    username = (req.username or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Username is required")

    # Validate that it looks like a Telegram username: 5–32 chars, [a-zA-Z0-9_]
    if not all(c.isalnum() or c == "_" for c in username):
        raise HTTPException(400, "Telegram usernames only contain letters, numbers, and underscores.")
    if len(username) < 5 or len(username) > 32:
        raise HTTPException(400, "Telegram usernames are 5–32 characters long.")

    result = await send_login_code_to_user(username)
    if not result.get("ok"):
        # If we have a deep link, return it as a structured response so the
        # frontend can render a "Open bot in Telegram" button.
        if result.get("needs_start"):
            return JSONResponse(
                status_code=200,  # 200, not 4xx — this is an expected flow
                content={
                    "ok": False,
                    "needs_start": True,
                    "deep_link": result["deep_link"],
                    "bot_username": result["bot_username"],
                    "message": result["error"],
                },
            )
        # Genuine error
        raise HTTPException(400, result.get("error") or "Failed to send code.")
    return {
        "ok": True,
        "message": f"Code sent to your Telegram chat (@{settings.TELEGRAM_BOT_USERNAME or 'our bot'}).",
        "username": username,
    }


@app.post("/api/auth/verify-code")
async def api_verify_code(req: VerifyCodeRequest, response: Response):
    """Step 2: user enters the code → validate and create a session."""
    code = (req.code or "").strip()
    if not code:
        raise HTTPException(400, "Code is required")
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "Code must be 6 digits.")
    user_info = consume_login_code(code)
    if not user_info:
        raise HTTPException(401, "Invalid or expired code. Please request a new one.")
    _set_session_cookie(response, user_info["session_token"])
    # Log the login
    log_activity(user_info["user_id"], "login", entity_type="auth",
                 details={"username": user_info.get("username")})
    return {
        "ok": True,
        "username": user_info.get("username"),
        "first_name": user_info.get("first_name"),
        "is_admin": is_admin(user_info.get("username") or ""),
    }


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    user = await get_current_user(request)
    if user:
        log_activity(user["id"], "logout")
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
async def api_me(request: Request):
    user = await get_current_user(request)
    if not user:
        return {"authenticated": False}
    user_identifier = user.get("email") or user.get("username") or ""
    return {
        "authenticated": True,
        "user_id": user.get("id"),
        "username": user.get("username"),
        "email": user.get("email"),
        "first_name": user.get("first_name"),
        "auth_provider": user.get("auth_provider", "telegram"),
        "telegram_chat_id": user.get("telegram_chat_id"),
        "is_admin": is_admin(user_identifier),
    }


# ---------------------------------------------------------------------------
# Multi-auth: email/password registration + login
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
async def api_register(req: RegisterRequest, response: Response):
    """Register a new user with email + password. Creates the account and
    immediately logs the user in (sets session cookie)."""
    email = (req.email or "").strip().lower()
    password = req.password or ""
    first_name = (req.first_name or "").strip()

    # Validate email
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Please enter a valid email address.")
    if len(email) > 254:
        raise HTTPException(400, "Email is too long.")

    # Validate password
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if len(password) > 128:
        raise HTTPException(400, "Password is too long (max 128 characters).")

    # Check if email is already registered
    if get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists. Try logging in instead.")

    # Create the user
    try:
        password_hash = hash_password(password)
        user = create_email_user(email, password_hash, first_name=first_name, auth_provider="email")
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logger.error(f"Registration failed for {email}: {e}")
        raise HTTPException(500, "Registration failed. Please try again.")

    # Create session
    token = create_session_for_user(user["id"])
    _set_session_cookie(response, token)
    log_activity(user["id"], "register", details={"email": email, "auth_provider": "email"})
    return {
        "ok": True,
        "user_id": user["id"],
        "email": user["email"],
        "first_name": user.get("first_name") or "",
        "auth_provider": "email",
        "message": "Account created! You're now signed in.",
    }


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    """Login with email OR username + password. Sets session cookie on success.

    The 'email' field accepts either:
    - An email address (e.g. 'mustafa@example.com')
    - A username (e.g. 'mustafa' or '@mustafa')

    This allows users who registered via Telegram (no email) to log in with
    their chosen username + password.
    """
    identifier = (req.email or "").strip()
    password = req.password or ""

    if not identifier or not password:
        raise HTTPException(400, "Username/email and password are required.")

    # Look up the user by email first, then by username
    user = None
    if "@" in identifier:
        # Treat as email
        user = get_user_by_email(identifier.lower())
    if not user:
        # Try as username (strip leading @, lowercase)
        clean_username = identifier.lstrip("@").lower()
        user = get_user_by_username(clean_username)

    if not user:
        raise HTTPException(401, "Invalid username/email or password.")
    if not user.get("password_hash"):
        # This account was created via Telegram OAuth or Google OAuth without
        # a password — they need to set one first via the registration flow.
        provider = user.get("auth_provider") or "telegram"
        raise HTTPException(
            401,
            f"This account doesn't have a password yet (created via {provider}). "
            "Please register again to set a username and password."
        )

    if not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Invalid username/email or password.")

    token = create_session_for_user(user["id"])
    _set_session_cookie(response, token)
    log_activity(user["id"], "login",
                 details={"auth_provider": user.get("auth_provider") or "email"})
    return {
        "ok": True,
        "user_id": user["id"],
        "username": user.get("username"),
        "email": user.get("email"),
        "first_name": user.get("first_name") or "",
        "auth_provider": user.get("auth_provider") or "email",
        "message": "Signed in successfully.",
    }


# ---------------------------------------------------------------------------
# New unified registration framework (2026-07)
# ---------------------------------------------------------------------------
# Two registration paths, both requiring a 6-digit verification code:
#   1. Email: user enters email → backend emails code → user enters code →
#      user picks username + password → account created.
#   2. Telegram: user enters @username → bot DMs code → user enters code →
#      user picks username + password → account created.
#
# After registration, the user logs in with their chosen username + password
# via /api/auth/login (above). The email/Telegram becomes a verified contact
# channel for notifications.

class EmailCodeRequest(BaseModel):
    email: str


class EmailVerifyRequest(BaseModel):
    email: str
    code: str


class TelegramVerifyRequest(BaseModel):
    code: str


class CreateAccountRequest(BaseModel):
    code: str              # The 6-digit code (serves as registration token)
    username: str
    password: str
    first_name: str = ""


@app.post("/api/auth/email/request-code")
async def api_email_request_code(req: EmailCodeRequest):
    """Step 1 (email path): user enters their email → backend sends a 6-digit
    code to that email. The code is valid for 10 minutes."""
    email = (req.email or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Please enter a valid email address.")
    if len(email) > 254:
        raise HTTPException(400, "Email is too long.")

    # If an account already exists with this email, refuse (user should log in instead)
    existing = get_user_by_email(email)
    if existing and existing.get("password_hash"):
        raise HTTPException(
            409,
            "An account with this email already exists. Try logging in instead."
        )

    # Issue the code
    code = create_registration_code(method="email", identifier=email)

    # Send the email
    from .email_notify import build_login_code_email
    html, text = build_login_code_email(code, username=email.split("@")[0])
    sent_ok = send_email(
        to_email=email,
        subject="JobRadar — Your registration code",
        html_body=html,
        text_body=text,
    )
    if not sent_ok:
        # SMTP not configured — return the code in the response so the user
        # can still complete registration in dev/test environments. In
        # production, SMTP should be configured and this branch won't fire.
        logger.warning(f"SMTP send failed for {email}; returning code in response (dev mode)")
        return {
            "ok": True,
            "message": "Email delivery is not configured on the server. "
                       "Use the code below to complete registration (dev mode).",
            "dev_code": code,
            "email": email,
        }

    return {
        "ok": True,
        "message": f"A 6-digit code was sent to {email}. It expires in 10 minutes.",
        "email": email,
    }


class TelegramCodeRequest(BaseModel):
    username: str


@app.post("/api/auth/telegram/request-code")
async def api_telegram_request_code(req: TelegramCodeRequest):
    """Step 1 (telegram path): user enters their Telegram username → bot DMs
    them a 6-digit registration code. The user must have already sent /start
    to the bot (otherwise we can't DM them)."""
    username = (req.username or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Telegram username is required.")
    if not all(c.isalnum() or c == "_" for c in username):
        raise HTTPException(400, "Telegram usernames only contain letters, numbers, and underscores.")
    if len(username) < 5 or len(username) > 32:
        raise HTTPException(400, "Telegram usernames are 5–32 characters long.")

    result = await send_registration_code_to_user(username)
    if not result.get("ok"):
        # If we have a deep link, return it as a structured response so the
        # frontend can render an "Open bot in Telegram" button.
        if result.get("needs_start"):
            return JSONResponse(
                status_code=200,  # 200, not 4xx — this is an expected flow
                content={
                    "ok": False,
                    "needs_start": True,
                    "deep_link": result["deep_link"],
                    "bot_username": result["bot_username"],
                    "message": result["error"],
                },
            )
        # If the user is already registered, return a friendly hint
        if result.get("already_registered"):
            return JSONResponse(
                status_code=200,
                content={
                    "ok": False,
                    "already_registered": True,
                    "message": result["error"],
                },
            )
        # Genuine error
        raise HTTPException(400, result.get("error") or "Failed to send code.")
    return {
        "ok": True,
        "message": f"Code sent to your Telegram chat (@{settings.TELEGRAM_BOT_USERNAME or 'our bot'}).",
        "username": username,
    }


@app.post("/api/auth/email/verify-code")
async def api_email_verify_code(req: EmailVerifyRequest):
    """Step 2 (email path): user enters the code → backend verifies it WITHOUT
    consuming it, so the user can still re-enter if they make a typo in the
    next step. Returns a registration_token (= the same code) to pass to
    /api/auth/create-account."""
    email = (req.email or "").strip().lower()
    code = (req.code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "Code must be 6 digits.")
    info = peek_registration_code(code)
    if not info or info["method"] != "email" or info["identifier"] != email:
        raise HTTPException(401, "Invalid or expired code. Please request a new one.")
    return {
        "ok": True,
        "verified": True,
        "method": "email",
        "email": email,
        "registration_token": code,  # Same as the code — pass to /create-account
        "message": "Email verified! Now choose a username and password to finish.",
    }


@app.post("/api/auth/telegram/verify-code")
async def api_telegram_verify_code(req: TelegramVerifyRequest, response: Response):
    """Step 2 (telegram path): user enters the code → backend verifies it
    WITHOUT consuming it. Returns a registration_token to pass to
    /api/auth/create-account.

    Note: This is DIFFERENT from the legacy /api/auth/verify-code endpoint,
    which auto-creates a session for existing Telegram users. This new endpoint
    is for NEW registrations and does NOT log the user in — they must finish
    setting their username + password via /api/auth/create-account first.
    """
    code = (req.code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "Code must be 6 digits.")
    info = peek_registration_code(code)
    if not info or info["method"] != "telegram":
        raise HTTPException(401, "Invalid or expired code. Please request a new one.")

    # Check if a user already exists with this Telegram user_id — if so,
    # they should log in instead of re-registering.
    existing_user = None
    if info.get("telegram_user_id"):
        from .database import get_user_by_telegram_id
        existing_user = get_user_by_telegram_id(info["telegram_user_id"])
    if existing_user and existing_user.get("password_hash"):
        # Already registered — log them in directly
        token = create_session_for_user(existing_user["id"])
        _set_session_cookie(response, token)
        log_activity(existing_user["id"], "login",
                     details={"auth_provider": "telegram"})
        return {
            "ok": True,
            "already_registered": True,
            "username": existing_user.get("username"),
            "first_name": existing_user.get("first_name") or "",
            "message": "Welcome back! You're already registered.",
        }

    return {
        "ok": True,
        "verified": True,
        "method": "telegram",
        "telegram_username": info["identifier"],
        "first_name": info.get("first_name") or "",
        "registration_token": code,
        "message": "Telegram verified! Now choose a username and password to finish.",
    }


@app.post("/api/auth/create-account")
async def api_create_account(req: CreateAccountRequest, response: Response):
    """Step 3 (final): user submits username + password + the registration
    token (which is the same as the 6-digit code). Backend atomically
    consumes the code and creates the user account with the chosen
    credentials, then logs the user in."""
    code = (req.code or "").strip()
    username = (req.username or "").strip().lstrip("@")
    password = req.password or ""
    first_name = (req.first_name or "").strip()

    # Validate code format
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "Invalid or missing registration code.")

    # Validate username
    if not username:
        raise HTTPException(400, "Username is required.")
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(username) > 32:
        raise HTTPException(400, "Username must be at most 32 characters.")
    if not all(c.isalnum() or c in "_." for c in username):
        raise HTTPException(400, "Username can only contain letters, numbers, underscores, and dots.")

    # Validate password
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if len(password) > 128:
        raise HTTPException(400, "Password is too long (max 128 characters).")

    # Consume the registration code (atomic — marks as used)
    info = consume_registration_code(code)
    if not info:
        raise HTTPException(
            401,
            "Invalid, expired, or already-used registration code. Please start again."
        )

    # Build the user record based on the registration method
    try:
        password_hash = hash_password(password)
        if info["method"] == "email":
            user = create_user_with_credentials(
                username=username,
                password_hash=password_hash,
                email=info["identifier"],
                first_name=first_name,
                auth_provider="email",
            )
        elif info["method"] == "telegram":
            user = create_user_with_credentials(
                username=username,
                password_hash=password_hash,
                first_name=first_name or info.get("first_name") or "",
                auth_provider="telegram",
                telegram_user_id=info.get("telegram_user_id"),
                telegram_chat_id=info.get("telegram_chat_id"),
            )
        else:
            raise HTTPException(400, f"Unknown registration method: {info['method']}")
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logger.error(f"Account creation failed: {e}")
        raise HTTPException(500, "Account creation failed. Please try again.")

    if not user:
        raise HTTPException(500, "Account creation failed — no user returned.")

    # Log in the new user
    token = create_session_for_user(user["id"])
    _set_session_cookie(response, token)
    log_activity(user["id"], "register",
                 details={"auth_provider": info["method"], "username": username})
    return {
        "ok": True,
        "user_id": user["id"],
        "username": user.get("username"),
        "email": user.get("email"),
        "first_name": user.get("first_name") or "",
        "auth_provider": info["method"],
        "message": "Account created! You're now signed in.",
    }


# ---------------------------------------------------------------------------
# Multi-auth: Google OAuth (legacy — kept for backward compat, but the new UI
# uses the email+code flow instead, which doesn't require GOOGLE_OAUTH_* env
# vars).
# ---------------------------------------------------------------------------

@app.get("/api/auth/google/start")
async def api_google_start(request: Request):
    """Redirect to Google's OAuth consent screen. Requires GOOGLE_OAUTH_CLIENT_ID."""
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(503, "Google OAuth is not configured on the server. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.")

    # Build the redirect URI: prefer env var, else derive from WEBAPP_PUBLIC_URL
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
    if not redirect_uri:
        if not settings.WEBAPP_PUBLIC_URL:
            raise HTTPException(500, "WEBAPP_PUBLIC_URL is not set — cannot build Google OAuth redirect URI.")
        redirect_uri = settings.WEBAPP_PUBLIC_URL.rstrip("/") + "/api/auth/google/callback"

    # Build the Google OAuth URL
    from urllib.parse import urlencode
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url=auth_url)


@app.get("/api/auth/google/callback")
async def api_google_callback(request: Request, response: Response, code: str = "", error: str = ""):
    """Handle the Google OAuth callback. Exchanges the code for user info,
    creates or looks up the user, sets session cookie, redirects to dashboard."""
    if error:
        return RedirectResponse(url=f"/?login_error={error}", status_code=302)
    if not code:
        return RedirectResponse(url="/?login_error=no_code", status_code=302)

    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        return RedirectResponse(url="/?login_error=google_not_configured", status_code=302)

    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
    if not redirect_uri:
        if not settings.WEBAPP_PUBLIC_URL:
            return RedirectResponse(url="/?login_error=no_public_url", status_code=302)
        redirect_uri = settings.WEBAPP_PUBLIC_URL.rstrip("/") + "/api/auth/google/callback"

    # Exchange the code for an access token
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse(url="/?login_error=no_access_token", status_code=302)

            # Fetch user info
            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
            google_user = user_resp.json()
    except Exception as e:
        logger.error(f"Google OAuth token exchange failed: {e}")
        return RedirectResponse(url="/?login_error=oauth_failed", status_code=302)

    email = (google_user.get("email") or "").strip().lower()
    first_name = google_user.get("given_name") or google_user.get("name") or ""
    if not email:
        return RedirectResponse(url="/?login_error=no_email", status_code=302)

    # Look up or create the user
    user = get_user_by_email(email)
    if not user:
        try:
            user = create_email_user(email, "", first_name=first_name, auth_provider="google")
            log_activity(user["id"], "register", details={"email": email, "auth_provider": "google"})
        except ValueError:
            return RedirectResponse(url="/?login_error=email_exists", status_code=302)
    else:
        log_activity(user["id"], "login", details={"auth_provider": "google"})

    # Set session cookie and redirect to dashboard
    token = create_session_for_user(user["id"])
    _set_session_cookie(response, token)
    return RedirectResponse(url="/?login_success=1", status_code=302)


# ---------------------------------------------------------------------------
# Data API
# ---------------------------------------------------------------------------

@app.get("/api/regions")
async def api_regions():
    """Returns all regions with their countries for the wizard."""
    out = []
    for r in REGIONS:
        countries = []
        for c in countries_by_region(r["code"]):
            countries.append({
                "code": c.code, "name": c.name,
                "dial_code": c.dial_code, "language": c.language,
            })
        out.append({**r, "countries": countries})
    return {"regions": out}


@app.get("/api/categories")
async def api_categories():
    return {
        "categories": [
            {"key": k, "label": v.english_label, "icon": v.icon}
            for k, v in CATEGORIES.items()
        ]
    }


@app.post("/api/scan/now")
async def api_scan_now(background_tasks: BackgroundTasks,
                       request: Request,
                       country: Optional[str] = None,
                       category: Optional[str] = None):
    """Trigger an on-demand scan. Notifications go to the calling user (if logged in)
    plus the admin chat."""
    user = await get_current_user(request)
    user_id = user["id"] if user else None

    countries = None
    if country:
        countries = [c.strip().upper() for c in country.split(",") if c.strip()]
        countries = [c for c in countries if c in COUNTRIES] or None

    categories = None
    if category:
        categories = [c.strip().lower() for c in category.split(",") if c.strip()]
        categories = [c for c in categories if c in CATEGORIES] or None

    if user:
        log_activity(user["id"], "scan_now",
                     details={"countries": countries, "categories": categories})

    # Mark scan as running in the in-memory state (so /api/scan/status can report it)
    _mark_scan_started(countries=countries, categories=categories, user=user)

    # Wrap the scan so we mark it finished when done
    async def _scan_wrapper():
        try:
            await run_scan_and_pipeline(countries, categories, user_id)
        except Exception as e:
            _mark_scan_finished(error=str(e))
            raise
        else:
            _mark_scan_finished()

    background_tasks.add_task(_scan_wrapper)
    return {
        "status": "scan_started",
        "countries": countries or "all",
        "categories": categories or "all",
        "user": user.get("username") if user else None,
    }


@app.get("/api/scan/status")
async def api_scan_status():
    """Live scan status. Returns:
      - running: bool
      - started_at: ISO timestamp (or null)
      - finished_at: ISO timestamp (or null)
      - countries / categories: list or null
      - triggered_by: username or "scheduler"
      - new_jobs_since_start: count of jobs discovered since this scan started
      - error: string or null
    """
    out = dict(_LIVE_SCAN)
    # Add live count of jobs discovered since the scan started
    if _LIVE_SCAN["running"] and _LIVE_SCAN["started_at"]:
        try:
            out["new_jobs_since_start"] = count_jobs_since(_LIVE_SCAN["started_at"])
        except Exception:
            out["new_jobs_since_start"] = 0
    else:
        out["new_jobs_since_start"] = 0
    return out


@app.get("/api/jobs")
async def api_jobs(country: Optional[str] = None,
                   category: Optional[str] = None,
                   status: Optional[str] = None,
                   limit: int = 200):
    country_codes = None
    if country:
        country_codes = [c.strip().upper() for c in country.split(",") if c.strip()]
    category_keys = None
    if category:
        category_keys = [c.strip().lower() for c in category.split(",") if c.strip()]
    return {"jobs": list_jobs(country_codes=country_codes,
                              categories=category_keys,
                              status=status, limit=limit)}


@app.get("/api/job/{job_id}")
async def api_job_detail(job_id: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/api/job/{job_id}/status")
async def api_set_status(job_id: int, status: str):
    if status not in {"new", "saved", "applied", "rejected"}:
        raise HTTPException(400, "Invalid status")
    set_job_status(job_id, status)
    return {"ok": True, "id": job_id, "status": status}


@app.get("/api/stats")
async def api_stats():
    return count_jobs()


@app.get("/health")
async def health():
    """Lightweight healthcheck — no DB access. Railway hits this every few seconds."""
    return {"status": "ok", "version": "3.1.0"}


@app.get("/api/diagnostics")
async def api_diagnostics():
    def mask(v):
        if not v: return "(empty)"
        if len(v) <= 8: return v[:2] + "***"
        return v[:4] + "..." + v[-4:]
    return {
        "telegram_bot_token": mask(settings.TELEGRAM_BOT_TOKEN),
        "telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
        "webapp_public_url": settings.WEBAPP_PUBLIC_URL,
        "telegram_chat_id": mask(settings.TELEGRAM_CHAT_ID),
        "gemini_api_key": mask(settings.GEMINI_API_KEY),
        "gemini_model": settings.GEMINI_MODEL,
        "database_path": settings.DATABASE_PATH,
        "scan_cron_hours": settings.SCAN_CRON_HOURS,
        "scan_cron_tz": settings.SCAN_CRON_TZ,
        "realtime_notify": settings.REALTIME_NOTIFY,
        "countries_configured": len(COUNTRIES),
        "regions_configured": len(REGIONS),
        "categories_configured": len(CATEGORIES),
        "users_registered": len(list_users()),
        "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "smtp_configured": bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD),
        "admin_usernames": settings.ADMIN_TELEGRAM_USERNAMES,
        "ready": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID and settings.GEMINI_API_KEY),
    }


@app.get("/screenshot/{job_id}")
async def job_screenshot(job_id: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    path = job.get("screenshot_path") or ""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Screenshot not available")
    return FileResponse(path, media_type="image/png")


@app.get("/api/users")
async def api_users(request: Request):
    """List registered users — admin only."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not is_admin(user.get("username") or ""):
        raise HTTPException(403, "Admin only")
    return {"users": list_users()}


# ---------------------------------------------------------------------------
# Saved Searches (Phase 2 + Phase 3 scheduling)
# ---------------------------------------------------------------------------

class SaveSearchRequest(BaseModel):
    name: str
    countries: List[str] = []
    categories: List[str] = []
    keywords: str = ""


@app.get("/api/saved-searches")
async def api_list_saved_searches(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"saved_searches": list_saved_searches(user["id"])}


@app.post("/api/saved-searches")
async def api_create_saved_search(req: SaveSearchRequest, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not req.name.strip():
        raise HTTPException(400, "Name is required")
    ss = create_saved_search(
        user["id"], req.name.strip(),
        [c.upper() for c in req.countries if c.upper() in COUNTRIES],
        [c.lower() for c in req.categories if c.lower() in CATEGORIES],
        req.keywords.strip(),
    )
    log_activity(user["id"], "saved_search_create", entity_type="saved_search",
                 entity_id=ss["id"], details={"name": req.name.strip()})
    return {"ok": True, "saved_search": ss}


@app.delete("/api/saved-searches/{search_id}")
async def api_delete_saved_search(search_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    ok = delete_saved_search(user["id"], search_id)
    if not ok:
        raise HTTPException(404, "Not found")
    log_activity(user["id"], "saved_search_delete", entity_type="saved_search", entity_id=search_id)
    return {"ok": True}


@app.post("/api/saved-searches/{search_id}/run")
async def api_run_saved_search(search_id: int, request: Request, background_tasks: BackgroundTasks):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    searches = list_saved_searches(user["id"])
    target = next((s for s in searches if s["id"] == search_id), None)
    if not target:
        raise HTTPException(404, "Saved search not found")
    countries = target["countries"] or None
    categories = target["categories"] or None
    touch_saved_search(search_id)
    log_activity(user["id"], "saved_search_run", entity_type="saved_search", entity_id=search_id)
    background_tasks.add_task(run_scan_and_pipeline, countries, categories, user["id"])
    return {"ok": True, "status": "scan_started", "countries": countries, "categories": categories}


# Phase 3: schedule a saved search (off/daily/weekly)
class ScheduleRequest(BaseModel):
    frequency: str  # off | daily | weekly


@app.put("/api/saved-searches/{search_id}/schedule")
async def api_set_schedule(search_id: int, req: ScheduleRequest, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if req.frequency not in VALID_SCHEDULE_FREQUENCIES:
        raise HTTPException(400, f"Invalid frequency. Must be one of: {VALID_SCHEDULE_FREQUENCIES}")
    ok = set_saved_search_schedule(user["id"], search_id, req.frequency)
    if not ok:
        raise HTTPException(404, "Saved search not found")
    log_activity(user["id"], "saved_search_schedule",
                 entity_type="saved_search", entity_id=search_id,
                 details={"frequency": req.frequency})
    return {"ok": True, "frequency": req.frequency}


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

@app.get("/api/favorites")
async def api_list_favorites(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"favorites": list_favorites(user["id"])}


@app.post("/api/favorites/{job_id}")
async def api_add_favorite(job_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not get_job(job_id):
        raise HTTPException(404, "Job not found")
    add_favorite(user["id"], job_id)
    log_activity(user["id"], "favorite_add", entity_type="job", entity_id=job_id)
    return {"ok": True, "favorited": True}


@app.delete("/api/favorites/{job_id}")
async def api_remove_favorite(job_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    remove_favorite(user["id"], job_id)
    log_activity(user["id"], "favorite_remove", entity_type="job", entity_id=job_id)
    return {"ok": True, "favorited": False}


# ---------------------------------------------------------------------------
# Application Tracking
# ---------------------------------------------------------------------------

class ApplicationRequest(BaseModel):
    status: str
    notes: str = ""


@app.get("/api/applications")
async def api_list_applications(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"applications": list_applications(user["id"])}


@app.post("/api/applications/{job_id}")
async def api_upsert_application(job_id: int, req: ApplicationRequest, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not get_job(job_id):
        raise HTTPException(404, "Job not found")
    if req.status not in VALID_APP_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {VALID_APP_STATUSES}")
    upsert_application(user["id"], job_id, req.status, req.notes.strip())
    log_activity(user["id"], f"application_{req.status}",
                 entity_type="job", entity_id=job_id)
    return {"ok": True, "job_id": job_id, "status": req.status}


@app.delete("/api/applications/{job_id}")
async def api_remove_application(job_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    remove_application(user["id"], job_id)
    log_activity(user["id"], "application_remove", entity_type="job", entity_id=job_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# User Settings + Resume upload
# ---------------------------------------------------------------------------

@app.get("/api/user/settings")
async def api_get_settings(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return get_user_settings(user["id"])


@app.put("/api/user/settings")
async def api_update_settings(request: Request,
                              notify_telegram: Optional[bool] = None,
                              notify_email: Optional[bool] = None,
                              email: Optional[str] = None,
                              min_salary: Optional[str] = None,
                              max_commute_km: Optional[int] = None,
                              email_digest: Optional[bool] = None):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    kwargs = {}
    if notify_telegram is not None: kwargs["notify_telegram"] = notify_telegram
    if notify_email is not None: kwargs["notify_email"] = notify_email
    if email is not None: kwargs["email"] = email
    if min_salary is not None: kwargs["min_salary"] = min_salary
    if max_commute_km is not None: kwargs["max_commute_km"] = max_commute_km
    # email_digest lives in user_settings — pass through
    if email_digest is not None: kwargs["email_digest"] = email_digest
    update_user_settings(user["id"], **kwargs)
    log_activity(user["id"], "settings_update", entity_type="settings", details=kwargs)
    return {"ok": True, "settings": get_user_settings(user["id"])}


@app.post("/api/user/resume")
async def api_upload_resume(request: Request, file: UploadFile = File(...)):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    # Validate file type
    allowed = {".pdf", ".doc", ".docx"}
    filename = file.filename or "resume.bin"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(400, f"Only PDF, DOC, DOCX allowed. Got: {ext or '(none)'}")
    # Read content with size cap
    content = await file.read()
    if len(content) > settings.MAX_RESUME_SIZE_BYTES:
        raise HTTPException(413, f"Resume too large. Max {settings.MAX_RESUME_SIZE_BYTES // (1024*1024)} MB.")
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    safe_name = f"user_{user['id']}_resume{ext}"
    target_path = os.path.join(settings.UPLOAD_DIR, safe_name)
    with open(target_path, "wb") as f:
        f.write(content)
    set_resume_path(user["id"], target_path)
    log_activity(user["id"], "resume_upload", entity_type="settings",
                 details={"filename": filename, "size_bytes": len(content)})
    return {"ok": True, "filename": safe_name, "size_bytes": len(content)}


@app.get("/api/user/resume")
async def api_download_resume(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    s = get_user_settings(user["id"])
    path = s.get("resume_path") or ""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "No resume uploaded yet.")
    return FileResponse(path, filename=os.path.basename(path))


# ---------------------------------------------------------------------------
# Phase 3: Activity log, stats, CSV export
# ---------------------------------------------------------------------------

@app.get("/api/activity")
async def api_activity(request: Request, limit: int = 50):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"activity": list_activity(user["id"], limit=min(limit, 200))}


@app.get("/api/email-log")
async def api_email_log(request: Request, limit: int = 30):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"emails": list_email_log(user["id"], limit=min(limit, 100))}


@app.get("/api/user/stats")
async def api_user_stats(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user_stats(user["id"])


@app.get("/api/jobs/export.csv")
async def api_export_jobs_csv(request: Request,
                              country: Optional[str] = None,
                              category: Optional[str] = None,
                              status: Optional[str] = None,
                              limit: int = 1000):
    """Export filtered jobs as a CSV file. Auth required."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    country_codes = None
    if country:
        country_codes = [c.strip().upper() for c in country.split(",") if c.strip()]
    category_keys = None
    if category:
        category_keys = [c.strip().lower() for c in category.split(",") if c.strip()]
    jobs = list_jobs(country_codes=country_codes, categories=category_keys,
                     status=status, limit=min(limit, 5000))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "title", "company", "country_code", "country_name",
        "category", "portal_name", "phone_normalized", "ad_summary_en",
        "rejects_foreigners", "has_phone", "posted_at", "discovered_at", "url",
    ])
    for j in jobs:
        writer.writerow([
            j.get("id"), j.get("title", ""), j.get("company", ""),
            j.get("country_code", ""), j.get("country_name", ""),
            j.get("category", ""), j.get("portal_name", ""),
            j.get("phone_normalized", ""), j.get("ad_summary_en", ""),
            j.get("rejects_foreigners", 0), j.get("has_phone", 0),
            j.get("posted_at", ""), j.get("discovered_at", ""),
            j.get("url", ""),
        ])
    log_activity(user["id"], "export_csv",
                 details={"country": country, "category": category, "rows": len(jobs)})
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobradar_jobs.csv"},
    )


# ---------------------------------------------------------------------------
# Phase 3: Admin endpoints
# ---------------------------------------------------------------------------

@app.get("/api/admin/stats")
async def api_admin_stats(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not is_admin(user.get("username") or ""):
        raise HTTPException(403, "Admin only")
    return global_stats()


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not is_admin(user.get("username") or ""):
        raise HTTPException(403, "Admin only")
    return {"users": list_users()}


@app.get("/api/admin/scan-log")
async def api_admin_scan_log(request: Request, limit: int = 20):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not is_admin(user.get("username") or ""):
        raise HTTPException(403, "Admin only")
    return {"scans": list_scan_log(limit=min(limit, 100))}


@app.post("/api/admin/scan-now")
async def api_admin_scan_now(background_tasks: BackgroundTasks, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not is_admin(user.get("username") or ""):
        raise HTTPException(403, "Admin only")
    log_activity(user["id"], "admin_scan_now")
    _mark_scan_started(countries=None, categories=None, user=user)
    async def _scan_wrapper():
        try:
            await run_scan_and_pipeline(None, None, user["id"])
        except Exception as e:
            _mark_scan_finished(error=str(e))
            raise
        else:
            _mark_scan_finished()
    background_tasks.add_task(_scan_wrapper)
    return {"ok": True, "status": "scan_started"}


# ---------------------------------------------------------------------------
# Phase 3: Test email (so users can verify their email config)
# ---------------------------------------------------------------------------

@app.post("/api/user/test-email")
async def api_test_email(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    s = get_user_settings(user["id"])
    to = s.get("email") or ""
    if not to:
        raise HTTPException(400, "Set your email in settings first.")
    html = (
        "<div style='background:#0a1628;padding:24px;font-family:Inter,Arial,sans-serif;color:#e2e8f0;'>"
        "<h2 style='color:#22d3ee;'>🛰️ JobRadar — Test Email</h2>"
        "<p>If you can read this, your email notifications are working correctly.</p>"
        "<p style='color:#9fb3c8;font-size:13px;'>JobRadar Global</p>"
        "</div>"
    )
    ok = send_email(to, "JobRadar — Test Email", html, user_id=user["id"])
    if not ok:
        raise HTTPException(500, "Failed to send email. Check server SMTP config.")
    return {"ok": True, "to": to}
