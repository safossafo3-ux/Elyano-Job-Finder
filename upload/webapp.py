"""
FastAPI dashboard + API.
Routes:
  GET  /                  Dashboard (job list, filters, stats, scan trigger button)
  POST /api/scan/now      Trigger on-demand scan (background task)
  GET  /api/jobs          JSON list of jobs
  GET  /api/stats         JSON stats
  GET  /api/job/{id}      Job detail
  POST /api/job/{id}/status  Update job status (saved/applied/rejected)
"""

import asyncio
import logging
from typing import Optional

import os
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings, COUNTRIES, CATEGORIES, _project_root
from .database import (
    init_db, list_jobs, get_job, set_job_status, count_jobs,
)
from .scheduler import start_scheduler, stop_scheduler, run_scan_and_pipeline

logger = logging.getLogger(__name__)

BASE_DIR = _project_root()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="JobRadar", version="0.1.0")

# Mount static dir (for any CSS/JS later)
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
# Dashboard HTML
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    country: Optional[str] = None,    # comma-separated, e.g. "RS,BG,RO"
    category: Optional[str] = None,   # comma-separated
    status: Optional[str] = None,
):
    init_db()
    # Parse comma-separated country filter
    country_codes = None
    if country:
        country_codes = [c.strip().upper() for c in country.split(",") if c.strip()]
        country_codes = [c for c in country_codes if c in COUNTRIES] or None

    # Parse comma-separated category filter
    category_keys = None
    if category:
        category_keys = [c.strip().lower() for c in category.split(",") if c.strip()]
        category_keys = [c for c in category_keys if c in CATEGORIES] or None

    # Pull jobs from DB (we filter the combined set; for performance we just query all
    # and filter in Python — fine for a few hundred jobs)
    all_jobs = list_jobs(limit=1000)
    jobs = [j for j in all_jobs
            if (not country_codes or j["country_code"] in country_codes)
            and (not category_keys or j["category"] in category_keys)
            and (not status or j["status"] == status)][:300]

    stats = count_jobs()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "jobs": jobs,
            "countries": COUNTRIES,
            "categories": CATEGORIES,
            "filters": {
                "country": country or "",
                "country_list": country_codes or [],
                "category": category or "",
                "category_list": category_keys or [],
                "status": status or "",
            },
            "stats": stats,
        },
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/scan/now")
async def api_scan_now(background_tasks: BackgroundTasks,
                       country: Optional[str] = None,    # comma-separated
                       category: Optional[str] = None):   # comma-separated
    """Trigger an on-demand scan as a background task.
    country and category accept comma-separated lists, e.g. "RS,BG,RO".
    Empty means scan all."""
    countries = None
    if country:
        countries = [c.strip().upper() for c in country.split(",") if c.strip()]
        countries = [c for c in countries if c in COUNTRIES] or None

    categories = None
    if category:
        categories = [c.strip().lower() for c in category.split(",") if c.strip()]
        categories = [c for c in categories if c in CATEGORIES] or None

    background_tasks.add_task(run_scan_and_pipeline, countries, categories)
    return {
        "status": "scan_started",
        "countries": countries or "all",
        "categories": categories or "all",
    }


@app.get("/api/jobs")
async def api_jobs(country: Optional[str] = None, category: Optional[str] = None,
                   status: Optional[str] = None, limit: int = 200):
    return {"jobs": list_jobs(country=country, category=category, status=status, limit=limit)}


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


@app.get("/api/diagnostics")
async def api_diagnostics():
    """Show config status (masked) so you can see what's loaded and what's missing."""
    def mask(v: str) -> str:
        if not v:
            return "(empty)"
        if len(v) <= 8:
            return v[:2] + "***"
        return v[:4] + "..." + v[-4:]

    return {
        "telegram_bot_token": mask(settings.TELEGRAM_BOT_TOKEN),
        "telegram_chat_id":   mask(settings.TELEGRAM_CHAT_ID),
        "gemini_api_key":     mask(settings.GEMINI_API_KEY),
        "gemini_model":       settings.GEMINI_MODEL,
        "database_path":      settings.DATABASE_PATH,
        "scan_cron_hours":    settings.SCAN_CRON_HOURS,
        "scan_cron_tz":       settings.SCAN_CRON_TZ,
        "headless":           settings.HEADLESS,
        "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        "gemini_configured":   bool(settings.GEMINI_API_KEY),
        "ready":               bool(
            settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID and settings.GEMINI_API_KEY
        ),
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
