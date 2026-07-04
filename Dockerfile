# JobRadar Dockerfile — Railway-ready
FROM python:3.11-slim

# System deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 libdrm2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium
RUN playwright install chromium

# Copy app
COPY . .

# Railway injects PORT at runtime; we don't hardcode it here.
# Locally, config.py defaults to 8000 if neither PORT nor WEBAPP_PORT is set.

# Run
CMD ["python", "run.py"]
