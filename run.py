"""
Entrypoint for JobRadar.
Run with: python run.py
"""

import os
import logging

import uvicorn

from jobradar.config import settings
from jobradar.webapp import app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def resolve_port() -> int:
    """
    Railway injects PORT at runtime. Be paranoid and check every possible source:
      1. PORT env var (Railway's dynamic port)
      2. WEBAPP_PORT env var (our custom override)
      3. settings.WEBAPP_PORT (already PORT-aware, but just in case)
      4. 8000 fallback
    """
    port_str = (
        os.getenv("PORT")
        or os.getenv("WEBAPP_PORT")
        or str(getattr(settings, "WEBAPP_PORT", 8000))
        or "8000"
    )
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = 8000
    return port


if __name__ == "__main__":
    port = resolve_port()
    host = os.getenv("WEBAPP_HOST", settings.WEBAPP_HOST)

    # Loud startup banner so we can see in Railway logs what's happening
    print("=" * 60, flush=True)
    print(f"  JobRadar starting", flush=True)
    print(f"  HOST = {host}", flush=True)
    print(f"  PORT = {port}  (Railway PORT env = {os.getenv('PORT', '<not set>')})", flush=True)
    print(f"  Binding to http://{host}:{port}", flush=True)
    print("=" * 60, flush=True)

    uvicorn.run(
        "jobradar.webapp:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
