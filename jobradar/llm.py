"""
Gemini LLM integration.
Used to:
  - Extract a 1-line English summary of a job ad
  - Detect whether the ad explicitly rejects foreigners
  - Extract phone numbers and normalize them with the right country code
"""

import json
import re
import logging
from typing import Optional, Dict, Any

import httpx

from .config import settings, COUNTRIES

logger = logging.getLogger(__name__)


GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)


PROMPT_TEMPLATE = """You are an assistant analyzing a job advertisement.

The ad was scraped from a job portal in {country_name} (dial code {dial_code}, TLD {tld}).
The original text is likely in {language}, but may contain mixed languages.

Your task: return ONLY valid JSON with the following shape (no markdown, no commentary):

{{
  "summary_en": "One single line in English, max 120 chars, summarizing the role and key conditions (salary if mentioned, location, shift).",
  "rejects_foreigners": true | false,
  "rejects_foreigners_reason": "If true, quote the exact phrase from the ad that rejects non-citizens/non-locals. If false, empty string.",
  "phone_raw": "the raw phone string as it appears in the ad, or empty string if none",
  "phone_normalized": "phone normalized to E.164 with the country dial code, e.g. +38163123456, or empty if none",
  "company": "employer name if mentioned, otherwise empty string",
  "is_relevant": true | false   // true if the ad is actually for courier/construction/factory work; false if it's unrelated (e.g. a banner, a CV, a manager role)
}}

Ad text (truncated to 6000 chars):
---
{ad_text}
---

Return ONLY the JSON object.
"""


async def analyze_ad(country_code: str, ad_text: str) -> Optional[Dict[str, Any]]:
    """
    Calls Gemini to analyze a job ad.
    Returns the parsed JSON dict, or None on error.
    """
    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set")
        return None

    country = COUNTRIES.get(country_code)
    if not country:
        logger.error(f"Unknown country code: {country_code}")
        return None

    truncated = ad_text[:6000]
    prompt = PROMPT_TEMPLATE.format(
        country_name=country.name,
        dial_code=country.dial_code,
        tld="",  # not used in this version
        language=country.language,
        ad_text=truncated,
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 600,
            "responseMimeType": "application/json",
        },
    }

    url = GEMINI_URL.format(model=settings.GEMINI_MODEL, api_key=settings.GEMINI_API_KEY)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        if not text:
            logger.warning(f"Gemini returned empty for ad in {country_code}")
            return None

        # Some models wrap JSON in ```json ... ``` even with responseMimeType
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        return json.loads(text)
    except httpx.HTTPStatusError as e:
        logger.error(f"Gemini HTTP error: {e.response.status_code} - {e.response.text[:300]}")
        return None
    except Exception as e:
        logger.error(f"Gemini analyze_ad error: {e}")
        return None


# ---------------------------------------------------------------------------
# Phone normalization (fallback if Gemini misses it)
# ---------------------------------------------------------------------------

PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s\-\(\)\.]{7,}\d)"
)


def normalize_phone(raw: str, dial_code: str) -> str:
    """Best-effort normalization to E.164."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return ""
    # If it starts with 00, replace with +
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    # If it doesn't start with + and we have a country dial code, prepend it
    if not digits.startswith("+"):
        bare = digits.lstrip("0")
        # strip leading 0 of national format
        digits = dial_code + bare
    return digits[:16]
