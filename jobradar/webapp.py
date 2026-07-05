"""
FastAPI dashboard + API — Phase 2 with username login, saved searches,
favorites, and application tracking.
"""

import asyncio
import json
import logging
import os
from typing import Optional, List

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Response, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import settings, COUNTRIES, CATEGORIES, REGIONS, _project_root, countries_by_region, get_keyword
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
)
from .scheduler import start_scheduler, stop_scheduler, run_scan_and_pipeline
from .telegram_bot import send_text

logger = logging.getLogger(__name__)

BASE_DIR = _project_root()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="JobRadar Global", version="2.1.0")

_static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


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
        secure=False,  # set True if behind HTTPS in production (Railway terminates TLS, so False is fine)
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    init_db()
    user = await get_current_user(request)
    stats = count_jobs()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "regions": REGIONS,
            "countries": COUNTRIES,
            "categories": CATEGORIES,
            "stats": stats,
            "user": user,
            "telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
            "webapp_public_url": settings.WEBAPP_PUBLIC_URL,
        },
    )


# ---------------------------------------------------------------------------
# Auth API — username-based login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str


@app.post("/api/auth/login")
async def api_login_username(req: LoginRequest, response: Response):
    """Log in with a Telegram username (no codes, no bots-in-the-loop).
    The user must have sent /start to the bot at least once so we know
    their telegram_user_id and chat_id."""
    username = (req.username or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Username is required")
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(
            404,
            f"No account found for '@{username}'. "
            f"Send /start to @{settings.TELEGRAM_BOT_USERNAME or 'our bot'} on Telegram first, "
            f"then come back and enter your username."
        )
    token = create_session_for_user(user["id"])
    _set_session_cookie(response, token)
    return {
        "ok": True,
        "username": user.get("username"),
        "first_name": user.get("first_name"),
    }


@app.post("/api/auth/logout")
async def api_logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
async def api_me(request: Request):
    user = await get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": user.get("id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "telegram_chat_id": user.get("telegram_chat_id"),
    }


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

    background_tasks.add_task(run_scan_and_pipeline, countries, categories, user_id)
    return {
        "status": "scan_started",
        "countries": countries or "all",
        "categories": categories or "all",
        "user": user.get("username") if user else None,
    }


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
    return {"status": "ok"}


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
    """List registered users (admin only — for now, anyone logged in can see)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"users": list_users()}


# ---------------------------------------------------------------------------
# Phase 2: Saved Searches
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
    return {"ok": True, "saved_search": ss}


@app.delete("/api/saved-searches/{search_id}")
async def api_delete_saved_search(search_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    ok = delete_saved_search(user["id"], search_id)
    if not ok:
        raise HTTPException(404, "Not found")
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
    background_tasks.add_task(run_scan_and_pipeline, countries, categories, user["id"])
    return {"ok": True, "status": "scan_started", "countries": countries, "categories": categories}


# ---------------------------------------------------------------------------
# Phase 2: Favorites
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
    return {"ok": True, "favorited": True}


@app.delete("/api/favorites/{job_id}")
async def api_remove_favorite(job_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    remove_favorite(user["id"], job_id)
    return {"ok": True, "favorited": False}


# ---------------------------------------------------------------------------
# Phase 2: Application Tracking
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
    return {"ok": True, "job_id": job_id, "status": req.status}


@app.delete("/api/applications/{job_id}")
async def api_remove_application(job_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    remove_application(user["id"], job_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Phase 2: User Settings
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
                              max_commute_km: Optional[int] = None):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    kwargs = {}
    if notify_telegram is not None: kwargs["notify_telegram"] = notify_telegram
    if notify_email is not None: kwargs["notify_email"] = notify_email
    if email is not None: kwargs["email"] = email
    if min_salary is not None: kwargs["min_salary"] = min_salary
    if max_commute_km is not None: kwargs["max_commute_km"] = max_commute_km
    update_user_settings(user["id"], **kwargs)
    return {"ok": True, "settings": get_user_settings(user["id"])}
