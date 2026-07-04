"""
Universal Indeed scraper — one HTML structure, 40+ countries.
Plus a Google search fallback for countries without Indeed.
Uses Playwright async.
"""

import asyncio
import logging
import os
import random
import string
from typing import List, Dict, Optional
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from ..config import settings, COUNTRIES, CATEGORIES, _default_screenshots_dir, get_keyword
from ..database import (
    upsert_job, log_scan_start, log_scan_finish,
)
from ..pipeline import analyze_and_notify_single

logger = logging.getLogger(__name__)


SCREENSHOTS_DIR = _default_screenshots_dir()


def screenshot_path_for(url: str) -> str:
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    slug = "".join(c if c in string.ascii_letters + string.digits else "_" for c in url)[:120]
    return f"{SCREENSHOTS_DIR}/{slug}.png"


# ---------------------------------------------------------------------------
# Indeed scraper
# ---------------------------------------------------------------------------

async def scrape_indeed(country_code: str, category_key: str,
                        list_page: Page, detail_page: Page,
                        user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape Indeed for one (country, category)."""
    country = COUNTRIES[country_code]
    if not country.indeed_domain:
        return {"found": 0, "new": 0}

    keyword = get_keyword(category_key, country_code)
    search_url = f"https://{country.indeed_domain}/jobs?q={quote(keyword)}"

    logger.info(f"[{country_code}/Indeed] GET {search_url}")
    try:
        await list_page.goto(search_url, wait_until="domcontentloaded",
                             timeout=settings.PAGE_TIMEOUT_MS)
    except PWTimeout:
        logger.warning(f"Timeout loading {search_url}")
        return {"found": 0, "new": 0}

    # Accept cookies if present
    for btn_text in ["Accept", "Accept all", "Got it", "Continue", "I agree"]:
        try:
            btn = list_page.get_by_role("button", name=btn_text).first
            if await btn.count() > 0:
                await btn.click(timeout=1500)
                break
        except Exception:
            continue

    # Indeed job cards: various selectors across versions
    job_links = await list_page.evaluate(
        """
        () => {
          const sels = [
            'a.jcs-JobTitle.css-jcqul8.eu4oa1w0',
            'a[data-jk]',
            'h2.jobTitle a',
            'a.jobTitle',
            'a[id^="job_"]'
          ];
          const seen = new Set();
          const out = [];
          for (const sel of sels) {
            for (const a of document.querySelectorAll(sel)) {
              const href = a.href || a.getAttribute('href');
              if (!href || seen.has(href)) continue;
              seen.add(href);
              out.push({href, text: a.innerText.trim()});
              if (out.length >= 20) break;
            }
            if (out.length >= 20) break;
          }
          return out;
        }
        """
    )

    found = 0
    new = 0
    for entry in job_links[:settings.MAX_JOBS_PER_PORTAL]:
        href = entry["href"]
        # Make absolute
        if href.startswith("/"):
            url = f"https://{country.indeed_domain}{href}"
        elif href.startswith("http"):
            url = href
        else:
            url = urljoin(f"https://{country.indeed_domain}/", href)

        await asyncio.sleep(random.uniform(0.4, 1.2))

        detail = await fetch_indeed_detail(detail_page, url)
        if not detail:
            continue

        found += 1
        job = {
            **detail,
            "country_code": country_code,
            "country_name": country.name,
            "category": category_key,
            "portal_name": "Indeed",
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
            try:
                res = await analyze_and_notify_single(
                    country_code,
                    detail.get("full_text") or detail.get("title") or "",
                    url,
                    user_id=user_id,
                )
                logger.info(f"    ↳ realtime {url[:60]}… → {res['status']}")
            except Exception as e:
                logger.warning(f"    ↳ realtime analyze failed for {url}: {e}")

    return {"found": found, "new": new}


async def fetch_indeed_detail(page: Page, url: str) -> Optional[Dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=settings.PAGE_TIMEOUT_MS)
    except PWTimeout:
        return None

    # Take screenshot
    shot_path = screenshot_path_for(url)
    try:
        await page.screenshot(path=shot_path, full_page=False)
    except Exception:
        shot_path = ""

    data = await page.evaluate(
        """
        () => {
          const titleEl = document.querySelector('h1.jobsearch-JobInfoHeader-title, h1.jobtitle, h1');
          const title = titleEl ? titleEl.innerText.trim() : '';
          const companyEl = document.querySelector('[data-company-name], .company, .jobsearch-CompanyInfoContainer');
          const company = companyEl ? companyEl.innerText.trim() : '';
          const descEl = document.querySelector('#jobDescriptionText, .jobsearch-JobComponentDescription, [class*="jobDescription"]');
          const text = descEl ? descEl.innerText : document.body.innerText;
          return { title, company, text };
        }
        """
    )
    return {
        "url": url,
        "title": (data.get("title") or "").strip()[:200],
        "company": (data.get("company") or "").strip()[:200],
        "full_text": (data.get("text") or "")[:6000],
        "screenshot_path": shot_path,
    }


# ---------------------------------------------------------------------------
# Google search fallback (for countries without Indeed)
# Uses DuckDuckGo HTML endpoint to avoid Google's anti-bot.
# ---------------------------------------------------------------------------

async def scrape_search_engine(country_code: str, category_key: str,
                               list_page: Page, detail_page: Page,
                               user_id: Optional[int] = None) -> Dict[str, int]:
    """DuckDuckGo search for jobs in countries without Indeed."""
    country = COUNTRIES[country_code]
    keyword = get_keyword(category_key, country_code)

    queries = [
        f"{keyword} jobs {country.name} site:linkedin.com/jobs",
        f"{keyword} jobs {country.name}",
        f"{keyword} employment {country.name}",
    ]

    all_links: List[str] = []
    seen = set()

    for q in queries[:2]:  # cap at 2 queries per (country, category)
        url = f"https://html.duckduckgo.com/html/?q={quote(q)}"
        logger.info(f"[{country_code}/DDG] GET {url}")
        try:
            await list_page.goto(url, wait_until="domcontentloaded",
                                 timeout=settings.PAGE_TIMEOUT_MS)
        except PWTimeout:
            continue
        links = await list_page.evaluate(
            """
            () => {
              const out = [];
              for (const a of document.querySelectorAll('a.result__a')) {
                const href = a.href;
                if (href) out.push(href);
              }
              return out;
            }
            """
        )
        for link in links:
            if link in seen:
                continue
            seen.add(link)
            all_links.append(link)
        await asyncio.sleep(random.uniform(1, 2))

    # Filter: only keep links that look like job listings
    job_keywords = ["job", "vacanc", "career", "posao", "empleo", "stelle",
                    "angebot", "offr", "vaga", "praca", "arbete", "tyopaikka",
                    "virka", "töö", "darbs", "pra", " práce"]
    filtered = []
    for link in all_links:
        if any(kw in link.lower() for kw in job_keywords):
            filtered.append(link)
    filtered = filtered[:settings.MAX_JOBS_PER_PORTAL]

    found = 0
    new = 0
    for url in filtered:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        detail = await fetch_generic_detail(detail_page, url)
        if not detail:
            continue
        found += 1
        job = {
            **detail,
            "country_code": country_code,
            "country_name": country.name,
            "category": category_key,
            "portal_name": "Search",
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
            try:
                res = await analyze_and_notify_single(
                    country_code,
                    detail.get("full_text") or detail.get("title") or "",
                    url,
                    user_id=user_id,
                )
                logger.info(f"    ↳ realtime {url[:60]}… → {res['status']}")
            except Exception as e:
                logger.warning(f"    ↳ realtime analyze failed for {url}: {e}")

    return {"found": found, "new": new}


async def fetch_generic_detail(page: Page, url: str) -> Optional[Dict]:
    try:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=settings.PAGE_TIMEOUT_MS)
    except PWTimeout:
        return None

    shot_path = screenshot_path_for(url)
    try:
        await page.screenshot(path=shot_path, full_page=False)
    except Exception:
        shot_path = ""

    data = await page.evaluate(
        """
        () => {
          const titleEl = document.querySelector('h1, h2.job-title, .job-title');
          const title = titleEl ? titleEl.innerText.trim() : document.title;
          const companyEl = document.querySelector('.company-name, .company, [class*="company"]');
          const company = companyEl ? companyEl.innerText.trim() : '';
          const main = document.querySelector('main, [role=main], .job-details, .job-content, article, body');
          const text = main ? main.innerText : '';
          return { title, company, text };
        }
        """
    )
    return {
        "url": url,
        "title": (data.get("title") or "").strip()[:200],
        "company": (data.get("company") or "").strip()[:200],
        "full_text": (data.get("text") or "")[:6000],
        "screenshot_path": shot_path,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def scrape_country_category(country_code: str, category_key: str,
                                  playwright, user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape one country/category using Indeed (if available) or search engine."""
    country = COUNTRIES[country_code]
    scan_id = log_scan_start([country_code], [category_key], user_id=user_id)

    browser = await playwright.chromium.launch(headless=settings.HEADLESS)
    context = await browser.new_context(
        user_agent=settings.USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )
    await context.route(
        "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4,webm}",
        lambda route: route.abort(),
    )

    try:
        list_page = await context.new_page()
        detail_page = await context.new_page()

        if country.indeed_domain:
            res = await scrape_indeed(country_code, category_key,
                                       list_page, detail_page, user_id)
        else:
            res = await scrape_search_engine(country_code, category_key,
                                              list_page, detail_page, user_id)

        log_scan_finish(scan_id, res["found"], res["new"])
        return res
    except Exception as e:
        logger.error(f"Failed {country_code}/{category_key}: {e}")
        log_scan_finish(scan_id, 0, 0, str(e))
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
