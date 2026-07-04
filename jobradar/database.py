"""
SQLite database layer — multi-user edition.
Adds users table, login_codes table, user_searches, user_job_notifications.
"""

import sqlite3
import json
import secrets
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER UNIQUE NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS login_codes (
    code TEXT PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_sessions (
    session_token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    countries TEXT NOT NULL,        -- JSON list
    categories TEXT NOT NULL,       -- JSON list
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

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
    status TEXT DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country_code);
CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS user_job_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    notified_at TEXT NOT NULL,
    UNIQUE(user_id, job_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    user_id INTEGER,
    countries TEXT,
    categories TEXT,
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


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def upsert_job(job: Dict[str, Any]) -> bool:
    """Insert a new job or skip if URL exists. Returns True if newly inserted."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
            (url, title, company, country_code, country_name, category, portal_name,
             phone_raw, phone_normalized, ad_summary, ad_summary_en, full_text,
             screenshot_path, rejects_foreigners, has_phone, posted_at, discovered_at, status)
            VALUES
            (:url, :title, :company, :country_code, :country_name, :category, :portal_name,
             :phone_raw, :phone_normalized, :ad_summary, :ad_summary_en, :full_text,
             :screenshot_path, :rejects_foreigners, :has_phone, :posted_at, :discovered_at, 'new')
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
            """UPDATE jobs SET ad_summary_en=?, rejects_foreigners=?, phone_raw=?,
               phone_normalized=?, has_phone=? WHERE id=?""",
            (ad_summary_en, int(rejects_foreigners), phone_raw, phone_normalized,
             int(has_phone), job_id),
        )


def get_job_by_url(url: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None


def mark_job_notified_for_user(user_id: int, job_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_job_notifications (user_id, job_id, notified_at) VALUES (?, ?, ?)",
            (user_id, job_id, datetime.utcnow().isoformat()),
        )


def get_unnotified_jobs_for_user(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Get jobs this user hasn't been notified about yet (and which are eligible)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.* FROM jobs j
            LEFT JOIN user_job_notifications ujn ON j.id = ujn.job_id AND ujn.user_id = ?
            WHERE ujn.id IS NULL
              AND j.rejects_foreigners = 0
              AND j.ad_summary_en != '(analysis failed)'
              AND j.ad_summary_en != ''
            ORDER BY j.discovered_at ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def list_jobs(country_codes: Optional[List[str]] = None,
              categories: Optional[List[str]] = None,
              status: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if country_codes:
        placeholders = ",".join("?" * len(country_codes))
        query += f" AND country_code IN ({placeholders})"
        params.extend(country_codes)
    if categories:
        placeholders = ",".join("?" * len(categories))
        query += f" AND category IN ({placeholders})"
        params.extend(categories)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY discovered_at DESC LIMIT ?"
    params.append(limit)
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


def count_jobs() -> Dict[str, int]:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        eligible = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE rejects_foreigners=0 AND ad_summary_en != '' AND ad_summary_en != '(analysis failed)'"
        ).fetchone()[0]
        return {"total": total, "eligible": eligible}


# ---------------------------------------------------------------------------
# Users & auth
# ---------------------------------------------------------------------------

def create_login_code(telegram_user_id: int, telegram_chat_id: int,
                      username: str = "", first_name: str = "") -> str:
    code = f"{secrets.randbelow(1000000):06d}"
    expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    with get_conn() as conn:
        # Invalidate previous unused codes for this user
        conn.execute(
            "UPDATE login_codes SET used=1 WHERE telegram_user_id=? AND used=0",
            (telegram_user_id,)
        )
        conn.execute(
            """INSERT INTO login_codes
               (code, telegram_user_id, telegram_chat_id, username, first_name, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (code, telegram_user_id, telegram_chat_id, username, first_name,
             datetime.utcnow().isoformat(), expires)
        )
    return code


def consume_login_code(code: str) -> Optional[Dict[str, Any]]:
    """Try to use a login code. Returns user info if valid, None otherwise."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM login_codes WHERE code=? AND used=0",
            (code,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Check expiry
        expires = datetime.fromisoformat(d["expires_at"])
        if datetime.utcnow() > expires:
            return None
        # Mark as used
        conn.execute("UPDATE login_codes SET used=1 WHERE code=?", (code,))
        # Create or update user
        now = datetime.utcnow().isoformat()
        cur = conn.execute(
            """INSERT INTO users (telegram_user_id, telegram_chat_id, username, first_name, created_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                 telegram_chat_id=excluded.telegram_chat_id,
                 username=excluded.username,
                 first_name=excluded.first_name,
                 last_login_at=excluded.last_login_at""",
            (d["telegram_user_id"], d["telegram_chat_id"], d["username"],
             d["first_name"], now, now)
        )
        user_id = cur.lastrowid
        if not user_id:
            # User already existed — fetch id
            u = conn.execute(
                "SELECT id FROM users WHERE telegram_user_id=?",
                (d["telegram_user_id"],)
            ).fetchone()
            user_id = u["id"] if u else None
        if not user_id:
            return None
        # Create session
        session_token = secrets.token_urlsafe(32)
        session_expires = (datetime.utcnow() + timedelta(days=30)).isoformat()
        conn.execute(
            """INSERT INTO user_sessions (session_token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (session_token, user_id, now, session_expires)
        )
        return {
            "user_id": user_id,
            "session_token": session_token,
            "telegram_user_id": d["telegram_user_id"],
            "telegram_chat_id": d["telegram_chat_id"],
            "username": d["username"],
            "first_name": d["first_name"],
        }


def get_user_by_session(session_token: str) -> Optional[Dict[str, Any]]:
    if not session_token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM user_sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.session_token=? AND s.expires_at > ?""",
            (session_token, datetime.utcnow().isoformat())
        ).fetchone()
        return dict(row) if row else None


def get_user_by_telegram_id(telegram_user_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id=?",
            (telegram_user_id,)
        ).fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Scan log
# ---------------------------------------------------------------------------

def log_scan_start(countries: List[str], categories: List[str],
                   user_id: Optional[int] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO scan_log
               (started_at, user_id, countries, categories)
               VALUES (?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), user_id,
             json.dumps(countries), json.dumps(categories))
        )
        return cur.lastrowid


def log_scan_finish(scan_id: int, jobs_found: int, jobs_new: int, error: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scan_log SET finished_at=?, jobs_found=?, jobs_new=?, error=? WHERE id=?",
            (datetime.utcnow().isoformat(), jobs_found, jobs_new, error, scan_id),
        )
