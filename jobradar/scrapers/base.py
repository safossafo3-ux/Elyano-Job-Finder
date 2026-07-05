"""
Multi-portal job scraper — Phase 4.

For each (country, category) we now query up to 15+ job portals in parallel batches:
  - Indeed (still primary, country-specific subdomain)
  - LinkedIn, Jooble, Jora, Talent.com, CareerJet, Glassdoor, Monster
  - Region-specific portals (StepStone, Xing, Bayt, JobStreet, Reed, Totaljobs, ...)
  - Recruitment agencies (Hays, Michael Page, Adecco, Randstad, Manpower)
  - DuckDuckGo/Google fallbacks

Strategies:
  - Use real browser fingerprint + viewport
  - Block images/fonts to speed up
  - Try to extract job cards from listing pages (each portal type has its own DOM parser)
  - For portals we can't parse, fall back to "discover links" generic extraction
  - Cap jobs per portal (default 15) → up to 15 portals × 15 jobs = 225 candidate jobs per (country, category)
  - Deduplicate by URL across portals
"""

import asyncio
import logging
import os
import random
import string
from typing import List, Dict, Optional, Set
from urllib.parse import quote, quote_plus, urljoin, urlparse

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from ..config import settings, COUNTRIES, CATEGORIES, _default_screenshots_dir, get_keyword
from ..database import (
    upsert_job, log_scan_start, log_scan_finish,
)
from ..pipeline import analyze_and_notify_single
from ..portals import Portal, portals_for, PORTALS

logger = logging.getLogger(__name__)


SCREENSHOTS_DIR = _default_screenshots_dir()
MAX_PORTALS_PER_COUNTRY = int(os.getenv("MAX_PORTALS_PER_COUNTRY", "15"))
MAX_JOBS_PER_PORTAL = int(os.getenv("MAX_JOBS_PER_PORTAL", "15"))


def screenshot_path_for(url: str) -> str:
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    slug = "".join(c if c in string.ascii_letters + string.digits else "_" for c in url)[:120]
    return f"{SCREENSHOTS_DIR}/{slug}.png"


# ---------------------------------------------------------------------------
# Per-portal job link extractors — each returns a list of {url, title} dicts
# ---------------------------------------------------------------------------

async def extract_job_links_generic(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Generic extractor: scan all <a> elements with job-like text/href."""
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
            out.push({url: href, title: text.slice(0, 200)});
            if (out.length >= 30) break;
          }
          return out;
        }
        """,
        base_url,
    )


async def extract_job_links_indeed(page: Page, base_url: str) -> List[Dict[str, str]]:
    """Indeed-specific selectors across multiple versions."""
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
              out.push({href, text: (a.innerText || a.textContent || '').trim().slice(0,200)});
              if (out.length >= 30) break;
            }
            if (out.length >= 30) break;
          }
          return out.map(e => ({url: e.href, title: e.text}));
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
            out.push({url: href, title});
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
            out.push({url: href, title});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


