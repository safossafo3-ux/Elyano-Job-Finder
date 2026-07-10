"""
Multi-portal job scraper — Phase 4.

For each (country, category) we now query up to 15+ job portals in parallel batches:
  - Indeed (still primary, country-specific subdomain)
  - LinkedIn, Jooble, Jora, Talent.com, CareerJet, Glassdoor, Monster
  - Region-specific portals (StepStone, Xing, Bayt, JobStreet, Reed, Totaljobs, ...)
  - Recruitment agencies (Hays, Michael Page, Adecco, Randstad, Manpower)
  - DuckDuckGo/Google fallbacks

Strategies (v3 — fast, listing-first):
  - Use real browser fingerprint + viewport
  - Block images/fonts to speed up
  - Extract RICH job data (title, company, summary, location, url) directly
    from the listing page where possible — this means jobs are saved even
    if the detail page is blocked or slow.
  - Only fetch the detail page for the FIRST FEW jobs as a "richness upgrade"
    (so we still get the phone number / full ad text when possible).
  - Save EVERY job found, even if detail fetch fails. Use listing-page text as
    the ad text. The LLM analyzes whatever it has.
  - Cap jobs per portal (default 15) → up to 15 portals × 15 jobs = 225 candidate jobs per (country, category)
  - Deduplicate by URL across portals
"""

import asyncio
import logging
import os
import random
import string
from typing import List, Dict, Optional, Set, Tuple
from urllib.parse import quote, quote_plus, urljoin, urlparse

import httpx
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from ..config import settings, COUNTRIES, CATEGORIES, _default_screenshots_dir, get_keyword
from ..database import (
    upsert_job, log_scan_start, log_scan_finish,
)
from ..pipeline import analyze_and_notify_single
from ..portals import Portal, portals_for, PORTALS

logger = logging.getLogger(__name__)


SCREENSHOTS_DIR = _default_screenshots_dir()
MAX_PORTALS_PER_COUNTRY = int(os.getenv("MAX_PORTALS_PER_COUNTRY", "30"))
MAX_JOBS_PER_PORTAL = int(os.getenv("MAX_JOBS_PER_PORTAL", "20"))
# How many of the top jobs per portal get a detail-page fetch (slow, gets blocked).
# Keep this LOW so the overall scan finishes quickly.
MAX_DETAIL_FETCHES_PER_PORTAL = int(os.getenv("MAX_DETAIL_FETCHES_PER_PORTAL", "3"))

# --- TIMEOUTS (prevent scan hangs) ---
# Per-portal hard cap. If a single portal takes longer than this (load + extract
# + Cloudflare solve + httpx fallback), we abort it and move on. This is the
# single most important fix for the "scan yields nothing" issue: previously a
# single hung portal (waiting forever on Cloudflare or a slow page) would block
# the entire scan and the user would see "0 jobs found" forever.
PORTAL_TIMEOUT_SECONDS = float(os.getenv("PORTAL_TIMEOUT_SECONDS", "45"))
# Overall cap for one (country, category) — even if every portal hangs, the
# scan finishes within this many seconds and reports whatever it found.
SCAN_TIMEOUT_SECONDS = float(os.getenv("SCAN_TIMEOUT_SECONDS", "300"))


def screenshot_path_for(url: str) -> str:
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    slug = "".join(c if c in string.ascii_letters + string.digits else "_" for c in url)[:120]
    return f"{SCREENSHOTS_DIR}/{slug}.png"


# ---------------------------------------------------------------------------
# Cloudflare / challenge-page detection — returns True if the page title or
# body matches a known bot-challenge signature. When this returns True, the
# scraper MUST NOT try to extract job links (the links it finds are false
# positives like "Find jobs" navigation links from the challenge page).
# ---------------------------------------------------------------------------
CHALLENGE_TITLE_KW = [
    "just a moment", "attention required", "access denied", "are you a robot",
    "captcha", "verify you are", "403 forbidden", "service unavailable",
    "enable javascript and cookies", "checking your browser",
]

# Body-text signatures that indicate a Cloudflare/bot-challenge page even
# when the <title> doesn't match any of the keywords above.
CHALLENGE_BODY_KW = [
    "verify you are human", "verifying you are human",
    "checking your browser before accessing",
    "please turn javascript on and reload",
    "this process is automatic",
    "completing the captcha",
    "ray id:", "performance & security by cloudflare",
    "needs to review the security of your connection",
    "demande de vérification",
    "überprüfen sie", "verificando",
]


def is_challenge_page(title: str) -> bool:
    """Detect Cloudflare/anti-bot challenge pages by title."""
    t = (title or "").lower().strip()
    return any(kw in t for kw in CHALLENGE_TITLE_KW)


async def is_page_challenged(page: Page) -> bool:
    """Check the Playwright page for challenge signatures — both the <title>
    AND the visible body text. Cloudflare sometimes serves the challenge with
    a normal-looking title but a body containing 'Verify you are human' etc.
    """
    try:
        title = await page.title()
        if is_challenge_page(title):
            return True
    except Exception:
        pass
    # Body-text check — catches Cloudflare variants that set a benign title
    try:
        body_text = await page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 5000) : ''")
        if body_text:
            bt = body_text.lower()
            for kw in CHALLENGE_BODY_KW:
                if kw in bt:
                    return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Cloudflare Turnstile / challenge auto-solver
#
# When a page is served behind Cloudflare's "Verify you are human" interstitial,
# the page typically shows a checkbox inside an iframe from challenges.cloudflare.com.
# Clicking that checkbox once and WAITING (without further interaction) lets
# Cloudflare validate the browser fingerprint and auto-redirect to the real
# content. This is exactly the behavior the user described:
#   "click it once and wait for 18 seconds with no any activity on the page,
#    it will load on it's own"
#
# We detect the Turnstile iframe, click slightly LEFT OF CENTER (matches the
# checkbox position the user observed), then wait silently for up to 30s.
# ---------------------------------------------------------------------------

# Selectors that match the Cloudflare Turnstile checkbox iframe and the
# clickable area inside it. Cloudflare rotates these occasionally, so we
# try several.
TURNSTILE_IFRAME_SELECTORS = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="/cdn-cgi/challenge-platform/"]',
    'iframe[title*="Cloudflare" i]',
    'iframe[title*="Widget" i][src*="cloudflare"]',
    'iframe[title*="challenge" i]',
    '#cf-chl-widget-iframe',
    'div.cf-turnstile iframe',
]

# Clickable checkbox selectors inside the Turnstile widget (after switching
# into the iframe's content frame).
TURNSTILE_CHECKBOX_SELECTORS = [
    'input[type="checkbox"]',
    'div.cf-turnstile',
    '#challenge-stage input',
    'label.cb-lb',
    'div#cf-stage',
]


async def _find_turnstile_iframe(page: Page):
    """Locate a Cloudflare Turnstile iframe on the page. Returns the frame
    element handle or None."""
    for sel in TURNSTILE_IFRAME_SELECTORS:
        try:
            handle = await page.query_selector(sel)
            if handle:
                return handle
        except Exception:
            continue
    # Fallback: any iframe whose src mentions cloudflare or challenge
    try:
        for frame in page.frames:
            url = frame.url or ""
            if "challenges.cloudflare.com" in url or "/cdn-cgi/challenge-platform/" in url:
                return frame  # already a Frame
    except Exception:
        pass
    return None


