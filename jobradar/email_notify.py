"""
Email notification module — Phase 3.
Sends job alerts and login codes via SMTP using stdlib smtplib + email.mime.

Configuration (env vars):
  SMTP_HOST          e.g. smtp.gmail.com
  SMTP_PORT          e.g. 587 (STARTTLS) or 465 (SSL)
  SMTP_USER          sender email address
  SMTP_PASSWORD      sender password / app-specific password
  SMTP_FROM_NAME     display name (default "JobRadar")
  SMTP_USE_SSL       "true" → use SMTP_SSL on connect (port 465 typically)
                     "false" (default) → STARTTLS upgrade after connect (port 587 typically)
"""

import logging
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List

from .config import settings
from .database import log_email

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


def _build_message(to_email: str, subject: str, html_body: str, text_body: str = "") -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.SMTP_FROM_NAME or 'JobRadar'} <{settings.SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def send_email(to_email: str, subject: str, html_body: str,
               text_body: str = "", user_id: Optional[int] = None) -> bool:
    """Send an email. Returns True on success, False on failure."""
    if not _smtp_configured():
        logger.warning("SMTP not configured — skipping email send")
        log_email(user_id, to_email, subject, html_body, "failed",
                  error="SMTP not configured")
        return False

    msg = _build_message(to_email, subject, html_body, text_body)
    try:
        if settings.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as s:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.sendmail(settings.SMTP_USER, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.sendmail(settings.SMTP_USER, [to_email], msg.as_string())
        log_email(user_id, to_email, subject, html_body, "sent")
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email send failed to {to_email}: {e}")
        log_email(user_id, to_email, subject, html_body, "failed", error=str(e))
        return False


def build_job_alert_html(jobs: List[dict], saved_search_name: str = "") -> str:
    """Build an HTML email body listing new jobs for a saved search."""
    rows = []
    for j in jobs[:25]:
        title = j.get("title") or "(untitled)"
        company = j.get("company") or ""
        country = j.get("country_name") or ""
        phone = j.get("phone_normalized") or ""
        summary = j.get("ad_summary_en") or ""
        url = j.get("url") or "#"
        rows.append(f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #1f2a44;">
            <div style="font-size:15px;font-weight:600;color:#fff;">
              <a href="{url}" style="color:#22d3ee;text-decoration:none;">{title}</a>
            </div>
            <div style="font-size:13px;color:#9fb3c8;margin-top:2px;">
              {company} • {country}
            </div>
            <div style="font-size:13px;color:#cbd5e1;margin-top:6px;">{summary}</div>
            {'<div style="font-size:13px;color:#22d3ee;margin-top:4px;">📞 ' + phone + '</div>' if phone else ''}
          </td>
        </tr>
        """)
    extra = ""
    if len(jobs) > 25:
        extra = f"<p style='color:#9fb3c8;font-size:13px;'>+ {len(jobs)-25} more jobs in the dashboard.</p>"

    return f"""
    <div style="background:#0a1628;padding:24px;font-family:Inter,Arial,sans-serif;color:#e2e8f0;">
      <h2 style="color:#22d3ee;margin:0 0 8px 0;">🛰️ JobRadar — New Jobs Found</h2>
      <p style="color:#9fb3c8;font-size:14px;margin:0 0 18px 0;">
        Saved search <b style="color:#fff;">{saved_search_name or 'Daily Digest'}</b> • {len(jobs)} new job(s)
      </p>
      <table style="width:100%;border-collapse:collapse;">
        {"".join(rows)}
      </table>
      {extra}
      <p style="margin-top:24px;font-size:12px;color:#64748b;">
        JobRadar Global • {settings.WEBAPP_PUBLIC_URL or ''}
      </p>
    </div>
    """


def build_login_code_email(code: str, username: str) -> tuple:
    """Returns (html, text) for a login code email."""
    html = f"""
    <div style="background:#0a1628;padding:32px;font-family:Inter,Arial,sans-serif;color:#e2e8f0;text-align:center;">
      <h2 style="color:#22d3ee;margin:0 0 8px 0;">🛰️ JobRadar Login</h2>
      <p style="color:#9fb3c8;font-size:14px;margin:0 0 24px 0;">
        Here is your one-time login code. It expires in 10 minutes.
      </p>
      <div style="display:inline-block;background:#1e293b;border:1px solid #22d3ee;border-radius:12px;padding:18px 32px;margin:0 auto;">
        <span style="font-size:38px;font-weight:700;color:#22d3ee;letter-spacing:8px;font-family:monospace;">{code}</span>
      </div>
      <p style="color:#64748b;font-size:12px;margin-top:24px;">
        If you didn't request this code, you can safely ignore this email.
      </p>
    </div>
    """
    text = f"JobRadar Login\n\nYour one-time login code is: {code}\n\nIt expires in 10 minutes.\nIf you didn't request it, ignore this email."
    return html, text
