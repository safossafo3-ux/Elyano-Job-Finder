"""
MySQL database layer (migrated from SQLite).
Uses PyMySQL directly (no ORM), matching the exact function signatures the
rest of the app (pipeline.py, webapp.py, telegram_bot.py, scrapers/base.py)
already imports — no other file needs to change.

Requires: pip install pymysql
"""

import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

import pymysql
import pymysql.cursors

from .config import settings

logger = logging.getLogger(__name__)


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        url VARCHAR(512) UNIQUE NOT NULL,
        title VARCHAR(500),
        company VARCHAR(500),
        country_code VARCHAR(5) NOT NULL,
        country_name VARCHAR(100),
        category VARCHAR(50) NOT NULL,
        portal_name VARCHAR(100),
        phone_raw VARCHAR(50),
        phone_normalized VARCHAR(50),
        ad_summary TEXT,
        ad_summary_en TEXT,
        full_text LONGTEXT,
        screenshot_path VARCHAR(500),
        rejects_foreigners TINYINT DEFAULT 0,
        has_phone TINYINT DEFAULT 0,
        posted_at VARCHAR(50),
        discovered_at VARCHAR(50) NOT NULL,
        status VARCHAR(20) DEFAULT 'new',
        notified TINYINT DEFAULT 0,
        notified_at VARCHAR(50),
        INDEX idx_jobs_country (country_code),
        INDEX idx_jobs_category (category),
        INDEX idx_jobs_status (status),
        INDEX idx_jobs_notified (notified)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        started_at VARCHAR(50) NOT NULL,
        finished_at VARCHAR(50),
        country_code VARCHAR(5),
        category VARCHAR(50),
        portal_name VARCHAR(100),
        jobs_found INT DEFAULT 0,
        jobs_new INT DEFAULT 0,
        error TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


@contextmanager
def get_conn():
    conn = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create the database (if missing) and tables. Safe to call repeatedly."""
    # Connect without selecting a database first, in case it doesn't exist yet.
    bootstrap = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        charset="utf8mb4",
    )
    try:
        with bootstrap.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.MYSQL_DATABASE}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        bootstrap.commit()
    finally:
        bootstrap.close()

    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                cur.execute(stmt)


def upsert_job(job: Dict[str, Any]) -> bool:
    """Insert a new job or skip if it exists. Returns True if newly inserted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT IGNORE INTO jobs
                (url, title, company, country_code, country_name, category, portal_name,
                 phone_raw, phone_normalized, ad_summary, ad_summary_en, full_text,
                 screenshot_path, rejects_foreigners, has_phone, posted_at, discovered_at, notified)
                VALUES
                (%(url)s, %(title)s, %(company)s, %(country_code)s, %(country_name)s, %(category)s, %(portal_name)s,
                 %(phone_raw)s, %(phone_normalized)s, %(ad_summary)s, %(ad_summary_en)s, %(full_text)s,
                 %(screenshot_path)s, %(rejects_foreigners)s, %(has_phone)s, %(posted_at)s, %(discovered_at)s, 0)
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
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs SET
                    ad_summary_en = %s,
                    rejects_foreigners = %s,
                    phone_raw = %s,
                    phone_normalized = %s,
                    has_phone = %s
                WHERE id = %s
                """,
                (ad_summary_en, int(rejects_foreigners), phone_raw, phone_normalized,
                 int(has_phone), job_id),
            )


def mark_job_notified(job_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET notified=1, notified_at=%s WHERE id=%s",
                (datetime.utcnow().isoformat(), job_id),
            )


def get_job_by_url(url: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE url=%s", (url,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_unnotified_eligible_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE notified = 0
                  AND rejects_foreigners = 0
                ORDER BY discovered_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_unanalyzed_jobs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE ad_summary_en = '' OR ad_summary_en IS NULL
                ORDER BY discovered_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def list_jobs(country: Optional[str] = None, category: Optional[str] = None,
              status: Optional[str] = None, limit: int = 200, offset: int = 0
              ) -> List[Dict[str, Any]]:
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if country:
        query += " AND country_code = %s"
        params.append(country)
    if category:
        query += " AND category = %s"
        params.append(category)
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY discovered_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def set_job_status(job_id: int, status: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status=%s WHERE id=%s", (status, job_id))


def log_scan_start(country_code: str, category: str, portal_name: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_log (started_at, country_code, category, portal_name) "
                "VALUES (%s, %s, %s, %s)",
                (datetime.utcnow().isoformat(), country_code, category, portal_name),
            )
            return cur.lastrowid


def log_scan_finish(scan_id: int, jobs_found: int, jobs_new: int, error: str = ""):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scan_log SET finished_at=%s, jobs_found=%s, jobs_new=%s, error=%s WHERE id=%s",
                (datetime.utcnow().isoformat(), jobs_found, jobs_new, error, scan_id),
            )


def count_jobs() -> Dict[str, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM jobs")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE rejects_foreigners=0")
            eligible = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE notified=0 AND rejects_foreigners=0"
            )
            unnotified = cur.fetchone()["c"]
            return {"total": total, "eligible": eligible, "unnotified": unnotified}