async def _click_turnstile_checkbox(page: Page, iframe_handle) -> bool:
    """Click the Cloudflare checkbox once. The checkbox is visually positioned
    slightly LEFT OF CENTER inside the widget. We try (a) clicking inside the
    iframe's content frame, then (b) clicking the iframe element at a
    slightly-left-of-center offset as a fallback."""
    # Strategy 1: switch into the iframe's content frame and click the checkbox
    try:
        frame = None
        if hasattr(iframe_handle, "content_frame"):
            frame = await iframe_handle.content_frame()
        elif hasattr(iframe_handle, "url"):  # already a Frame
            frame = iframe_handle
        if frame:
            for sel in TURNSTILE_CHECKBOX_SELECTORS:
                try:
                    box = await frame.query_selector(sel)
                    if box:
                        await box.click(timeout=2000, no_wait_after=True)
                        return True
                except Exception:
                    continue
            # If no checkbox element found, click the frame body at the
            # standard checkbox offset (left-of-center, vertically centered).
            try:
                await frame.click("body", position={"x": 28, "y": 28},
                                  timeout=2000, no_wait_after=True)
                return True
            except Exception:
                pass
    except Exception:
        pass

    # Strategy 2: click the IFRAME ELEMENT itself at slightly-left-of-center.
    # The user described the checkbox as "at the center of the screen, to the
    # left side slightly" — so we use 35% of the iframe width (left of center)
    # and 50% height.
    try:
        bbox = await iframe_handle.bounding_box()
        if bbox:
            x = bbox["x"] + bbox["width"] * 0.35
            y = bbox["y"] + bbox["height"] * 0.5
            await page.mouse.move(x, y)
            await page.mouse.click(x, y)
            return True
    except Exception:
        pass
    return False


async def solve_cloudflare_challenge(page: Page, max_wait_seconds: int = 18) -> bool:
    """Detect a Cloudflare Turnstile challenge on the page, click the checkbox
    ONCE, then wait silently for the page to auto-redirect.

    Returns True if the challenge was solved (page navigated away from the
    challenge URL or the title changed), False otherwise.

    Per user instruction:
      "there is a check box usually at the center of the screen to the left
       side slightly, click it once and wait for 18 seconds with no any
       activity on the page..it will load on it's own"
    """
    try:
        title_before = (await page.title()) or ""
    except Exception:
        title_before = ""
    url_before = page.url or ""

    iframe_handle = await _find_turnstile_iframe(page)
    if not iframe_handle:
        # No Turnstile iframe — nothing to solve.
        return False

    logger.info(f"[Cloudflare] Turnstile iframe detected on {url_before[:80]} — clicking checkbox once")

    clicked = await _click_turnstile_checkbox(page, iframe_handle)
    if not clicked:
        logger.info(f"[Cloudflare] Could not click the checkbox — trying fallback full-page click")
        # Last resort: click at slightly-left-of-center of the viewport
        try:
            vp = page.viewport_size or {"width": 1366, "height": 900}
            await page.mouse.click(vp["width"] * 0.35, vp["height"] * 0.5)
            clicked = True
        except Exception:
            clicked = False

    if not clicked:
        return False

    # Now WAIT SILENTLY for up to max_wait_seconds. No further clicks, no
    # scrolling, no mouse movements — any activity can reset the challenge.
    # Poll the page title/URL every 1s; if either changes, the challenge
    # has been solved.
    logger.info(f"[Cloudflare] Checkbox clicked — waiting silently up to {max_wait_seconds}s for auto-redirect")
    waited = 0
    while waited < max_wait_seconds:
        await asyncio.sleep(1)
        waited += 1
        try:
            title_now = (await page.title()) or ""
        except Exception:
            title_now = title_before
        url_now = page.url or ""
        # Solved if the title changed away from a challenge signature,
        # OR the URL changed, OR no challenge iframe is present anymore.
        if title_now != title_before and not is_challenge_page(title_now):
            logger.info(f"[Cloudflare] Solved after {waited}s — title changed to '{title_now[:60]}'")
            # Give the page a moment to settle after the redirect
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return True
        if url_now != url_before and "challenge" not in url_now:
            logger.info(f"[Cloudflare] Solved after {waited}s — URL changed to {url_now[:80]}")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return True
        # Check if the Turnstile iframe is gone
        iframe_still_there = await _find_turnstile_iframe(page)
        if not iframe_still_there and not is_challenge_page(title_now):
            logger.info(f"[Cloudflare] Solved after {waited}s — challenge iframe removed")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return True

    logger.info(f"[Cloudflare] Waited {waited}s — challenge not cleared, continuing anyway")
    return False


# ---------------------------------------------------------------------------
# httpx-based fallback scrapers — these bypass Cloudflare's Playwright
# fingerprint detection by using a real browser User-Agent + Accept headers
# and a simple HTTP GET. Many portals (Talent.com, CareerJet, DuckDuckGo,
# LinkedIn JSON endpoint) serve usable HTML/JSON to plain HTTP clients
# while blocking Playwright headless browsers.
# ---------------------------------------------------------------------------
HTTPX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


async def httpx_get(url: str, timeout: float = 15.0) -> Optional[str]:
    """Plain HTTP GET via httpx. Returns HTML text or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HTTPX_HEADERS) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return None
            ct = r.headers.get("content-type", "")
            if "text" not in ct and "html" not in ct and "json" not in ct:
                return None
            return r.text
    except Exception as e:
        logger.debug(f"httpx_get failed for {url[:80]}: {e}")
        return None


# ---------------------------------------------------------------------------
# Per-portal job link extractors — each returns a list of {url, title} dicts
# ---------------------------------------------------------------------------

async def extract_job_links_generic(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Generic extractor: scan all <a> elements with job-like text/href.
    Returns rich dicts: {url, title, company, location, summary}."""
    return await page.evaluate(
        """
        (baseurl) => {
          const jobKw = /(job|vacanc|career|posao|empleo|stelle|angebot|offr|vaga|praca|arbete|tyopaikka|virka|töö|darbs|pra| lavoro|trabajo|pracovní|munka|pozíció|functions?|rol[eé]|sự nghiệp|lowongan|pekerjaan|ongkon|採用|求人|채용|招聘| работа|робота|zaposlitev)/i;
          const jobHrefKw = /(\\/jobs?\\/|\\/job\\/|\\/vacanc|\\/career|job-details|job-view|viewjob|\\/puestos?\\/|\\/ofertas?\\/|\\/offres?\\/|\\/stellen?\\/|\\/stellenangebot|\\/vacancy|\\/vacantes?\\/|\\?q=|\\/search\\/)/i;
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href;
            if (!href || href.startsWith('javascript:') || href.startsWith('#')) continue;
            const text = (a.innerText || a.textContent || '').trim();
            if (text.length < 4 || text.length > 250) continue;
            const isJobText = jobKw.test(text);
            const isJobHref = jobHrefKw.test(href);
            if (!isJobText && !isJobHref) continue;
            // Avoid pagination / category links
            if (/^(next|prev|more|load|filter|sort|back|home|login|sign)/i.test(text)) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            // Try to grab a sibling company name and summary
            const card = a.closest('[class*=job], [class*=card], [class*=result], li, article, tr, [data-cy]');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=company], [class*=employer], [data-company-name], .company-name, .job-card__company-name, [class*=business]');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], [class*=loc], [data-location]');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
              const sumEl = card.querySelector('[class*=snippet], [class*=summary], [class*=description], .job-snippet');
              if (sumEl) summary = (sumEl.innerText || '').trim().slice(0,500);
            }
            out.push({url: href, title: text.slice(0, 200), company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """,
        base_url,
    )


