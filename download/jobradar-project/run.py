"""
Entrypoint for JobRadar.
Run with: python run.py
"""

import asyncio
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


if __name__ == "__main__":
    uvicorn.run(
        "jobradar.webapp:app",
        host=settings.WEBAPP_HOST,
        port=settings.WEBAPP_PORT,
        reload=False,
    )
