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

-- Phase 2 tables
CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    countries TEXT NOT NULL,        -- JSON list of country codes
    categories TEXT NOT NULL,       -- JSON list of category keys
    keywords TEXT,                  -- optional extra keywords
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, job_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',  -- applied | interview | offer | rejected
    applied_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(user_id, job_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    notify_telegram INTEGER DEFAULT 1,
    notify_email INTEGER DEFAULT 0,
    email TEXT,
    min_salary TEXT,
    max_commute_km INTEGER,
    resume_path TEXT,
    email_digest INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Phase 3 tables
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,           -- login | favorite_add | favorite_remove | application_* | saved_search_* | scan_* | settings_update
    entity_type TEXT,               -- job | saved_search | application | settings
    entity_id INTEGER,
    details TEXT,                   -- JSON blob
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);

CREATE TABLE IF NOT EXISTS email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    to_email TEXT NOT NULL,
    subject TEXT,
    body_preview TEXT,
    status TEXT NOT NULL,           -- sent | failed
    error TEXT,
    sent_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS job_alert_digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    saved_search_id INTEGER,
    new_jobs_count INTEGER DEFAULT 0,
    sent_at TEXT NOT NULL,
    channel TEXT NOT NULL,           -- telegram | email
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(saved_search_id) REFERENCES saved_searches(id)
);
"""

# Migrations for existing databases (idempotent — safe to run multiple times)
MIGRATIONS = [
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
    "CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id)",
    # Phase 3 — add new columns to existing tables (idempotent: wrapped in try/except)
    "ALTER TABLE user_settings ADD COLUMN resume_path TEXT",
    "ALTER TABLE user_settings ADD COLUMN email_digest INTEGER DEFAULT 0",
    "ALTER TABLE saved_searches ADD COLUMN schedule_frequency TEXT DEFAULT 'off'",  # off | daily | weekly
    "ALTER TABLE saved_searches ADD COLUMN last_notified_at TEXT",
]


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
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                # Likely "duplicate column name" — idempotent, ignore.
                pass


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


# ---------------------------------------------------------------------------
# Phase 2: username-based login + saved searches + favorites + applications
# ---------------------------------------------------------------------------

def register_telegram_user(telegram_user_id: int, telegram_chat_id: int,
                           username: str = "", first_name: str = "") -> Dict[str, Any]:
    """Called when user messages the bot. Creates/updates user record immediately
    so they can log in by username later — no code flow needed."""
    now = datetime.utcnow().isoformat()
    # Normalize username: lowercase, strip leading @
    if username:
        username = username.lstrip("@").lower()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (telegram_user_id, telegram_chat_id, username, first_name, created_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                 telegram_chat_id=excluded.telegram_chat_id,
                 username=COALESCE(NULLIF(excluded.username, ''), users.username),
                 first_name=COALESCE(NULLIF(excluded.first_name, ''), users.first_name)""",
            (telegram_user_id, telegram_chat_id, username or None, first_name or None, now, now)
        )
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id=?", (telegram_user_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Look up a user by their Telegram username (case-insensitive, no leading @)."""
    if not username:
        return None
    clean = username.strip().lstrip("@").lower()
    if not clean:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = ?", (clean,)
        ).fetchone()
        return dict(row) if row else None


