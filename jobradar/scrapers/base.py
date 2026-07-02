"""
Base scraper using Playwright (async).
Each portal has its own HTML structure, so we use a few generic strategies:

Strategy 1: Look for <a> tags whose href or text matches the search query.
Strategy 2: Look for elements with class/role/id containing "job", "ad", "card".
Strategy 3: If the page returns a JSON blob (some sites do), parse it.

To make this maintainable, each (country, portal) can override CSS selectors
in PORTAL_SELECTORS below. For unknown portals, the generic fallback is used.
"""

import asyncio
import logging
import os
import random
import re
import string
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from ..config import settings, COUNTRIES, CATEGORIES
from ..database import (
    upsert_job, log_scan_start, log_scan_finish, count_jobs
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-portal CSS selectors (refine these as you verify each site)
# ---------------------------------------------------------------------------
# Keys: (country_code, portal_name)
# Fields:
#   job_link:    CSS for the <a> that points to the detail page
#   title:       CSS for the title text (relative to the card, or absolute)
#   company:     CSS for the company name (optional)
#   wait_for:    CSS selector to wait for before scraping (proves page loaded)

PORTAL_SELECTORS: Dict[tuple, Dict[str, str]] = {
    ("RS", "Infostud"): {
        "job_link": "a.job-ad-title, h2.job-ad-title a, a[href*='/posao/']",
        "title": "h1, h2.job-ad-title",
        "company": "a.company-name, .employer-name",
        "wait_for": "a.job-ad-title, .job-ad, .search-results",
    },
    ("RS", "HelloWorld"): {
        "job_link": "a.job-title, .job-card a[href*='/oglas']",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-card, .search-results, .jobs-list",
    },
    ("RS", "Joberty"): {
        "job_link": "a[href*='/posao/'], .job-card a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-card, .list-of-jobs",
    },
    ("BA", "Poslovi.ba"): {
        "job_link": "a.job-title, a[href*='/posao/']",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("BA", "MojPosao"): {
        "job_link": "a[href*='/posao/'], .job-ad-title",
        "title": "h1, .job-ad-title",
        "company": ".company-name",
        "wait_for": ".search-results, .job-list",
    },
    ("ME", "Poslopi"): {
        "job_link": "a[href*='/posao/'], .job-title a",
        "title": "h1, .job-title",
        "company": ".company",
        "wait_for": ".search-results, .job-list",
    },
    ("ME", "Oglasi.me"): {
        "job_link": "a[href*='/posao/'], .ad-title a",
        "title": "h1, .ad-title",
        "company": ".company",
        "wait_for": ".ads, .search-results",
    },
    ("BG", "Jobs.bg"): {
        "job_link": "a[href*='/job/'], .job-card a, .carditated",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results, .cards-container",
    },
    ("BG", "Rabota.bg"): {
        "job_link": "a[href*='/job/'], .job-title a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("BG", "JobOffer.bg"): {
        "job_link": "a[href*='/job/'], .job-card a",
        "title": "h1, h2.entry-title",
        "company": ".company-name",
        "wait_for": ".job-list, .archive",
    },
    ("RO", "EJobs.ro"): {
        "job_link": "a[href*='/locuri-de-munca/'], .job-title a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("RO", "Hipo.ro"): {
        "job_link": "a[href*='/job/'], .job-card a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("RO", "BestJobs"): {
        "job_link": "a[href*='/locuri-de-munca/'], .job-card a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("MK", "Kariera.mk"): {
        "job_link": "a[href*='/job/'], .job-title a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("MK", "Vrabotuvanje"): {
        "job_link": "a[href*='/job/'], .job-ad a",
        "title": "h1, .job-ad-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
    ("LV", "CVmarket.lv"): {
        "job_link": "a[href*='/job/'], .job-title a, a.main-list-link",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .vacancies-list, .search-results",
    },
    ("LV", "CV.lv"): {
        "job_link": "a[href*='/vacancies/'], .job-card a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".vacancies-list, .search-results",
    },
    ("LV", "SS.lv"): {
        "job_link": "a[href*='/msg/'], .msga a",
        "title": "td.msga, h1",
        "company": ".company-name",
        "wait_for": "table, .search-results",
    },
    ("LT", "CVbankas.lt"): {
        "job_link": "a[href*='/'], .job-ad a, a.primary",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, #jobad_list, .search-results",
    },
    ("LT", "CV.lt"): {
        "job_link": "a[href*='/vacancies/'], .job-card a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".vacancies-list, .search-results",
    },
    ("LT", "Darbas.lt"): {
        "job_link": "a[href*='/skelbimas/'], .job-ad a",
        "title": "h1, .job-title",
        "company": ".company-name",
        "wait_for": ".job-list, .search-results",
    },
}

# Fallback generic selectors used when a portal isn't in PORTAL_SELECTORS
GENERIC_SELECTORS = {
    "job_link": (
        "a[href*='job'], a[href*='posao'], a[href*='vacanc'], a[href*='oglas'], "
        "a[href*='/ad/'], a[href*='skelbimas'], .job-card a, .job-ad a, .job-item a"
    ),
    "title": "h1, h2.job-title, .job-title, .ad-title",
    "company": ".company-name, .company, .employer, [class*='company']",
    "wait_for": "body",
}


def get_selectors(country_code: str, portal_name: str) -> Dict[str, str]:
    return PORTAL_SELECTORS.get(
        (country_code, portal_name),
        GENERIC_SELECTORS,
    )


# ---------------------------------------------------------------------------
# Screenshot path helpers
# ---------------------------------------------------------------------------

SCREENSHOTS_DIR = "/home/z/my-project/download/screenshots"


def screenshot_path_for(url: str) -> str:
    """Build a deterministic screenshot path for a URL."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    slug = "".join(c if c in string.ascii_letters + string.digits else "_" for c in url)[:120]
    return f"{SCREENSHOTS_DIR}/{slug}.png"


# ---------------------------------------------------------------------------
# Base Scraper
# ---------------------------------------------------------------------------

class BaseScraper:
    def __init__(self, country_code: str, category_key: str, portal: Dict[str, str]):
        self.country = COUNTRIES[country_code]
        self.category = CATEGORIES[category_key]
        self.portal = portal
        self.selectors = get_selectors(country_code, portal["name"])
        self.keyword = self.category.keywords[country_code]

    def build_search_url(self) -> str:
        return self.portal["search"].format(query=quote(self.keyword))

    async def fetch_job_list(self, page: Page) -> List[str]:
        """Returns list of absolute job detail URLs."""
        url = self.build_search_url()
        logger.info(f"[{self.country.code}/{self.portal['name']}] GET {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=settings.PAGE_TIMEOUT_MS)
        except PWTimeout:
            logger.warning(f"Timeout loading {url}")
            return []

        # Wait for the wait_for selector
        try:
            await page.wait_for_selector(self.selectors["wait_for"], timeout=10_000)
        except PWTimeout:
            logger.warning(f"wait_for selector not found on {url}")

        # Sometimes need a tiny scroll to trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
        await page.wait_for_timeout(800)

        # Accept cookie banner if present (common in EU sites)
        for btn_text in ["Accept", "Prihvati", "Приемам", "Accept all", "Slažem se",
                         "Sunt de acord", "Piekrītu", "Sutinku", "Согласен"]:
            try:
                btn = page.get_by_role("button", name=btn_text).first
                if await btn.count() > 0:
                    await btn.click(timeout=1500)
                    break
            except Exception:
                continue

        # Extract job links
        links = await page.evaluate(
            """
            (sel) => {
              const anchors = Array.from(document.querySelectorAll(sel));
              const seen = new Set();
              const out = [];
              for (const a of anchors) {
                const href = a.href || a.getAttribute('href');
                if (!href) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                out.push(href);
                if (out.length >= 30) break;
              }
              return out;
            }
            """,
            self.selectors["job_link"],
        )

        # Normalize to absolute URLs
        absolute_links = [urljoin(self.portal["base"], l) for l in links]
        return absolute_links[:settings.MAX_JOBS_PER_PORTAL]

    async def fetch_job_detail(self, page: Page, url: str) -> Optional[Dict]:
        """Open a job detail page, extract title, text, take screenshot."""
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=settings.PAGE_TIMEOUT_MS)
        except PWTimeout:
            logger.warning(f"Timeout on detail {url}")
            return None

        try:
            await page.wait_for_selector(self.selectors["title"], timeout=8_000)
        except PWTimeout:
            pass

        # Take screenshot of the visible portion (most ads fit in 1-2 screens)
        shot_path = screenshot_path_for(url)
        try:
            await page.screenshot(path=shot_path, full_page=False)
        except Exception as e:
            logger.warning(f"Screenshot failed for {url}: {e}")
            shot_path = ""

        # Extract title and full text
        data = await page.evaluate(
            """
            () => {
              const titleEl = document.querySelector('h1') ||
                              document.querySelector('h2.job-title') ||
                              document.querySelector('.job-title');
              const title = titleEl ? titleEl.innerText.trim() : '';
              const companyEl = document.querySelector('.company-name, .company, .employer');
              const company = companyEl ? companyEl.innerText.trim() : '';

              // Pull the main content; fall back to body
              const main = document.querySelector('main') ||
                           document.querySelector('[role=main]') ||
                           document.querySelector('.job-details') ||
                           document.querySelector('.job-content') ||
                           document.body;
              const text = main ? main.innerText : '';

              return { title, company, text };
            }
            """,
        )

        # Truncate ad text for LLM
        ad_text = (data.get("text") or "")[:6000]

        return {
            "url": url,
            "title": (data.get("title") or "").strip()[:200],
            "company": (data.get("company") or "").strip()[:200],
            "full_text": ad_text,
            "screenshot_path": shot_path,
        }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def scrape_country_category(
    country_code: str,
    category_key: str,
    playwright,
) -> Dict[str, int]:
    """
    Scrape all portals for one (country, category).
    Returns {"found": N, "new": M}.
    """
    country = COUNTRIES[country_code]
    category = CATEGORIES[category_key]
    total_found = 0
    total_new = 0

    browser = await playwright.chromium.launch(headless=settings.HEADLESS)
    context = await browser.new_context(
        user_agent=settings.USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )
    # Block heavy resources to speed up scraping
    await context.route(
        "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4,webm}",
        lambda route: route.abort(),
    )

    try:
        list_page = await context.new_page()
        detail_page = await context.new_page()

        for portal in country.portals:
            scan_id = log_scan_start(country_code, category_key, portal["name"])
            try:
                scraper = BaseScraper(country_code, category_key, portal)
                links = await scraper.fetch_job_list(list_page)
                logger.info(f"  Found {len(links)} links on {portal['name']}")

                for link in links:
                    # Random short delay to look human
                    await asyncio.sleep(random.uniform(0.4, 1.2))

                    detail = await scraper.fetch_job_detail(detail_page, link)
                    if not detail:
                        continue

                    total_found += 1
                    job = {
                        **detail,
                        "country_code": country_code,
                        "country_name": country.name,
                        "category": category_key,
                        "portal_name": portal["name"],
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
                        total_new += 1

                log_scan_finish(scan_id, len(links), total_new, "")
            except Exception as e:
                logger.error(f"  Error on {portal['name']}: {e}")
                log_scan_finish(scan_id, 0, 0, str(e))
    finally:
        await context.close()
        await browser.close()

    return {"found": total_found, "new": total_new}


async def scrape_all(countries: Optional[List[str]] = None,
                     categories: Optional[List[str]] = None) -> Dict[str, int]:
    """
    Scrape all configured countries × categories.
    """
    countries = countries or list(COUNTRIES.keys())
    categories = categories or list(CATEGORIES.keys())

    totals = {"found": 0, "new": 0}
    async with async_playwright() as pw:
        for cc in countries:
            for cat in categories:
                logger.info(f"=== Scraping {cc} / {cat} ===")
                try:
                    res = await scrape_country_category(cc, cat, pw)
                    totals["found"] += res["found"]
                    totals["new"] += res["new"]
                except Exception as e:
                    logger.error(f"Failed {cc}/{cat}: {e}")

    counts = count_jobs()
    logger.info(f"Scan complete. Found={totals['found']} New={totals['new']} "
                f"DB_total={counts['total']}")
    return totals