async def extract_job_links_talent(page: Page, base_url: str) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/job/"], a.job-link, .job-card a, h3 a, h2 a')) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const title = (a.innerText || a.textContent || '').trim().slice(0, 200);
            if (title.length < 3) continue;
            out.push({url: href, title});
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
            out.push({url: href, title});
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
            out.push({url: href, title});
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
            out.push({url: href, title});
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
            out.push({url: href, title});
            if (out.length >= 30) break;
          }
          return out;
        }
        """
    )


_PORTAL_EXTRACTORS = {
    "indeed":      extract_job_links_indeed,
    "linkedin":    extract_job_links_linkedin,
    "jooble":      extract_job_links_jooble,
    "talent":      extract_job_links_talent,
    "jora":        extract_job_links_jora,
    "careerjet":   extract_job_links_careerjet,
    "glassdoor":   extract_job_links_glassdoor,
    "monster":     extract_job_links_monster,
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
# Single-portal scrape
# ---------------------------------------------------------------------------

async def scrape_one_portal(portal: Portal, country_code: str, category_key: str,
                            list_page: Page, detail_page: Page,
                            seen_urls: Set[str],
                            user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape one portal for one (country, category). Returns {found, new}."""
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
    try:
        await list_page.goto(search_url, wait_until="domcontentloaded",
                             timeout=settings.PAGE_TIMEOUT_MS)
    except PWTimeout:
        logger.warning(f"Timeout loading {portal.name} for {country_code}")
        return {"found": 0, "new": 0}
    except Exception as e:
        logger.warning(f"Error loading {portal.name}: {e}")
        return {"found": 0, "new": 0}

    # Click cookie banner if present (best-effort)
    for btn_text in ["Accept", "Accept all", "Got it", "Continue", "I agree", "OK",
                     "Akkoord", "Accepter", "Accepteer", "Zustimmen", "Aceitar", "Aceptar"]:
        try:
            btn = list_page.get_by_role("button", name=btn_text).first
            if await btn.count() > 0:
                await btn.click(timeout=800)
                break
        except Exception:
            continue

    # Brief settle for SPA-like pages
    try:
        await list_page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    raw_links = await extract_job_links(portal.portal_type, list_page, search_url)
    if not raw_links:
        # Some portals embed results inside iframes; try the generic extractor on the whole document
        raw_links = await extract_job_links_generic(list_page, search_url)

    found = 0
    new = 0
    # URLs that are obviously NOT job detail pages
    SKIP_URL_KW = [
        "/login", "/signup", "/register", "/account", "/auth", "/privacy",
        "/terms", "javascript:", "/about", "/contact", "/hire", "/employer",
        "/career/salaries", "/career-guide", "/companies/", "/cmp/", "/review/",
        "/career-advice", "/browse-", "/sitemap", "/feed/", "/messaging/",
        "/help/", "/support", "/faq", "/legal/",
        # LinkedIn browse pages (URL ends in "-jobs")
    ]
    # Titles that indicate the page is NOT a job detail (captcha, listing, etc.)
    SKIP_TITLE_KW = [
        "additional verification", "are you a robot", "captcha", "verify you are",
        "access denied", "403 forbidden", "page not found", "sign in to", "log in",
        "blocked", "forbidden", "service unavailable", "browse jobs", "all jobs",
        "jobs in united states", "jobs in united kingdom", "jobs in germany",
        "search results", "we couldn't find", "no results",
    ]
    # Skip LinkedIn browse-all-jobs URLs like "/jobs/{keyword}-jobs"
    def is_linkedin_browse(url: str) -> bool:
        if "linkedin.com/jobs/" not in url.lower():
            return False
        path = urlparse(url).path.lower()
        # Real LinkedIn job URLs look like /jobs/view/{id}/ or /jobs/view/?currentJobId=...
        if "/jobs/view/" in path or "currentjobid" in url.lower():
            return False
        # Anything else (e.g. /jobs/sales-jobs, /jobs/ey-jobs) is a browse page
        return True

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

        url_lower = url.lower()

        # Skip obviously non-job URLs
        if any(kw in url_lower for kw in SKIP_URL_KW):
            continue

        # Skip LinkedIn browse-all-jobs pages
        if is_linkedin_browse(url):
            continue

        # Skip Indeed salary/category pages
        if "indeed.com" in url_lower and "/jobs" not in url_lower and "/viewjob" not in url_lower:
            # Indeed job URLs always contain /jobs or /viewjob
            if url_lower.endswith("indeed.com/") or "indeed.com/?" in url_lower:
                continue

        # Skip if URL was already seen
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Pre-check the title text — if it's obviously a non-job page, skip
        title_text = (entry.get("text") or entry.get("title") or "").lower()
        if any(kw in title_text for kw in SKIP_TITLE_KW):
            continue

        await asyncio.sleep(random.uniform(0.3, 1.0))
        detail = await fetch_job_detail(detail_page, url, portal.portal_type)
        if not detail:
            continue

        # Post-fetch validation: skip pages with anti-bot / not-found titles
        detail_title_lower = (detail.get("title") or "").lower()
        if any(kw in detail_title_lower for kw in SKIP_TITLE_KW):
            logger.debug(f"Skipping non-job page: {url[:80]}")
            continue

        # Skip pages where the body text is suspiciously short (likely captcha/empty)
        if len(detail.get("full_text") or "") < 100:
            continue

        found += 1
        job = {
            **detail,
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
            try:
                res = await analyze_and_notify_single(
                    country_code,
                    detail.get("full_text") or detail.get("title") or "",
                    url,
                    user_id=user_id,
                )
                logger.info(f"    ↳ {portal.name} realtime {url[:60]}… → {res['status']}")
            except Exception as e:
                logger.warning(f"    ↳ realtime analyze failed for {url}: {e}")

    logger.info(f"[{country_code}/{portal.name}] found={found} new={new}")
    return {"found": found, "new": new}


# ---------------------------------------------------------------------------
# Orchestration — for each (country, category), try up to N portals in parallel batches
# ---------------------------------------------------------------------------

async def scrape_country_category(country_code: str, category_key: str,
                                  playwright, user_id: Optional[int] = None) -> Dict[str, int]:
    """Scrape one country/category across multiple portals."""
    country = COUNTRIES[country_code]
    scan_id = log_scan_start([country_code], [category_key], user_id=user_id)

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

        log_scan_finish(scan_id, totals["found"], totals["new"])
        return totals
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