def create_session_for_user(user_id: int) -> str:
    """Create a fresh session for an existing user. Returns the session token."""
    now = datetime.utcnow().isoformat()
    expires = (datetime.utcnow() + timedelta(days=30)).isoformat()
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login_at=? WHERE id=?", (now, user_id)
        )
        conn.execute(
            """INSERT INTO user_sessions (session_token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, now, expires)
        )
    return token


# Saved searches ----------------------------------------------------------

def create_saved_search(user_id: int, name: str, countries: List[str],
                        categories: List[str], keywords: str = "") -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO saved_searches (user_id, name, countries, categories, keywords, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, name, json.dumps(countries), json.dumps(categories),
             keywords or "", now)
        )
        sid = cur.lastrowid
        return {"id": sid, "user_id": user_id, "name": name,
                "countries": countries, "categories": categories,
                "keywords": keywords, "created_at": now, "last_run_at": None}


def list_saved_searches(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_searches WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["countries"] = json.loads(d.get("countries") or "[]")
            d["categories"] = json.loads(d.get("categories") or "[]")
            out.append(d)
        return out


def delete_saved_search(user_id: int, search_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM saved_searches WHERE id=? AND user_id=?",
            (search_id, user_id)
        )
        return cur.rowcount > 0


def touch_saved_search(search_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE saved_searches SET last_run_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), search_id)
        )


# Favorites ---------------------------------------------------------------

def add_favorite(user_id: int, job_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, job_id, created_at) VALUES (?, ?, ?)",
            (user_id, job_id, datetime.utcnow().isoformat())
        )


def remove_favorite(user_id: int, job_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id=? AND job_id=?",
            (user_id, job_id)
        )


def list_favorites(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.*, f.created_at AS favorited_at
               FROM favorites f
               JOIN jobs j ON f.job_id = j.id
               WHERE f.user_id=?
               ORDER BY f.created_at DESC""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def is_favorite(user_id: int, job_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND job_id=?",
            (user_id, job_id)
        ).fetchone()
        return bool(row)


# Applications ------------------------------------------------------------

VALID_APP_STATUSES = {"applied", "interview", "offer", "rejected"}


def upsert_application(user_id: int, job_id: int, status: str, notes: str = ""):
    if status not in VALID_APP_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO applications (user_id, job_id, status, applied_at, notes)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, job_id) DO UPDATE SET
                 status=excluded.status,
                 notes=COALESCE(NULLIF(excluded.notes, ''), applications.notes)""",
            (user_id, job_id, status, now, notes)
        )


def remove_application(user_id: int, job_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM applications WHERE user_id=? AND job_id=?",
            (user_id, job_id)
        )


