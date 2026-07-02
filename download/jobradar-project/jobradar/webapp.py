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
    country: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
):
    init_db()
    jobs = list_jobs(country=country, category=category, status=status, limit=300)
    stats = count_jobs()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "jobs": jobs,
            "countries": COUNTRIES,
            "categories": CATEGORIES,
            "filters": {"country": country or "", "category": category or "",
                        "status": status or ""},
            "stats": stats,
        },
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/scan/now")
async def api_scan_now(background_tasks: BackgroundTasks,
                       country: Optional[str] = None,
                       category: Optional[str] = None):
    """Trigger an on-demand scan as a background task."""
    countries = [country] if country else None
    categories = [category] if category else None
    background_tasks.add_task(run_scan_and_pipeline, countries, categories)
    return {"status": "scan_started", "country": country, "category": category}


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


@app.get("/screenshot/{job_id}")
async def job_screenshot(job_id: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    path = job.get("screenshot_path") or ""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Screenshot not available")
    return FileResponse(path, media_type="image/png")