async def extract_job_links_indeed(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Indeed-specific selectors across multiple versions.
    Returns rich dicts with company + summary where available."""
    return await page.evaluate(
        """
        () => {
          const sels = [
            'a.jcs-JobTitle.css-jcqul8.eu4oa1w0',
            'a[data-jk]',
            'h2.jobTitle a',
            'a.jobTitle',
            'a[id^="job_"]',
            'div.card a.tapItem',
            '[data-testid="job-title"] a',
            'a[data-testid="job-title"]'
          ];
          const seen = new Set();
          const out = [];
          for (const sel of sels) {
            for (const a of document.querySelectorAll(sel)) {
              const href = a.href || a.getAttribute('href');
              if (!href || seen.has(href)) continue;
              seen.add(href);
              const title = (a.innerText || a.textContent || '').trim().slice(0,200);
              // Find parent card for company/snippet
              const card = a.closest('li, .job_seen_beacon, [class*=result], [data-jk], div.card, tr');
              let company = '';
              let location = '';
              let summary = '';
              if (card) {
                const coEl = card.querySelector('[data-company-name], .companyName, [class*=companyName], [class*=company-name]');
                if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
                const locEl = card.querySelector('[data-testid="text-location"], .companyLocation, [class*=location]');
                if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
                const sumEl = card.querySelector('.job-snippet, [class*=snippet], [class*=summary]');
                if (sumEl) summary = (sumEl.innerText || '').trim().slice(0,500);
              }
              out.push({url: href, title, company, location, summary});
              if (out.length >= 30) break;
            }
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_linkedin(page: Page, base_url: str) -> List[Dict[str, str]]:
    """LinkedIn Jobs result cards."""
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a.base-card__full-link, a[href*="/jobs/view/"], div.job-card-container a[href*="/jobs/view/"]')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.job-card-container, li, [class*=job-card], [data-entity-urn]');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('.job-card-container__company-name, [class*=company-name], .base-search-card__subtitle');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('.job-card-container__metadata-item, [class*=location], .base-search-card__metadata');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_jooble(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/job/"], .vacancy a, h2 a')) {
            const href = a.href;
            if (!href || seen.has(href) || href.includes('jooble.org/SearchResult')) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.vacancy, [class*=job], li, article, tr');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=company], .vacancy-company, .gray_text');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], [class*=region]');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
              const sumEl = card.querySelector('.description, [class*=snippet]');
              if (sumEl) summary = (sumEl.innerText || '').trim().slice(0,500);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_talent(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Talent.com — multiple selector families, including the current
    `a[data-job-id]` + `.job-card__title` layout used in 2024+."""
    return await page.evaluate(
        r"""
        () => {
          const out = [];
          const seen = new Set();
          // Multiple selector strategies — Talent.com has changed layouts several times
          const sels = [
            'a[data-job-id]',
            'a.job-card',
            '.job-card a',
            '.job a[href*="/job/"]',
            'a[href*="/job/"]',
            'li article a',
            'h2 a', 'h3 a',
          ];
          for (const sel of sels) {
            for (const a of document.querySelectorAll(sel)) {
              const href = a.href;
              if (!href || seen.has(href)) continue;
              // Skip pagination/category nav
              if (!href.includes('/job/') && !href.match(/\/j\d+/i)) continue;
              seen.add(href);
              const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
              if (title.length < 3) continue;
              const card = a.closest('.job-card, article, li, tr, [class*=result]');
              let company = '';
              let location = '';
              let summary = '';
              if (card) {
                const coEl = card.querySelector('[class*=company], .company, [class*=business], [class*=employer]');
                if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
                const locEl = card.querySelector('[class*=location], .location, [class*=region]');
                if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
              }
              out.push({url: href, title, company, location, summary});
              if (out.length >= 30) break;
            }
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_jora(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/job/"], a[href*="viewjob"], .job-item a, h3 a')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.job-item, li, article, tr, [class*=result]');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=company], .company');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], .location');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_careerjet(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/job/"], a[href*="jobdetail"], .job a, h2 a')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.job, li, article, tr, [class*=result]');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=company], .company');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], .location');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
              const sumEl = card.querySelector('.desc, [class*=description], [class*=snippet]');
              if (sumEl) summary = (sumEl.innerText || '').trim().slice(0,500);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_glassdoor(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/Job/"], a[href*="job-listing"], .jobCard a, [data-test*="job-link"]')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.jobCard, li, [class*=job], article');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=employer], .employerName, [data-test*=employer]');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], .loc');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_monster(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/job/"], a[href*="job-openings"], .job-card a, h2 a, h3 a')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            const card = a.closest('.job-card, li, article, tr, [class*=result]');
            let company = '';
            let location = '';
            let summary = '';
            if (card) {
              const coEl = card.querySelector('[class*=company], .company');
              if (coEl) company = (coEl.innerText || '').trim().slice(0,200);
              const locEl = card.querySelector('[class*=location], .location');
              if (locEl) location = (locEl.innerText || '').trim().slice(0,120);
            }
            out.push({url: href, title, company, location, summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


# ---------------------------------------------------------------------------
# httpx-based fallback scrapers — for portals that block Playwright but serve
# usable HTML to plain HTTP clients. These return the same {url, title,
# company, location, summary} dict shape as the Playwright extractors.
# ---------------------------------------------------------------------------

import re as _re
from html import unescape as _unescape


def _strip_html(s: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    s = _re.sub(r"<[^>]+>", " ", s)
    s = _unescape(s)
    return _re.sub(r"\s+", " ", s).strip()


def httpx_extract_talent(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse Talent.com HTML with regex. Talent.com uses <a data-job-id="..."
    with the job title as inner text. Returns rich dicts."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # Match <a ... href=".../job/<id>" ...>title</a>
    for m in _re.finditer(
        r'<a[^>]+href="([^"]*?/job/[^"]+)"[^>]*>(.*?)</a>',
        html, _re.IGNORECASE | _re.DOTALL,
    ):
        href = m.group(1)
        title = _strip_html(m.group(2))[:200]
        if not title or len(title) < 3:
            continue
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if href in seen:
            continue
        seen.add(href)
        out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
        if len(out) >= 30:
            break
    return out


def httpx_extract_careerjet(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse CareerJet HTML. Job links contain '/job/' or 'jobdetail'."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for m in _re.finditer(
        r'<a[^>]+href="([^"]+(?:/job/|jobdetail)[^"]*)"[^>]*>(.*?)</a>',
        html, _re.IGNORECASE | _re.DOTALL,
    ):
        href = m.group(1)
        title = _strip_html(m.group(2))[:200]
        if not title or len(title) < 3:
            continue
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if href in seen:
            continue
        seen.add(href)
        out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
        if len(out) >= 30:
            break
    return out


def httpx_extract_duckduckgo(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse DuckDuckGo HTML search results. DDG serves a simple HTML page
    at html.duckduckgo.com/html/?q=... that's parseable without JS."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # DDG HTML results: <a class="result__a" href="...">title</a>
    # The href is usually a redirect like //duckduckgo.com/l/?uddg=<encoded_url>
    for m in _re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html, _re.IGNORECASE | _re.DOTALL,
    ):
        href = m.group(1)
        title = _strip_html(m.group(2))[:200]
        if not title or len(title) < 3:
            continue
        # Decode DDG redirect
        if "uddg=" in href:
            try:
                from urllib.parse import parse_qs, urlparse as _up
                q = parse_qs(_up(href).query).get("uddg", [""])[0]
                if q:
                    href = q
            except Exception:
                pass
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/"):
            href = urljoin(base_url, href)
        # Skip DDG internal links
        if "duckduckgo.com" in href and "uddg=" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
        if len(out) >= 30:
            break
    return out


# ---------------------------------------------------------------------------
# Search-engine result page (SERP) extractors — Google, Bing, Yandex, DDG.
#
# When we use Google/Bing/Yandex as a "portal", the search results page links
# to job postings on various job boards (Infostud, Posao.ba, Indeed, etc.).
# We extract these external job-board links and treat each as a job.
# ---------------------------------------------------------------------------

# Known job-board URL patterns — when a search result URL matches one of
# these, we treat it as a job posting. This filter is critical because Google
# SERPs contain many non-job links (news, Wikipedia, blog posts, etc.).
JOB_URL_PATTERNS = [
    # International job boards
    "/jobs/", "/job/", "/job-view", "/viewjob", "/jobdetail", "/jobs/view/",
    "/vacanc", "/vacature", "/vacancy", "/stellen/", "/stellenangebot",
    "/offres/", "/offre/", "/ofertas/", "/oferta/", "/empleos/",
    "/praca/", "/allasok/", "/munka/", "/locuri-de-munca", "/darbo-skelbimai",
    "/toopakkumine", "/vakances", "/praca", "/allas",
    # Local-language job boards (Balkans)
    "infostud.com", "poslovi.infostud.com", "hello.work", "helloworld.rs",
    "posao.ba", "infostud.ba", "mojposao.ba", "poslovi.com",
    "infostud.rs", "najposao.rs", "poslovi.rs", "oglasiradim",
    # Local-language job boards (Eastern Europe)
    "ejobs.ro", "bestjobs.ro", "helloastronaut.com", "olx.ro",
    "cv.ee", "cvkeskus.ee", "cv.lv", "workingday.lv", "ss.com",
    "infopraca.pl", "olx.pl", "praca.pl", "gumtree.pl",
    # Local-language job boards (Western Europe)
    "stepstone.de", "xing.com", "meinestadt.de", "jobware.de",
    "infojobs.net", "infojobs.it", "monster.es", "laboris.net",
    "meteojob.com", "leboncoin.fr", "optioncarriere.com",
    "indeed.", "linkedin.com/jobs", "glassdoor.", "jooble.org",
    "talent.com", "careerjet.", "monster.", "bayt.com",
    "reed.co.uk", "totaljobs.com", "cv-library.co.uk", "jobsite.co.uk",
    "jobstreet.", "jobsdb.", "shine.com", "naukri.com",
    "computrabajo.", "bumeran.", "catho.com.br", "kariyer.net",
    "jobindex.dk", "jobnet.dk", "blocketjobbsajt.se", "blocket.se",
    "tori.ee", "tyopaikat.oikotie.fi", "duunitori.fi",
    # Generic patterns
    "career", "recruit", "apply", "hiring",
]

# Patterns to explicitly SKIP — non-job results that often appear in SERPs.
SKIP_SERP_URLS = [
    "wikipedia.org", "youtube.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "linkedin.com/company", "linkedin.com/in/",
    "linkedin.com/posts/", "reddit.com", "tiktok.com",
    "play.google.com", "apps.apple.com", "amazon.",
    "google.com/search", "google.com/url", "bing.com/search",
    "yandex.com/search", "duckduckgo.com",
    ".pdf", ".jpg", ".png", ".gif",
    "/news/", "/blog/", "/article/",
    "pinterest.", "medium.com",
]


def _is_likely_job_url(url: str) -> bool:
    """Heuristic: does this URL look like a job posting?"""
    if not url:
        return False
    u = url.lower()
    # Skip obvious non-job URLs
    for skip in SKIP_SERP_URLS:
        if skip in u:
            return False
    # Check for job URL patterns
    for pat in JOB_URL_PATTERNS:
        if pat in u:
            return True
    return False


def httpx_extract_google_serp(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse Google Search results page HTML. Google SERPs embed external
    result links inside <a href="/url?q=<actual-url>&..."> wrappers, plus
    <a href="<actual-url>"> in knowledge panels. We extract both, filter to
    likely job URLs, and dedupe."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # Google wraps result links as <a href="/url?q=ENCODED_URL&sa=...">title</a>
    # or sometimes as <a href="https://actual-url">title</a> in newer layouts.
    # We try both patterns.
    patterns = [
        # /url?q=... pattern (classic)
        r'<a[^>]+href="/url\?q=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
        # direct https:// links in <h3> parents (newer Google layout)
        r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
    ]
    for pat in patterns:
        for m in _re.finditer(pat, html, _re.IGNORECASE | _re.DOTALL):
            href = _unescape(m.group(1))
            title = _strip_html(m.group(2))[:200]
            if not title or len(title) < 5:
                continue
            if href.startswith("//"):
                href = "https:" + href
            if href.startswith("/"):
                href = urljoin(base_url, href)
            # Filter to likely job URLs
            if not _is_likely_job_url(href):
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
            if len(out) >= 30:
                return out
    return out


def httpx_extract_bing_serp(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse Bing search results. Bing wraps result links as
    <a href="https://actual-url">title</a> inside <li class="b_algo">."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for m in _re.finditer(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        html, _re.IGNORECASE | _re.DOTALL,
    ):
        href = m.group(1)
        title = _strip_html(m.group(2))[:200]
        if not title or len(title) < 5:
            continue
        # Skip Bing-internal links
        if "bing.com" in href.lower() or "microsoft.com" in href.lower():
            continue
        if not _is_likely_job_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
        if len(out) >= 30:
            break
    return out


def httpx_extract_yandex_serp(html: str, base_url: str) -> List[Dict[str, str]]:
    """Parse Yandex search results. Similar to Bing — direct https:// links."""
    return httpx_extract_bing_serp(html, base_url)


# Playwright-based SERP extractors — for when the search engine serves a
# JS-heavy page that needs a real browser.

async def extract_job_links_serp(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Generic SERP extractor for Google/Bing/Yandex/DDG. Reads all <a> tags,
    filters to likely-job URLs, returns the results. Works in a real browser
    context (after Cloudflare has been solved, if applicable)."""
    return await page.evaluate(
        """
        (baseurl) => {
          const out = [];
          const seen = new Set();
          // Known job URL patterns (must match the Python JOB_URL_PATTERNS)
          const jobPats = [
            /\\/jobs?\\//i, /\\/job\\//i, /\\/job-view/i, /\\/viewjob/i, /\\/jobdetail/i,
            /\\/vacanc/i, /\\/vacature/i, /\\/vacancy/i, /\\/stellen\\//i, /\\/stellenangebot/i,
            /\\/offres?\\//i, /\\/ofertas?\\//i, /\\/empleos?\\//i, /\\/praca\\//i,
            /\\/locuri-de-munca/i, /\\/darbo-skelbimai/i, /\\/toopakkumine/i,
            /\\/vakances/i, /\\/allas/i, /\\/allasok/i, /\\/munka/i,
            /infostud/i, /posao\\.ba/i, /helloworld/i, /hello\\.work/i,
            /najposao/i, /poslovi\\./i, /ejobs/i, /bestjobs/i,
            /olx\\./i, /cv\\.ee/i, /cv\\.lv/i, /cvkeskus/i, /workingday/i,
            /infopraca/i, /praca\\.pl/i, /stepstone/i, /xing/i, /infojobs/i,
            /indeed\\./i, /linkedin\\.com\\/jobs/i, /glassdoor/i, /jooble/i,
            /talent\\.com/i, /careerjet/i, /monster\\./i, /bayt/i, /reed\\.co/i,
            /totaljobs/i, /cv-library/i, /jobsite/i, /jobstreet/i, /jobsdb/i,
            /naukri/i, /computrabajo/i, /bumeran/i, /catho/i, /kariyer/i,
            /jobindex/i, /blocket/i, /duunitori/i, /oikotie/i,
            /career/i, /recruit/i, /apply/i, /hiring/i
          ];
          const skipPats = [
            /wikipedia/i, /youtube/i, /facebook/i, /twitter/i, /x\\.com/i,
            /instagram/i, /linkedin\\.com\\/company/i, /linkedin\\.com\\/in\\//i,
            /linkedin\\.com\\/posts\\//i, /reddit/i, /tiktok/i,
            /play\\.google/i, /apps\\.apple/i, /amazon\\./i,
            /google\\.com\\/(search|url)/i, /bing\\.com\\/(search|maps)/i,
            /yandex\\.com\\/(search|maps)/i, /duckduckgo/i,
            /\\.pdf$/i, /\\.jpg$/i, /\\.png$/i, /\\.gif$/i,
            /\\/news\\//i, /\\/blog\\//i, /pinterest/i, /medium\\.com/i
          ];
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href;
            if (!href || href.startsWith('javascript:') || href.startsWith('#')) continue;
            const text = (a.innerText || a.textContent || '').trim();
            if (text.length < 5 || text.length > 250) continue;
            // Skip non-job URLs
            if (skipPats.some(p => p.test(href))) continue;
            // Keep only job-like URLs
            if (!jobPats.some(p => p.test(href))) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            // Try to grab a snippet from the parent result container
            const card = a.closest('div.g, div.result, li.b_algo, div organic, div[data-ved], div[class*="result"]');
            let summary = '';
            if (card) {
              const span = card.querySelector('span, div[class*="snippet"], div[class*="desc"]');
              if (span) summary = (span.innerText || '').trim().slice(0, 500);
            }
            out.push({url: href, title: text.slice(0, 200), company: '', location: '', summary: summary});
            if (out.length >= 30) break;
          }
          return out;
        }
        """,
        base_url,
    )


# Map portal_type -> httpx fallback extractor
_HTTPX_EXTRACTORS = {
    "talent":      httpx_extract_talent,
    "careerjet":   httpx_extract_careerjet,
    "duckduckgo":  httpx_extract_duckduckgo,
    "duckduckgo_local": httpx_extract_duckduckgo,
    "duckduckgo_sites": httpx_extract_duckduckgo,
    "google":      httpx_extract_google_serp,
    "google_local": httpx_extract_google_serp,
    "google_jobs": httpx_extract_google_serp,
    "google_jobs_ddg": httpx_extract_duckduckgo,
    "bing":        httpx_extract_bing_serp,
    "yandex":      httpx_extract_yandex_serp,
}


async def httpx_scrape_portal(portal_type: str, search_url: str) -> List[Dict[str, str]]:
    """Try to scrape a portal with httpx instead of Playwright. Returns a list
    of job dicts, or an empty list on failure. Used as a fallback when the
    Playwright scrape returns 0 links (likely because of Cloudflare)."""
    if portal_type not in _HTTPX_EXTRACTORS:
        # No httpx extractor for this portal type — try DuckDuckGo-style
        # generic parsing as a last resort.
        html = await httpx_get(search_url)
        if not html:
            return []
        # Generic: find all <a href> with job-like keywords in the href
        out: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for m in _re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*>([^<]{4,200})</a>',
            html, _re.IGNORECASE,
        ):
            href = m.group(1)
            title = _strip_html(m.group(2))[:200]
            if not title or len(title) < 4:
                continue
            if not any(kw in href.lower() for kw in ["/job", "/vacanc", "/career", "viewjob", "/ofertas", "/offres", "/stellen"]):
                continue
            if href.startswith("/"):
                href = urljoin(search_url, href)
            if href in seen:
                continue
            seen.add(href)
            out.append({"url": href, "title": title, "company": "", "location": "", "summary": ""})
            if len(out) >= 15:
                break
        return out
    html = await httpx_get(search_url)
    if not html:
        return []
    return _HTTPX_EXTRACTORS[portal_type](html, search_url)


async def httpx_duckduckgo_jobs(keyword: str, country_name: str) -> List[Dict[str, str]]:
    """Universal fallback: search DuckDuckGo's HTML endpoint for jobs.
    Query: '<keyword> jobs in <country> site:indeed.com OR site:linkedin.com OR ...'
    Returns a list of job dicts pointing to the original portals."""
    # Build a search query that targets job portals
    sites = ["indeed.com", "linkedin.com/jobs", "glassdoor.com", "jooble.org",
             "talent.com", "careerjet.com", "monster.com", "stepstone.de",
             "bayt.com", "jobstreet.com", "reed.co.uk", "totaljobs.com"]
    site_filter = " OR ".join(f"site:{s}" for s in sites)
    query = f"{keyword} jobs in {country_name} ({site_filter})"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    html = await httpx_get(url)
    if not html:
        return []
    return httpx_extract_duckduckgo(html, url)


async def httpx_linkedin_guest_jobs(keyword: str, country_name: str, limit: int = 25) -> List[Dict[str, str]]:
    """LinkedIn guest API — returns pure HTML fragments, no Cloudflare blocking.
    Endpoint: /jobs-guest/jobs/api/seeMoreJobPostings/search
    Returns up to 25 jobs per call. Each <li> contains <a href="/jobs/view/...">
    with the job title, plus company + location in nearby elements."""
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # Fetch up to `limit` jobs by paginating (start=0, start=25, start=50, ...)
    fetched = 0
    for start in range(0, limit, 25):
        if fetched >= limit:
            break
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={quote_plus(keyword)}&location={quote_plus(country_name)}&start={start}"
        )
        html = await httpx_get(url)
        if not html:
            break
        # Each job is an <li> containing:
        #   <a href="https://.../jobs/view/...">Title</a>
        #   <h4 class="base-search-card__subtitle">Company</h4>
        #   <span class="job-search-card__location">Location</span>
        # Parse all <li> blocks
        # Match: <li ...> ... <a href="(URL)">(title)</a> ... <h4 ...>(company)</h4> ... <span ...location...>(loc)</span> ... </li>
        li_blocks = _re.findall(r"<li[^>]*>(.*?)</li>", html, _re.IGNORECASE | _re.DOTALL)
        if not li_blocks:
            break
        for block in li_blocks:
            # Extract job view URL
            m = _re.search(
                r'<a[^>]+href="(https?://[^"]*/jobs/view/[^"]+)"[^>]*>(.*?)</a>',
                block, _re.IGNORECASE | _re.DOTALL,
            )
            if not m:
                continue
            href = m.group(1)
            title = _strip_html(m.group(2))[:200]
            if not title or len(title) < 3:
                continue
            if href in seen:
                continue
            seen.add(href)
            # Company (h4.base-search-card__subtitle)
            co_m = _re.search(
                r'<h4[^>]*base-search-card__subtitle[^>]*>(.*?)</h4>',
                block, _re.IGNORECASE | _re.DOTALL,
            )
            company = _strip_html(co_m.group(1))[:200] if co_m else ""
            # Location
            loc_m = _re.search(
                r'<span[^>]*job-search-card__location[^>]*>(.*?)</span>',
                block, _re.IGNORECASE | _re.DOTALL,
            )
            location = _strip_html(loc_m.group(1))[:120] if loc_m else ""
            # Also try to grab a snippet/time
            time_m = _re.search(
                r'<time[^>]*datetime="([^"]+)"',
                block, _re.IGNORECASE,
            )
            posted_at = time_m.group(1) if time_m else ""
            out.append({
                "url": href, "title": title, "company": company,
                "location": location, "summary": "", "posted_at": posted_at,
            })
            fetched += 1
            if fetched >= limit:
                break
        # If the page returned fewer than 25 <li> blocks, we've exhausted results
        if len(li_blocks) < 25:
            break
        await asyncio.sleep(0.4)  # polite delay
    return out


_PORTAL_EXTRACTORS = {
    "indeed":      extract_job_links_indeed,
    "linkedin":    extract_job_links_linkedin,
    "jooble":      extract_job_links_jooble,
    "talent":      extract_job_links_talent,
    "jora":        extract_job_links_jora,
    "careerjet":   extract_job_links_careerjet,
    "glassdoor":   extract_job_links_glassdoor,
    "monster":     extract_job_links_monster,
    # Search engine SERP extractors (Google/Bing/Yandex/DDG)
    "google":           extract_job_links_serp,
    "google_local":     extract_job_links_serp,
    "google_jobs":      extract_job_links_serp,
    "google_jobs_ddg":  extract_job_links_serp,
    "bing":             extract_job_links_serp,
    "yandex":           extract_job_links_serp,
    "duckduckgo_local": extract_job_links_serp,
    "duckduckgo_sites": extract_job_links_serp,
    # For all others, fall back to generic
}


async def extract_job_links(portal_type: str, page: Page, base_url: str) -> List[Dict[str, str]]:
    fn = _PORTAL_EXTRACTORS.get(portal_type, extract_job_links_generic)
    try:
        return await fn(page, base_url)
    except Exception as e:
        logger.warning(f"extract_job_links failed for {portal_type}: {e}")
        return []


# ---------------------------------------------------------------------------
# Detail fetcher — opens a job URL, extracts title/company/text, takes screenshot
# ---------------------------------------------------------------------------

async def fetch_job_detail(page: Page, url: str, portal_type: str = "") -> Optional[Dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=settings.PAGE_TIMEOUT_MS)
    except PWTimeout:
        # Try to read what we have
        pass
    except Exception as e:
        logger.debug(f"goto failed for {url}: {e}")
        return None

    # Tiny wait for content
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    shot_path = screenshot_path_for(url)
    try:
        await page.screenshot(path=shot_path, full_page=False)
    except Exception:
        shot_path = ""

    # Try portal-specific extractors first, then generic
    data = await page.evaluate(
        """
        () => {
          const titleSelectors = [
            'h1.jobsearch-JobInfoHeader-title',
            'h1.jobtitle',
            'h1.topcard__title',
            'h1.t-24',
            'h1.job-title',
            'h1',
            'h2.job-title',
            '[data-testid="job-title"]',
            '.job-header h1',
            '.vacancy-title',
            '.ad-title',
          ];
          let title = '';
          for (const s of titleSelectors) {
            const el = document.querySelector(s);
            if (el && el.innerText && el.innerText.trim()) { title = el.innerText.trim(); break; }
          }
          if (!title) title = document.title.slice(0, 200);

          const companySelectors = [
            '[data-company-name]',
            '.jobsearch-CompanyInfoContainer',
            '.company',
            '.topcard__flavor',
            '[data-testid="company-name"]',
            '.job-header .company',
            '.vacancy-company',
            '.employer',
            '[class*="company-name"]',
          ];
          let company = '';
          for (const s of companySelectors) {
            const el = document.querySelector(s);
            if (el && el.innerText && el.innerText.trim()) { company = el.innerText.trim().slice(0, 200); break; }
          }

          const descSelectors = [
            '#jobDescriptionText',
            '.jobsearch-JobComponentDescription',
            '[class*="jobDescription"]',
            '.description__text',
            '.show-more-less-html__markup',
            '.job-body',
            '.job-details',
            '.job-content',
            '.vacancy-text',
            '.ad-description',
            'article',
            'main',
            '[role=main]',
          ];
          let text = '';
          for (const s of descSelectors) {
            const el = document.querySelector(s);
            if (el && el.innerText && el.innerText.trim().length > 100) {
              text = el.innerText.trim();
              break;
            }
          }
          if (!text || text.length < 80) text = document.body.innerText;
          return { title, company, text };
        }
        """
    )

    title = (data.get("title") or "").strip()[:200]
    company = (data.get("company") or "").strip()[:200]
    full_text = (data.get("text") or "")[:6000]

    if not title and not full_text:
        return None

    return {
        "url": url,
        "title": title,
        "company": company,
        "full_text": full_text,
        "screenshot_path": shot_path,
    }


# ---------------------------------------------------------------------------
# Single-portal scrape — fast listing-first strategy
# ---------------------------------------------------------------------------

# URLs that are obviously NOT job detail pages
SKIP_URL_KW = [
    "/login", "/signup", "/register", "/account", "/auth", "/privacy",
    "/terms", "javascript:", "/about", "/contact", "/hire", "/employer",
    "/career/salaries", "/career-guide", "/companies/", "/cmp/", "/review/",
    "/career-advice", "/browse-", "/sitemap", "/feed/", "/messaging/",
    "/help/", "/support", "/faq", "/legal/",
]
# Titles that indicate the page is NOT a job detail (captcha, listing, etc.)
SKIP_TITLE_KW = [
    "additional verification", "are you a robot", "captcha", "verify you are",
    "access denied", "403 forbidden", "page not found", "sign in to", "log in",
    "blocked", "forbidden", "service unavailable", "browse jobs", "all jobs",
    "jobs in united states", "jobs in united kingdom", "jobs in germany",
    "search results", "we couldn't find", "no results",
]


def _is_skip_url(url: str) -> bool:
    url_lower = url.lower()
    if any(kw in url_lower for kw in SKIP_URL_KW):
        return True
    # LinkedIn browse-all-jobs pages like /jobs/{keyword}-jobs
    if "linkedin.com/jobs/" in url_lower:
        path = urlparse(url).path.lower()
        if "/jobs/view/" not in path and "currentjobid" not in url_lower:
            return True
    # Indeed salary/category pages
    if "indeed.com" in url_lower and "/jobs" not in url_lower and "/viewjob" not in url_lower:
        if url_lower.endswith("indeed.com/") or "indeed.com/?" in url_lower:
            return True
    return False


async def scrape_one_portal(portal: Portal, country_code: str, category_key: str,
                            list_page: Page, detail_page: Page,
                            seen_urls: Set[str],
                            user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape one portal for one (country, category). Returns {found, new}.

    Strategy (v3 — fast):
      1. Build listing URL, load it
      2. Extract RICH job data (title, company, summary, location) from the listing page
      3. Save EVERY job immediately using listing-page info as the ad text
      4. Only fetch the detail page for the first MAX_DETAIL_FETCHES_PER_PORTAL jobs
         to upgrade the data (richer description, phone number, screenshot)
      5. Run analyze-and-notify for each newly-inserted job

    The whole thing is wrapped in an asyncio.wait_for with PORTAL_TIMEOUT_SECONDS
    so a single hung portal can't block the entire scan.
    """
    try:
        return await asyncio.wait_for(
            _scrape_one_portal_impl(
                portal, country_code, category_key,
                list_page, detail_page, seen_urls, user_id,
            ),
            timeout=PORTAL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[{country_code}/{portal.name}] TIMEOUT after {PORTAL_TIMEOUT_SECONDS}s — moving on"
        )
        return {"found": 0, "new": 0}
    except Exception as e:
        logger.warning(
            f"[{country_code}/{portal.name}] error: {e} — moving on"
        )
        return {"found": 0, "new": 0}


async def _scrape_one_portal_impl(portal: Portal, country_code: str, category_key: str,
                                   list_page: Page, detail_page: Page,
                                   seen_urls: Set[str],
                                   user_id: Optional[int] = None) -> Dict[str, int]:
    """Actual implementation of scrape_one_portal (without the timeout wrapper)."""
    country = COUNTRIES[country_code]
    keyword = get_keyword(category_key, country_code)
    try:
        search_url = portal.build_url(country_code, keyword, country.name)
    except Exception as e:
        logger.warning(f"build_url failed for {portal.name}({country_code}): {e}")
        return {"found": 0, "new": 0}
    if not search_url:
        return {"found": 0, "new": 0}

    logger.info(f"[{country_code}/{portal.name}] {search_url[:120]}")

    # --- SEARCH-ENGINE OPTIMIZATION ---
    # For search engines (Google/Bing/Yandex/DDG), httpx is FASTER and often
    # MORE RELIABLE than Playwright — Playwright triggers "unusual traffic"
    # captchas on Google and DDG that httpx avoids. So for these portal types,
    # we try httpx FIRST and only fall back to Playwright if httpx returns 0.
    SEARCH_ENGINE_TYPES = {
        "google", "google_local", "google_jobs", "google_jobs_ddg",
        "bing", "yandex",
        "duckduckgo", "duckduckgo_local", "duckduckgo_sites",
    }
    raw_links = []
    if portal.portal_type in SEARCH_ENGINE_TYPES:
        try:
            raw_links = await httpx_scrape_portal(portal.portal_type, search_url)
        except Exception as e:
            logger.warning(f"httpx failed for search engine {portal.name}: {e}")
            raw_links = []
        if raw_links:
            logger.info(f"[{country_code}/{portal.name}] httpx found {len(raw_links)} links — skipping Playwright")
        else:
            logger.info(f"[{country_code}/{portal.name}] httpx returned 0 links — trying Playwright")

    if not raw_links:
        try:
            await list_page.goto(search_url, wait_until="domcontentloaded",
                                 timeout=settings.PAGE_TIMEOUT_MS)
        except PWTimeout:
            logger.warning(f"Timeout loading {portal.name} for {country_code}")
            return {"found": 0, "new": 0}
        except Exception as e:
            logger.warning(f"Error loading {portal.name}: {e}")
            return {"found": 0, "new": 0}

    # Click cookie banner if present (best-effort) — only needed if we loaded
    # the page in Playwright (i.e., we don't already have raw_links from httpx).
    if not raw_links:
        for btn_text in ["Accept", "Accept all", "Got it", "Continue", "I agree", "OK",
                         "Akkoord", "Accepter", "Accepteer", "Zustimmen", "Aceitar", "Aceptar"]:
            try:
                btn = list_page.get_by_role("button", name=btn_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=800)
                    break
            except Exception:
                continue

    # Brief settle for SPA-like pages — only needed if we loaded Playwright
    if not raw_links:
        try:
            await list_page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

    # --- CLOUDFLARE / CHALLENGE DETECTION + AUTO-SOLVE ---
    # Per user instruction: when Cloudflare's "Verify you are human" interstitial
    # appears (a checkbox slightly left of center), click it ONCE and wait
    # silently for ~18 seconds. The page auto-redirects to the real content.
    # We try the auto-solver FIRST; only if it fails do we fall back to httpx.
    #
    # NOTE: if we already have raw_links from the search-engine httpx-first
    # optimization above, skip this entire block — we don't need Playwright.
    if not raw_links:
        challenge_detected = await is_page_challenged(list_page)
        if challenge_detected or await _find_turnstile_iframe(list_page):
            logger.info(f"[{country_code}/{portal.name}] Cloudflare challenge detected — auto-solving (click + 18s wait)")
            solved = await solve_cloudflare_challenge(list_page, max_wait_seconds=18)
            if solved:
                logger.info(f"[{country_code}/{portal.name}] Cloudflare challenge SOLVED — extracting jobs from real page")
                # After solving, extract job links normally
                raw_links = await extract_job_links(portal.portal_type, list_page, search_url)
                if not raw_links:
                    raw_links = await extract_job_links_generic(list_page, search_url)
            else:
                # Auto-solver didn't clear the challenge — fall back to httpx
                logger.info(f"[{country_code}/{portal.name}] Cloudflare not cleared — trying httpx fallback")
                raw_links = await httpx_scrape_portal(portal.portal_type, search_url)
                if not raw_links:
                    logger.info(f"[{country_code}/{portal.name}] httpx fallback also returned 0 links")
                    return {"found": 0, "new": 0}
        else:
            raw_links = await extract_job_links(portal.portal_type, list_page, search_url)
            if not raw_links:
                # Some portals embed results inside iframes; try the generic extractor on the whole document
                raw_links = await extract_job_links_generic(list_page, search_url)

    # --- HTTPX FALLBACK ---
    # If Playwright returned 0 links (but page wasn't a challenge), the
    # portal may have changed its DOM or be partially blocking us. Try httpx.
    if not raw_links:
        logger.info(f"[{country_code}/{portal.name}] Playwright returned 0 links — trying httpx fallback")
        raw_links = await httpx_scrape_portal(portal.portal_type, search_url)

    if not raw_links:
        logger.info(f"[{country_code}/{portal.name}] no job links extracted")
        return {"found": 0, "new": 0}

    found = 0
    new = 0
    detail_tasks: List[Tuple[Dict, str]] = []  # (job_entry, normalized_url)

    for entry in raw_links[:MAX_JOBS_PER_PORTAL]:
        href = entry.get("url") or entry.get("href") or ""
        if not href:
            continue
        if href.startswith("/"):
            url = urljoin(f"https://{urlparse(search_url).netloc}/", href)
        elif href.startswith("http"):
            url = href
        else:
            url = urljoin(search_url, href)

        if _is_skip_url(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Pre-check the title text — if it's obviously a non-job page, skip
        title_text = (entry.get("title") or entry.get("text") or "").lower()
        if any(kw in title_text for kw in SKIP_TITLE_KW):
            continue

        # --- LISTING-FIRST STRATEGY: save the job immediately using listing data ---
        listing_title = (entry.get("title") or entry.get("text") or "").strip()[:200]
        listing_company = (entry.get("company") or "").strip()[:200]
        listing_location = (entry.get("location") or "").strip()[:120]
        listing_summary = (entry.get("summary") or "").strip()[:1000]

        # Compose ad text from whatever the listing gave us
        ad_parts = []
        if listing_title:
            ad_parts.append(f"Title: {listing_title}")
        if listing_company:
            ad_parts.append(f"Company: {listing_company}")
        if listing_location:
            ad_parts.append(f"Location: {listing_location}")
        if listing_summary:
            ad_parts.append(f"Description: {listing_summary}")
        if not ad_parts:
            # No info — skip
            continue
        listing_ad_text = "\n".join(ad_parts)

        found += 1
        job = {
            "url": url,
            "title": listing_title,
            "company": listing_company,
            "full_text": listing_ad_text[:6000],
            "screenshot_path": "",  # will be set later if we fetch detail
            "country_code": country_code,
            "country_name": country.name,
            "category": category_key,
            "portal_name": portal.name,
            "phone_raw": "",
            "phone_normalized": "",
            "ad_summary": "",
            "ad_summary_en": "",
            "rejects_foreigners": False,
            "has_phone": False,
            "posted_at": "",
        }
        is_new = upsert_job(job)
        if is_new:
            new += 1

        # Queue a detail-fetch for the FIRST few new jobs (richness upgrade).
        # Skip for non-new jobs (already have full data).
        if is_new and len(detail_tasks) < MAX_DETAIL_FETCHES_PER_PORTAL:
            detail_tasks.append((entry, url))

    # --- DETAIL UPGRADE: fetch detail pages for the first few jobs ---
    # Run sequentially because Playwright pages aren't concurrency-safe,
    # but limit to MAX_DETAIL_FETCHES_PER_PORTAL so the overall scan is fast.
    for entry, url in detail_tasks:
        # Small randomized delay to be polite
        await asyncio.sleep(random.uniform(0.3, 1.0))
        detail = await fetch_job_detail(detail_page, url, portal.portal_type)
        if not detail:
            continue
        # Post-fetch validation
        detail_title_lower = (detail.get("title") or "").lower()
        if any(kw in detail_title_lower for kw in SKIP_TITLE_KW):
            continue
        if len(detail.get("full_text") or "") < 100:
            continue

        # Update the job in DB with the richer data + screenshot path.
        # If the detail fetch found a phone number etc., it'll be picked up
        # by analyze_and_notify_single below.
        from ..database import get_job_by_url
        existing = get_job_by_url(url)
        if existing:
            # Only overwrite the full_text if the detail version is richer
            existing_text_len = len(existing.get("full_text") or "")
            new_text = detail.get("full_text") or ""
            if len(new_text) > existing_text_len:
                from ..database import update_job_full_text_and_screenshot
                try:
                    update_job_full_text_and_screenshot(
                        existing["id"], new_text, detail.get("screenshot_path") or ""
                    )
                except Exception as e:
                    logger.debug(f"detail upgrade failed for {url}: {e}")

    # --- ANALYZE + NOTIFY for all newly-inserted jobs (concurrent batch) ---
    # Use a smaller concurrency to avoid hitting Gemini rate limits.
    if new > 0:
        # Re-fetch the newly-inserted jobs for this country+category+portal
        # (cheaper than threading state through)
        from ..database import list_recent_jobs_for_portal
        recent_jobs = list_recent_jobs_for_portal(
            country_code=country_code,
            category=category_key,
            portal_name=portal.name,
            limit=new,
        )
        # Cap concurrency to avoid LLM/Telegram rate limits
        semaphore = asyncio.Semaphore(3)
        async def _process(j):
            async with semaphore:
                try:
                    res = await analyze_and_notify_single(
                        country_code,
                        j.get("full_text") or j.get("title") or "",
                        j["url"],
                        user_id=user_id,
                    )
                    logger.info(f"    ↳ {portal.name} realtime {j['url'][:60]}… → {res['status']}")
                except Exception as e:
                    logger.warning(f"    ↳ realtime analyze failed for {j['url']}: {e}")
        await asyncio.gather(*[_process(j) for j in recent_jobs], return_exceptions=True)

    logger.info(f"[{country_code}/{portal.name}] found={found} new={new}")
    return {"found": found, "new": new}


# ---------------------------------------------------------------------------
# Orchestration — for each (country, category), try up to N portals in parallel batches
# ---------------------------------------------------------------------------

async def scrape_country_category(country_code: str, category_key: str,
                                  playwright, user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape one country/category across multiple portals.

    Wrapped in an overall asyncio.wait_for(SCAN_TIMEOUT_SECONDS) so the scan
    ALWAYS finishes — even if every portal hangs. This is critical: previously
    a single country/category scan could run forever (no overall timeout),
    leaving the in-memory _LIVE_SCAN['running']=True forever and showing
    "0 new jobs found" indefinitely in the dashboard.
    """
    scan_id = log_scan_start([country_code], [category_key], user_id=user_id)
    try:
        totals = await asyncio.wait_for(
            _scrape_country_category_impl(
                country_code, category_key, playwright, user_id,
            ),
            timeout=SCAN_TIMEOUT_SECONDS,
        )
        log_scan_finish(scan_id, totals.get("found", 0), totals.get("new", 0))
        return totals
    except asyncio.TimeoutError:
        logger.warning(
            f"[{country_code}/{category_key}] SCAN TIMEOUT after {SCAN_TIMEOUT_SECONDS}s "
            f"— finishing with whatever was found so far"
        )
        # Best-effort: count jobs discovered since the scan started.
        # The per-portal timeouts already wrote jobs to the DB as they came in.
        try:
            from ..database import list_jobs
            jobs = list_jobs(country_codes=[country_code],
                             categories=[category_key], limit=500)
            found = len(jobs)
            new = sum(1 for j in jobs if j.get("discovered_at", "") >=
                      (await _scan_start_iso(scan_id) or ""))
        except Exception:
            found, new = 0, 0
        log_scan_finish(scan_id, found, new, error=f"timeout after {SCAN_TIMEOUT_SECONDS}s")
        return {"found": found, "new": new}
    except Exception as e:
        logger.error(f"Failed {country_code}/{category_key}: {e}")
        log_scan_finish(scan_id, 0, 0, str(e))
        return {"found": 0, "new": 0}


async def _scan_start_iso(scan_id: int) -> str:
    """Return the started_at ISO timestamp for a given scan_id."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT started_at FROM scan_log WHERE id=?", (scan_id,)
            ).fetchone()
            return row["started_at"] if row else ""
    except Exception:
        return ""


async def _scrape_country_category_impl(country_code: str, category_key: str,
                                        playwright, user_id: Optional[int] = None) -> Dict[str, int]:
    """Actual scrape logic (without the overall timeout wrapper)."""
    country = COUNTRIES[country_code]

    browser = await playwright.chromium.launch(headless=settings.HEADLESS)
    context = await browser.new_context(
        user_agent=settings.USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
        java_script_enabled=True,
    )
    await context.route(
        "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4,webm}",
        lambda route: route.abort(),
    )

    try:
        list_page = await context.new_page()
        detail_page = await context.new_page()

        # Pick the portals for this country (capped at MAX_PORTALS_PER_COUNTRY)
        all_portals = portals_for(country_code, country.region)
        portals = all_portals[:MAX_PORTALS_PER_COUNTRY]
        logger.info(f"=== {country_code}/{category_key} → {len(portals)} portals: "
                    f"{[p.name for p in portals]}")

        seen_urls: Set[str] = set()
        totals = {"found": 0, "new": 0}

        for portal in portals:
            try:
                res = await scrape_one_portal(
                    portal, country_code, category_key,
                    list_page, detail_page, seen_urls, user_id
                )
                totals["found"] += res["found"]
                totals["new"] += res["new"]
            except Exception as e:
                logger.error(f"Failed {portal.name} for {country_code}/{category_key}: {e}")

            # Stop early if we've found plenty of jobs
            if totals["found"] >= MAX_PORTALS_PER_COUNTRY * 5:
                logger.info(f"Hit cap of {totals['found']} jobs for {country_code}/{category_key}, stopping early")
                break

        # --- LINKEDIN GUEST API UNIVERSAL FALLBACK ---
        # If ALL portals returned 0 jobs (e.g., all blocked by Cloudflare or
        # the keyword is rare), fall back to LinkedIn's guest API which
        # returns pure HTML fragments without Cloudflare blocking. This is
        # the safety net that guarantees users get at least SOME results.
        if totals["found"] == 0:
            logger.info(f"[{country_code}/{category_key}] All portals returned 0 jobs — trying LinkedIn guest API fallback")
            keyword = get_keyword(category_key, country_code)
            try:
                li_links = await httpx_linkedin_guest_jobs(keyword, country.name, limit=25)
                logger.info(f"[{country_code}/{category_key}] LinkedIn guest API returned {len(li_links)} jobs")
                found = 0
                new = 0
                detail_tasks: List[Tuple[Dict, str]] = []
                for entry in li_links[:MAX_JOBS_PER_PORTAL]:
                    url = entry.get("url") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if _is_skip_url(url):
                        continue
                    title = (entry.get("title") or "").strip()[:200]
                    if not title or len(title) < 4:
                        continue
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in SKIP_TITLE_KW):
                        continue
                    company = (entry.get("company") or "").strip()[:200]
                    location = (entry.get("location") or "").strip()[:120]
                    posted_at = entry.get("posted_at") or ""
                    # Compose ad text from listing data
                    ad_parts = [f"Title: {title}"]
                    if company: ad_parts.append(f"Company: {company}")
                    if location: ad_parts.append(f"Location: {location}")
                    ad_parts.append(f"Source: LinkedIn jobs feed for '{keyword}' in {country.name}")
                    ad_text = "\n".join(ad_parts)
                    job = {
                        "url": url,
                        "title": title,
                        "company": company,
                        "full_text": ad_text[:6000],
                        "screenshot_path": "",
                        "country_code": country_code,
                        "country_name": country.name,
                        "category": category_key,
                        "portal_name": "LinkedIn",
                        "phone_raw": "",
                        "phone_normalized": "",
                        "ad_summary": "",
                        "ad_summary_en": "",
                        "rejects_foreigners": False,
                        "has_phone": False,
                        "posted_at": posted_at,
                    }
                    is_new = upsert_job(job)
                    if is_new:
                        new += 1
                        if len(detail_tasks) < MAX_DETAIL_FETCHES_PER_PORTAL:
                            detail_tasks.append((entry, url))
                    found += 1
                # Fetch detail pages for the first few to enrich the ad text
                for entry, url in detail_tasks:
                    await asyncio.sleep(random.uniform(0.3, 1.0))
                    detail = await fetch_job_detail(detail_page, url, "linkedin")
                    if not detail:
                        continue
                    from ..database import get_job_by_url, update_job_full_text_and_screenshot
                    existing = get_job_by_url(url)
                    if existing and len(detail.get("full_text") or "") > len(existing.get("full_text") or ""):
                        try:
                            update_job_full_text_and_screenshot(
                                existing["id"], detail["full_text"], detail.get("screenshot_path") or ""
                            )
                        except Exception as e:
                            logger.debug(f"LI guest detail upgrade failed for {url}: {e}")
                # Analyze + notify
                if new > 0:
                    from ..database import list_recent_jobs_for_portal
                    recent_jobs = list_recent_jobs_for_portal(
                        country_code=country_code, category=category_key,
                        portal_name="LinkedIn", limit=new,
                    )
                    semaphore = asyncio.Semaphore(3)
                    async def _process(j):
                        async with semaphore:
                            try:
                                res = await analyze_and_notify_single(
                                    country_code,
                                    j.get("full_text") or j.get("title") or "",
                                    j["url"], user_id=user_id,
                                )
                                logger.info(f"    -> LI guest realtime {j['url'][:60]}... -> {res['status']}")
                            except Exception as e:
                                logger.warning(f"    -> LI guest realtime analyze failed: {e}")
                    await asyncio.gather(*[_process(j) for j in recent_jobs], return_exceptions=True)
                totals["found"] += found
                totals["new"] += new
                logger.info(f"[{country_code}/{category_key}] LinkedIn guest API fallback: found={found} new={new}")
            except Exception as e:
                logger.error(f"[{country_code}/{category_key}] LinkedIn guest API fallback failed: {e}")

        # NOTE: log_scan_finish is called by the outer scrape_country_category
        # wrapper (which owns scan_id). This impl just returns the totals.
        return totals
    except Exception as e:
        logger.error(f"Failed {country_code}/{category_key}: {e}")
        return {"found": 0, "new": 0}
    finally:
        await context.close()
        await browser.close()


async def scrape_all(countries: Optional[List[str]] = None,
                     categories: Optional[List[str]] = None,
                     user_id: Optional[int] = None) -> Dict[str, int]:
    countries = countries or list(COUNTRIES.keys())
    categories = categories or list(CATEGORIES.keys())
    totals = {"found": 0, "new": 0}

    async with async_playwright() as pw:
        for cc in countries:
            for cat in categories:
                logger.info(f"=== Scraping {cc} / {cat} ===")
                try:
                    res = await scrape_country_category(cc, cat, pw, user_id)
                    totals["found"] += res["found"]
                    totals["new"] += res["new"]
                except Exception as e:
                    logger.error(f"Failed {cc}/{cat}: {e}")

    logger.info(f"Scan complete. Found={totals['found']} New={totals['new']}")
    return totals