def list_applications(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.*, a.status AS app_status, a.applied_at, a.notes AS app_notes
               FROM applications a
               JOIN jobs j ON a.job_id = j.id
               WHERE a.user_id=?
               ORDER BY a.applied_at DESC""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# User settings -----------------------------------------------------------

def get_user_settings(user_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return {
                "user_id": user_id,
                "notify_telegram": True,
                "notify_email": False,
                "email": "",
                "min_salary": "",
                "max_commute_km": None,
                "resume_path": "",
                "email_digest": False,
            }
        d = dict(row)
        d["notify_telegram"] = bool(d.get("notify_telegram"))
        d["notify_email"] = bool(d.get("notify_email"))
        d["email_digest"] = bool(d.get("email_digest"))
        d.setdefault("resume_path", "")
        return d


def update_user_settings(user_id: int, **kwargs):
    allowed = {"notify_telegram", "notify_email", "email", "min_salary",
               "max_commute_km", "resume_path", "email_digest"}
    # Filter & normalize
    cleaned = {}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k in ("notify_telegram", "notify_email", "email_digest"):
            v = 1 if v else 0
        if k == "max_commute_km" and v == "":
            v = None
        cleaned[k] = v
    if not cleaned:
        return
    with get_conn() as conn:
        # First, ensure the row exists with defaults (incl. Phase 3 cols)
        conn.execute(
            """INSERT OR IGNORE INTO user_settings
               (user_id, notify_telegram, notify_email, email, min_salary, max_commute_km, resume_path, email_digest)
               VALUES (?, 1, 0, '', '', NULL, NULL, 0)""",
            (user_id,)
        )
        # Then update only the fields provided
        assignments = ", ".join(f"{k}=?" for k in cleaned)
        values = list(cleaned.values()) + [user_id]
        conn.execute(
            f"UPDATE user_settings SET {assignments} WHERE user_id=?",
            values
        )


# User profile helpers ----------------------------------------------------

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Phase 3: Activity log, email log, statistics, scheduled searches
# ---------------------------------------------------------------------------

def log_activity(user_id: int, action: str, entity_type: str = "",
                 entity_id: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
    """Record a user action. Non-fatal: errors are swallowed."""
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO activity_log (user_id, action, entity_type, entity_id, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, action, entity_type, entity_id or None,
                 json.dumps(details) if details else None,
                 datetime.utcnow().isoformat())
            )
    except Exception:
        pass


def list_activity(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def log_email(user_id: Optional[int], to_email: str, subject: str,
              body_preview: str, status: str, error: str = ""):
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO email_log (user_id, to_email, subject, body_preview, status, error, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, to_email, subject, body_preview[:300], status, error,
                 datetime.utcnow().isoformat())
            )
    except Exception:
        pass


def list_email_log(user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM email_log WHERE user_id=? ORDER BY sent_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# Statistics --------------------------------------------------------------

def user_stats(user_id: int) -> Dict[str, Any]:
    """Per-user dashboard stats."""
    with get_conn() as conn:
        favorites = conn.execute(
            "SELECT COUNT(*) FROM favorites WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        applications = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        saved_searches = conn.execute(
            "SELECT COUNT(*) FROM saved_searches WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        # Application breakdown by status
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications WHERE user_id=? GROUP BY status",
            (user_id,)
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in status_rows}
        # Last login
        u = conn.execute("SELECT last_login_at, created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return {
            "favorites": favorites,
            "applications": applications,
            "saved_searches": saved_searches,
            "applications_by_status": {
                "applied": by_status.get("applied", 0),
                "interview": by_status.get("interview", 0),
                "offer": by_status.get("offer", 0),
                "rejected": by_status.get("rejected", 0),
            },
            "last_login_at": dict(u).get("last_login_at") if u else None,
            "member_since": dict(u).get("created_at") if u else None,
        }


def global_stats() -> Dict[str, Any]:
    """Admin stats across all users."""
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        eligible_jobs = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE rejects_foreigners=0 AND ad_summary_en != '' AND ad_summary_en != '(analysis failed)'"
        ).fetchone()[0]
        total_favorites = conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
        total_applications = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        total_saved_searches = conn.execute("SELECT COUNT(*) FROM saved_searches").fetchone()[0]
        # Jobs by country (top 10)
        by_country = conn.execute(
            "SELECT country_code, country_name, COUNT(*) AS n FROM jobs GROUP BY country_code ORDER BY n DESC LIMIT 10"
        ).fetchall()
        by_category = conn.execute(
            "SELECT category, COUNT(*) AS n FROM jobs GROUP BY category ORDER BY n DESC"
        ).fetchall()
        # Recent scans
        recent_scans = conn.execute(
            "SELECT * FROM scan_log ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        return {
            "total_users": total_users,
            "total_jobs": total_jobs,
            "eligible_jobs": eligible_jobs,
            "total_favorites": total_favorites,
            "total_applications": total_applications,
            "total_saved_searches": total_saved_searches,
            "jobs_by_country": [dict(r) for r in by_country],
            "jobs_by_category": [dict(r) for r in by_category],
            "recent_scans": [dict(r) for r in recent_scans],
        }


# Saved search scheduling -------------------------------------------------

VALID_SCHEDULE_FREQUENCIES = {"off", "daily", "weekly"}


def set_saved_search_schedule(user_id: int, search_id: int, frequency: str) -> bool:
    if frequency not in VALID_SCHEDULE_FREQUENCIES:
        raise ValueError(f"Invalid frequency: {frequency}")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE saved_searches SET schedule_frequency=? WHERE id=? AND user_id=?",
            (frequency, search_id, user_id)
        )
        return cur.rowcount > 0


def list_scheduled_searches(frequency: str) -> List[Dict[str, Any]]:
    """Get all saved searches with a given schedule frequency, across all users."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, u.telegram_chat_id, u.username, u.id AS user_id
               FROM saved_searches s
               JOIN users u ON s.user_id = u.id
               WHERE s.schedule_frequency=?""",
            (frequency,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["countries"] = json.loads(d.get("countries") or "[]")
            d["categories"] = json.loads(d.get("categories") or "[]")
            out.append(d)
        return out


def touch_saved_search_notified(search_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE saved_searches SET last_notified_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), search_id)
        )


def log_digest(user_id: int, saved_search_id: Optional[int], new_jobs_count: int, channel: str):
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO job_alert_digest (user_id, saved_search_id, new_jobs_count, sent_at, channel)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, saved_search_id, new_jobs_count,
                 datetime.utcnow().isoformat(), channel)
            )
    except Exception:
        pass


# Resume path helper ------------------------------------------------------

def set_resume_path(user_id: int, path: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        # Ensure row exists
        conn.execute(
            """INSERT OR IGNORE INTO user_settings
               (user_id, notify_telegram, notify_email, email, min_salary, max_commute_km, resume_path, email_digest)
               VALUES (?, 1, 0, '', '', NULL, ?, 0)""",
            (user_id, path)
        )
        conn.execute(
            "UPDATE user_settings SET resume_path=? WHERE user_id=?",
            (path, user_id)
        )


# Scan log list helper ----------------------------------------------------

def list_scan_log(limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_log ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
