"""
SQLite database layer.
Uses sqlite3 directly (no ORM) for portability and zero-config.
"""

import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    company TEXT,
    country_code TEXT NOT NULL,
    country_name TEXT,
    category TEXT NOT NULL,
    portal_name TEXT,
    phone_raw TEXT,
    phone_normalized TEXT,
    ad_summary TEXT,
    ad_summary_en TEXT,
    full_text TEXT,
    screenshot_path TEXT,
    rejects_foreigners INTEGER DEFAULT 0,
    has_phone INTEGER DEFAULT 0,
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT DEFAULT 'new',
    notified INTEGER DEFAULT 0,
    notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country_code);
CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs(notified);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    country_code TEXT,
    category TEXT,
    portal_name TEXT,
    jobs_found INTEGER DEFAULT 0,
    jobs_new INTEGER DEFAULT 0,
    error TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_job(job: Dict[str, Any]) -> bool:
    """Insert a new job or update existing. Returns True if it was newly inserted."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
            (url, title, company, country_code, country_name, category, portal_name,
             phone_raw, phone_normalized, ad_summary, ad_summary_en, full_text,
             screenshot_path, rejects_foreigners, has_phone, posted_at, discovered_at, notified)
            VALUES
            (:url, :title, :company, :country_code, :country_name, :category, :portal_name,
             :phone_raw, :phone_normalized, :ad_summary, :ad_summary_en, :full_text,
             :screenshot_path, :rejects_foreigners, :has_phone, :posted_at, :discovered_at, 0)
            """,
            {
                "url": job["url"],
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "country_code": job["country_code"],
                "country_name": job.get("country_name", ""),
                "category": job["category"],
                "portal_name": job.get("portal_name", ""),
                "phone_raw": job.get("phone_raw", ""),
                "phone_normalized": job.get("phone_normalized", ""),
                "ad_summary": job.get("ad_summary", ""),
                "ad_summary_en": job.get("ad_summary_en", ""),
                "full_text": job.get("full_text", ""),
                "screenshot_path": job.get("screenshot_path", ""),
                "rejects_foreigners": int(job.get("rejects_foreigners", False)),
                "has_phone": int(job.get("has_phone", False)),
                "posted_at": job.get("posted_at", ""),
                "discovered_at": datetime.utcnow().isoformat(),
            },
        )
        return cur.rowcount > 0


def update_job_analysis(job_id: int, *, ad_summary_en: str, rejects_foreigners: bool,
                       phone_raw: str, phone_normalized: str, has_phone: bool):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs SET
                ad_summary_en = ?,
                rejects_foreigners = ?,
                phone_raw = ?,
                phone_normalized = ?,
                has_phone = ?
            WHERE id = ?
            """,
            (ad_summary_en, int(rejects_foreigners), phone_raw, phone_normalized,
             int(has_phone), job_id),
        )


def mark_job_notified(job_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET notified=1, notified_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), job_id),
        )


def get_unnotified_eligible_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified = 0
              AND rejects_foreigners = 0
            ORDER BY discovered_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_unanalyzed_jobs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE ad_summary_en = '' OR ad_summary_en IS NULL
            ORDER BY discovered_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_jobs(country: Optional[str] = None, category: Optional[str] = None,
              status: Optional[str] = None, limit: int = 200, offset: int = 0
              ) -> List[Dict[str, Any]]:
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if country:
        query += " AND country_code = ?"
        params.append(country)
    if category:
        query += " AND category = ?"
        params.append(category)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY discovered_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def set_job_status(job_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def log_scan_start(country_code: str, category: str, portal_name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scan_log (started_at, country_code, category, portal_name) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), country_code, category, portal_name),
        )
        return cur.lastrowid


def log_scan_finish(scan_id: int, jobs_found: int, jobs_new: int, error: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scan_log SET finished_at=?, jobs_found=?, jobs_new=?, error=? WHERE id=?",
            (datetime.utcnow().isoformat(), jobs_found, jobs_new, error, scan_id),
        )


def count_jobs() -> Dict[str, int]:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        eligible = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE rejects_foreigners=0"
        ).fetchone()[0]
        unnotified = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE notified=0 AND rejects_foreigners=0"
        ).fetchone()[0]
        return {"total": total, "eligible": eligible, "unnotified": unnotified}
