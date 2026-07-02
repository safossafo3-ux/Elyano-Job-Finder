"""
Configuration for JobRadar.
Countries, job portals, keywords, phone country codes, role definitions.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict


# ---------------------------------------------------------------------------
# Countries and their job portals
# Each portal entry: (url, search_path_template)
# search_path_template uses {query} and {page} placeholders.
# ---------------------------------------------------------------------------

@dataclass
class Country:
    code: str               # ISO 2-letter
    tld: str                # .rs, .ba, etc.
    name: str               # English name
    dial_code: str          # +381, +387, etc.
    language: str           # primary language code (for Gemini hints)
    portals: List[Dict[str, str]] = field(default_factory=list)


COUNTRIES: Dict[str, Country] = {
    "RS": Country(
        code="RS", tld=".rs", name="Serbia", dial_code="+381", language="Serbian",
        portals=[
            {"name": "Infostud",       "base": "https://poslovi.infostud.com", "search": "https://poslovi.infostud.com/poslovi/?kw={query}"},
            {"name": "HelloWorld",     "base": "https://www.helloworld.rs",     "search": "https://www.helloworld.rs/oglasi-za-posao?keyword={query}"},
            {"name": "Joberty",        "base": "https://www.joberty.rs",        "search": "https://www.joberty.rs/poslovi?keyword={query}"},
        ],
    ),
    "BA": Country(
        code="BA", tld=".ba", name="Bosnia and Herzegovina", dial_code="+387", language="Bosnian",
        portals=[
            {"name": "Poslovi.ba",     "base": "https://www.poslovi.ba",        "search": "https://www.poslovi.ba/poslovi/?q={query}"},
            {"name": "MojPosao",       "base": "https://www.mojposao.ba",       "search": "https://www.mojposao.ba/pretraga?search={query}"},
        ],
    ),
    "ME": Country(
        code="ME", tld=".me", name="Montenegro", dial_code="+382", language="Montenegrin",
        portals=[
            {"name": "Poslopi",        "base": "https://www.poslopi.me",        "search": "https://www.poslopi.me/pretraga?search={query}"},
            {"name": "Oglasi.me",      "base": "https://www.oglasi.me",         "search": "https://www.oglasi.me/posao?search={query}"},
        ],
    ),
    "BG": Country(
        code="BG", tld=".bg", name="Bulgaria", dial_code="+359", language="Bulgarian",
        portals=[
            {"name": "Jobs.bg",        "base": "https://www.jobs.bg",           "search": "https://www.jobs.bg/job/?keywords={query}"},
            {"name": "Rabota.bg",      "base": "https://www.rabota.bg",         "search": "https://www.rabota.bg/?search={query}"},
            {"name": "JobOffer.bg",    "base": "https://www.joboffer.bg",       "search": "https://www.joboffer.bg/?s={query}"},
        ],
    ),
    "RO": Country(
        code="RO", tld=".ro", name="Romania", dial_code="+40", language="Romanian",
        portals=[
            {"name": "EJobs.ro",       "base": "https://www.ejobs.ro",          "search": "https://www.ejobs.ro/locuri-de-munca/{query}"},
            {"name": "Hipo.ro",        "base": "https://www.hipo.ro",           "search": "https://www.hipo.ro/locuri-de-munca?cauta={query}"},
            {"name": "BestJobs",       "base": "https://www.bestjobs.eu",       "search": "https://www.bestjobs.eu/ro/locuri-de-munca?keyword={query}"},
        ],
    ),
    "MK": Country(
        code="MK", tld=".mk", name="North Macedonia", dial_code="+389", language="Macedonian",
        portals=[
            {"name": "Kariera.mk",     "base": "https://www.kariera.mk",        "search": "https://www.kariera.mk/search?q={query}"},
            {"name": "Vrabotuvanje",   "base": "https://www.vrabotuvanje.com.mk","search": "https://www.vrabotuvanje.com.mk/?search={query}"},
        ],
    ),
    "LV": Country(
        code="LV", tld=".lv", name="Latvia", dial_code="+371", language="Latvian",
        portals=[
            {"name": "CVmarket.lv",    "base": "https://www.cvmarket.lv",       "search": "https://www.cvmarket.lv/darba-piedavajumi?search={query}"},
            {"name": "CV.lv",          "base": "https://www.cv.lv",             "search": "https://www.cv.lt/vacancies?search={query}"},
            {"name": "SS.lv",          "base": "https://www.ss.lv",             "search": "https://www.ss.lv/lv/search/?q={query}"},
        ],
    ),
    "LT": Country(
        code="LT", tld=".lt", name="Lithuania", dial_code="+370", language="Lithuanian",
        portals=[
            {"name": "CVbankas.lt",    "base": "https://www.cvbankas.lt",       "search": "https://www.cvbankas.lt/?padalinys=0&keyw={query}"},
            {"name": "CV.lt",          "base": "https://www.cv.lt",             "search": "https://www.cv.lt/vacancies?search={query}"},
            {"name": "Darbas.lt",      "base": "https://www.darbas.lt",         "search": "https://www.darbas.lt/?search={query}"},
        ],
    ),
}


# ---------------------------------------------------------------------------
# Job categories with multilingual keywords.
# Gemini will translate/normalize these at runtime too, but we pre-seed
# search queries in each country's main language.
# ---------------------------------------------------------------------------

@dataclass
class JobCategory:
    key: str
    english_label: str
    # keyword per country ISO code
    keywords: Dict[str, str]


CATEGORIES: Dict[str, JobCategory] = {
    "courier": JobCategory(
        key="courier",
        english_label="Courier (Glovo/Wolt/Bolt/Tazz)",
        keywords={
            "RS": "kurir glovo wolt bolt",
            "BA": "kurir glovo wolt bolt",
            "ME": "kurir glovo wolt bolt",
            "BG": "куер глауо волт болт",
            "RO": "curier glovo wolt bolt tazz",
            "MK": "курер glovo wolt bolt",
            "LV": "kurjers glovo wolt bolt",
            "LT": "kurjeris glovo wolt bolt",
        },
    ),
    "construction": JobCategory(
        key="construction",
        english_label="Construction worker",
        keywords={
            "RS": "građevinski radnik",
            "BA": "građevinski radnik",
            "ME": "građevinski radnik",
            "BG": "строителен работник",
            "RO": "muncitor constructii",
            "MK": "градежен работник",
            "LV": "būvdarbu strādnieks",
            "LT": "statybos darbuotojas",
        },
    ),
    "factory": JobCategory(
        key="factory",
        english_label="Factory worker",
        keywords={
            "RS": "radnik u fabrici proizvodnja",
            "BA": "radnik u fabrici proizvodnja",
            "ME": "radnik u fabrici proizvodnja",
            "BG": "работник във фабрика производство",
            "RO": "muncitor fabrica productie",
            "MK": "работник во фабрика",
            "LV": "fabrikas strādnieks",
            "LT": "fabrikos darbuotojas gamyba",
        },
    ),
}


# ---------------------------------------------------------------------------
# Settings (loaded from env)
# ---------------------------------------------------------------------------

class Settings:
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/home/z/my-project/jobradar.db")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")   # your personal chat id

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    # Scheduler
    SCAN_CRON_HOURS: str = os.getenv("SCAN_CRON_HOURS", "8,20")   # 8 AM and 8 PM
    SCAN_CRON_TZ: str = os.getenv("SCAN_CRON_TZ", "Africa/Cairo")

    # Scraping
    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
    PAGE_TIMEOUT_MS: int = int(os.getenv("PAGE_TIMEOUT_MS", "30000"))
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    MAX_JOBS_PER_PORTAL: int = int(os.getenv("MAX_JOBS_PER_PORTAL", "20"))

    # Webapp
    WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "0.0.0.0")
    WEBAPP_PORT: int = int(os.getenv("WEBAPP_PORT", "8000"))


settings = Settings()


def all_country_codes() -> List[str]:
    return list(COUNTRIES.keys())


def all_category_keys() -> List[str]:
    return list(CATEGORIES.keys())
