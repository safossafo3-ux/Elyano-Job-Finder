"""
FastAPI dashboard + API — global edition with multi-user auth.
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

from .config import settings, COUNTRIES, CATEGORIES, REGIONS, _project_root, countries_by_region, get_keyword
from .database import (
    init_db, list_jobs, get_job, set_job_status, count_jobs,
    consume_login_code, get_user_by_session, list_users,
    create_login_code, log_scan_start, log_scan_finish,
)
from .scheduler import start_scheduler, stop_scheduler, run_scan_and_pipeline
from .telegram_bot import send_text

logger = logging.getLogger(__name__)

BASE_DIR = _project_root()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="JobRadar Global", version="2.0.0")

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
        },
    )


# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------

@app.post("/api/auth/verify")
async def api_verify_code(code: str, response: Response):
    """Verify a login code from the dashboard. Sets session cookie."""
    user_info = consume_login_code(code.strip())
    if not user_info:
        raise HTTPException(401, "Invalid or expired code")
    response.set_cookie(
        key="session",
        value=user_info["session_token"],
        httponly=True,
        max_age=60*60*24*30,  # 30 days
        samesite="lax",
    )
    return {
        "ok": True,
        "username": user_info["username"],
        "first_name": user_info["first_name"],
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
