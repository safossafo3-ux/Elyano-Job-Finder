"""
Configuration for JobRadar.
Countries (EU + Balkans), job portals, keywords, phone country codes, role definitions.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict

# Load .env file if present (must happen BEFORE reading os.getenv)
try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, ".env"), override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Countries and their job portals
# Each portal entry: {"name": ..., "base": ..., "search": "url with {query}"}
# ---------------------------------------------------------------------------

@dataclass
class Country:
    code: str               # ISO 2-letter
    tld: str                # .rs, .ba, etc.
    name: str               # English name
    dial_code: str          # +381, +387, etc.
    language: str           # primary language (hint for Gemini)
    in_eu: bool             # True if EU member state
    portals: List[Dict[str, str]] = field(default_factory=list)


COUNTRIES: Dict[str, Country] = {
    # ===== Original Balkan non-EU countries (kept, useful for courier jobs) =====
    "RS": Country(
        code="RS", tld=".rs", name="Serbia", dial_code="+381", language="Serbian", in_eu=False,
        portals=[
            {"name": "Infostud",   "base": "https://poslovi.infostud.com", "search": "https://poslovi.infostud.com/poslovi/?kw={query}"},
            {"name": "HelloWorld", "base": "https://www.helloworld.rs",     "search": "https://www.helloworld.rs/oglasi-za-posao?keyword={query}"},
            {"name": "Joberty",    "base": "https://www.joberty.rs",        "search": "https://www.joberty.rs/poslovi?keyword={query}"},
        ],
    ),
    "BA": Country(
        code="BA", tld=".ba", name="Bosnia and Herzegovina", dial_code="+387", language="Bosnian", in_eu=False,
        portals=[
            {"name": "Poslovi.ba", "base": "https://www.poslovi.ba",  "search": "https://www.poslovi.ba/poslovi/?q={query}"},
            {"name": "MojPosao",   "base": "https://www.mojposao.ba", "search": "https://www.mojposao.ba/pretraga?search={query}"},
        ],
    ),
    "ME": Country(
        code="ME", tld=".me", name="Montenegro", dial_code="+382", language="Montenegrin", in_eu=False,
        portals=[
            {"name": "Poslopi",   "base": "https://www.poslopi.me", "search": "https://www.poslopi.me/pretraga?search={query}"},
            {"name": "Oglasi.me", "base": "https://www.oglasi.me",  "search": "https://www.oglasi.me/posao?search={query}"},
        ],
    ),
    "MK": Country(
        code="MK", tld=".mk", name="North Macedonia", dial_code="+389", language="Macedonian", in_eu=False,
        portals=[
            {"name": "Kariera.mk",     "base": "https://www.kariera.mk",          "search": "https://www.kariera.mk/search?q={query}"},
            {"name": "Vrabotuvanje",   "base": "https://www.vrabotuvanje.com.mk", "search": "https://www.vrabotuvanje.com.mk/?search={query}"},
        ],
    ),

    # ===== Original EU countries =====
    "BG": Country(
        code="BG", tld=".bg", name="Bulgaria", dial_code="+359", language="Bulgarian", in_eu=True,
        portals=[
            {"name": "Jobs.bg",     "base": "https://www.jobs.bg",       "search": "https://www.jobs.bg/job/?keywords={query}"},
            {"name": "Rabota.bg",   "base": "https://www.rabota.bg",     "search": "https://www.rabota.bg/?search={query}"},
            {"name": "JobOffer.bg", "base": "https://www.joboffer.bg",   "search": "https://www.joboffer.bg/?s={query}"},
        ],
    ),
    "RO": Country(
        code="RO", tld=".ro", name="Romania", dial_code="+40", language="Romanian", in_eu=True,
        portals=[
            {"name": "EJobs.ro",   "base": "https://www.ejobs.ro",    "search": "https://www.ejobs.ro/locuri-de-munca/{query}"},
            {"name": "Hipo.ro",    "base": "https://www.hipo.ro",     "search": "https://www.hipo.ro/locuri-de-munca?cauta={query}"},
            {"name": "BestJobs",   "base": "https://www.bestjobs.eu", "search": "https://www.bestjobs.eu/ro/locuri-de-munca?keyword={query}"},
        ],
    ),
    "LV": Country(
        code="LV", tld=".lv", name="Latvia", dial_code="+371", language="Latvian", in_eu=True,
        portals=[
            {"name": "CVmarket.lv", "base": "https://www.cvmarket.lv", "search": "https://www.cvmarket.lv/darba-piedavajumi?search={query}"},
            {"name": "CV.lv",       "base": "https://www.cv.lv",       "search": "https://www.cv.lt/vacancies?search={query}"},
            {"name": "SS.lv",       "base": "https://www.ss.lv",       "search": "https://www.ss.lv/lv/search/?q={query}"},
        ],
    ),
    "LT": Country(
        code="LT", tld=".lt", name="Lithuania", dial_code="+370", language="Lithuanian", in_eu=True,
        portals=[
            {"name": "CVbankas.lt", "base": "https://www.cvbankas.lt", "search": "https://www.cvbankas.lt/?padalinys=0&keyw={query}"},
            {"name": "CV.lt",       "base": "https://www.cv.lt",       "search": "https://www.cv.lt/vacancies?search={query}"},
            {"name": "Darbas.lt",   "base": "https://www.darbas.lt",   "search": "https://www.darbas.lt/?search={query}"},
        ],
    ),

    # ===== New: remaining 23 EU member states =====
    "AT": Country(
        code="AT", tld=".at", name="Austria", dial_code="+43", language="German", in_eu=True,
        portals=[
            {"name": "Karriere.at", "base": "https://www.karriere.at", "search": "https://www.karriere.at/jobs/{query}"},
            {"name": "Jobs.at",     "base": "https://www.jobs.at",     "search": "https://www.jobs.at/jobs?q={query}"},
        ],
    ),
    "BE": Country(
        code="BE", tld=".be", name="Belgium", dial_code="+32", language="French", in_eu=True,
        portals=[
            {"name": "StepStone.be", "base": "https://www.stepstone.be", "search": "https://www.stepstone.be/jobs/{query}"},
            {"name": "VDAB",         "base": "https://www.vdab.be",      "search": "https://www.vdab.be/vacatures?zoekterm={query}"},
        ],
    ),
    "HR": Country(
        code="HR", tld=".hr", name="Croatia", dial_code="+385", language="Croatian", in_eu=True,
        portals=[
            {"name": "MojPosao", "base": "https://www.moj-posao.net", "search": "https://www.moj-posao.net/Pretraga?search={query}"},
            {"name": "Posao.hr", "base": "https://www.posao.hr",      "search": "https://www.posao.hr/poslovi/?q={query}"},
        ],
    ),
    "CY": Country(
        code="CY", tld=".cy", name="Cyprus", dial_code="+357", language="English", in_eu=True,
        portals=[
            {"name": "CyprusJobs",  "base": "https://www.cyprusjobs.com",  "search": "https://www.cyprusjobs.com/jobs/?q={query}"},
            {"name": "Jobs.com.cy", "base": "https://www.jobs.com.cy",     "search": "https://www.jobs.com.cy/?s={query}"},
        ],
    ),
    "CZ": Country(
        code="CZ", tld=".cz", name="Czech Republic", dial_code="+420", language="Czech", in_eu=True,
        portals=[
            {"name": "Jobs.cz", "base": "https://www.jobs.cz", "search": "https://www.jobs.cz/{query}/"},
            {"name": "Prace.cz","base": "https://www.prace.cz", "search": "https://www.prace.cz/hledat/?search={query}"},
        ],
    ),
    "DK": Country(
        code="DK", tld=".dk", name="Denmark", dial_code="+45", language="Danish", in_eu=True,
        portals=[
            {"name": "Jobnet",      "base": "https://job.jobnet.dk",     "search": "https://job.jobnet.dk/?search={query}"},
            {"name": "StepStone.dk","base": "https://www.stepstone.dk",  "search": "https://www.stepstone.dk/jobs/{query}"},
        ],
    ),
    "EE": Country(
        code="EE", tld=".ee", name="Estonia", dial_code="+372", language="Estonian", in_eu=True,
        portals=[
            {"name": "CVkeskus", "base": "https://www.cvkeskus.ee", "search": "https://www.cvkeskus.ee/toopakkumised?search={query}"},
            {"name": "CV.ee",    "base": "https://www.cv.ee",       "search": "https://www.cv.ee/jobs?search={query}"},
        ],
    ),
    "FI": Country(
        code="FI", tld=".fi", name="Finland", dial_code="+358", language="Finnish", in_eu=True,
        portals=[
            {"name": "Oikotie",    "base": "https://tyopaikat.oikotie.fi", "search": "https://tyopaikat.oikotie.fi/tyopaikat?hakusana={query}"},
            {"name": "Duunitori",  "base": "https://duunitori.fi",         "search": "https://duunitori.fi/tyopaikat?haku={query}"},
        ],
    ),
    "FR": Country(
        code="FR", tld=".fr", name="France", dial_code="+33", language="French", in_eu=True,
        portals=[
            {"name": "Indeed.fr",      "base": "https://fr.indeed.com",            "search": "https://fr.indeed.com/jobs?q={query}"},
            {"name": "FranceTravail",  "base": "https://candidat.francetravail.fr","search": "https://candidat.francetravail.fr/recherche/{query}"},
        ],
    ),
    "DE": Country(
        code="DE", tld=".de", name="Germany", dial_code="+49", language="German", in_eu=True,
        portals=[
            {"name": "StepStone.de",      "base": "https://www.stepstone.de",         "search": "https://www.stepstone.de/jobs/{query}"},
            {"name": "Indeed.de",         "base": "https://de.indeed.com",            "search": "https://de.indeed.com/jobs?q={query}"},
            {"name": "Arbeitsagentur",    "base": "https://www.arbeitsagentur.de",    "search": "https://www.arbeitsagentur.de/jobsuche/suche?was={query}"},
        ],
    ),
    "GR": Country(
        code="GR", tld=".gr", name="Greece", dial_code="+30", language="Greek", in_eu=True,
        portals=[
            {"name": "Kariera.gr",      "base": "https://www.kariera.gr",         "search": "https://www.kariera.gr/search?q={query}"},
            {"name": "XrisiEukairia",   "base": "https://www.xrisieukairia.gr",   "search": "https://www.xrisieukairia.gr/?s={query}"},
        ],
    ),
    "HU": Country(
        code="HU", tld=".hu", name="Hungary", dial_code="+36", language="Hungarian", in_eu=True,
        portals=[
            {"name": "Profession.hu", "base": "https://www.profession.hu",   "search": "https://www.profession.hu/allasok/{query}"},
            {"name": "Jofogas",       "base": "https://allas.jofogas.hu",    "search": "https://allas.jofogas.hu/magyarorszag/{query}"},
        ],
    ),
    "IE": Country(
        code="IE", tld=".ie", name="Ireland", dial_code="+353", language="English", in_eu=True,
        portals=[
            {"name": "Indeed.ie",   "base": "https://ie.indeed.com",     "search": "https://ie.indeed.com/jobs?q={query}"},
            {"name": "IrishJobs",   "base": "https://www.irishjobs.ie",  "search": "https://www.irishjobs.ie/jobs/{query}"},
        ],
    ),
    "IT": Country(
        code="IT", tld=".it", name="Italy", dial_code="+39", language="Italian", in_eu=True,
        portals=[
            {"name": "Indeed.it",  "base": "https://it.indeed.com",   "search": "https://it.indeed.com/jobs?q={query}"},
            {"name": "InfoJobs",   "base": "https://www.infojobs.it", "search": "https://www.infojobs.it/empleo?palabras={query}"},
        ],
    ),
    "LU": Country(
        code="LU", tld=".lu", name="Luxembourg", dial_code="+352", language="French", in_eu=True,
        portals=[
            {"name": "Jobs.lu",   "base": "https://www.jobs.lu",    "search": "https://www.jobs.lu/?q={query}"},
            {"name": "Monster.lu","base": "https://www.monster.lu", "search": "https://www.monster.lu/jobs/?q={query}"},
        ],
    ),
    "MT": Country(
        code="MT", tld=".mt", name="Malta", dial_code="+356", language="English", in_eu=True,
        portals=[
            {"name": "JobsinMalta",     "base": "https://jobsinmalta.com",       "search": "https://jobsinmalta.com/jobs?q={query}"},
            {"name": "KeepMeposted",    "base": "https://keepmeposted.com",      "search": "https://keepmeposted.com/jobs?q={query}"},
        ],
    ),
    "NL": Country(
        code="NL", tld=".nl", name="Netherlands", dial_code="+31", language="Dutch", in_eu=True,
        portals=[
            {"name": "Indeed.nl",               "base": "https://nl.indeed.com",                       "search": "https://nl.indeed.com/jobs?q={query}"},
            {"name": "NationaleVacaturebank",   "base": "https://www.nationalevacaturebank.nl",       "search": "https://www.nationalevacaturebank.nl/vacatures/{query}"},
        ],
    ),
    "PL": Country(
        code="PL", tld=".pl", name="Poland", dial_code="+48", language="Polish", in_eu=True,
        portals=[
            {"name": "Pracuj.pl", "base": "https://www.pracuj.pl", "search": "https://www.pracuj.pl/praca/{query}"},
            {"name": "OLX",       "base": "https://www.olx.pl",    "search": "https://www.olx.pl/praca/q-{query}/"},
        ],
    ),
    "PT": Country(
        code="PT", tld=".pt", name="Portugal", dial_code="+351", language="Portuguese", in_eu=True,
        portals=[
            {"name": "NetEmpregos", "base": "https://www.net-empregos.com", "search": "https://www.net-empregos.com/pesquisa/{query}"},
            {"name": "Indeed.pt",   "base": "https://pt.indeed.com",        "search": "https://pt.indeed.com/jobs?q={query}"},
        ],
    ),
    "SK": Country(
        code="SK", tld=".sk", name="Slovakia", dial_code="+421", language="Slovak", in_eu=True,
        portals=[
            {"name": "Profesia.sk",    "base": "https://www.profesia.sk",       "search": "https://www.profesia.sk/praca/?search={query}"},
            {"name": "PonukaPrace",    "base": "https://www.ponukaprace.sk",    "search": "https://www.ponukaprace.sk/?search={query}"},
        ],
    ),
    "SI": Country(
        code="SI", tld=".si", name="Slovenia", dial_code="+386", language="Slovenian", in_eu=True,
        portals=[
            {"name": "MojDelo", "base": "https://www.mojdelo.com", "search": "https://www.mojdelo.com/iskanje-del?q={query}"},
            {"name": "Optius",  "base": "https://www.optius.com",  "search": "https://www.optius.com/?search={query}"},
        ],
    ),
    "ES": Country(
        code="ES", tld=".es", name="Spain", dial_code="+34", language="Spanish", in_eu=True,
        portals=[
            {"name": "InfoJobs.es", "base": "https://www.infojobs.net", "search": "https://www.infojobs.net/empleo?palabras={query}"},
            {"name": "Indeed.es",   "base": "https://es.indeed.com",    "search": "https://es.indeed.com/jobs?q={query}"},
        ],
    ),
    "SE": Country(
        code="SE", tld=".se", name="Sweden", dial_code="+46", language="Swedish", in_eu=True,
        portals=[
            {"name": "Arbetsförmedlingen", "base": "https://www.arbetsformedlingen.se", "search": "https://www.arbetsformedlingen.se/sok?query={query}"},
            {"name": "StepStone.se",       "base": "https://www.stepstone.se",          "search": "https://www.stepstone.se/jobs/{query}"},
        ],
    ),
}


# ---------------------------------------------------------------------------
# Job categories with multilingual keywords.
# If a country isn't in the dict, falls back to English (works for IE/MT/CY).
# ---------------------------------------------------------------------------

@dataclass
class JobCategory:
    key: str
    english_label: str
    keywords: Dict[str, str]  # keyword per country ISO code


CATEGORIES: Dict[str, JobCategory] = {
    "courier": JobCategory(
        key="courier",
        english_label="Courier (Glovo/Wolt/Bolt/Tazz)",
        keywords={
            "RS": "kurir glovo wolt bolt",
            "BA": "kurir glovo wolt bolt",
            "ME": "kurir glovo wolt bolt",
            "MK": "курер glovo wolt bolt",
            "BG": "куер glovo wolt bolt",
            "RO": "curier glovo wolt bolt tazz",
            "LV": "kurjers glovo wolt bolt",
            "LT": "kurjeris glovo wolt bolt",
            "AT": "Kurierfahrer glovo wolt bolt",
            "BE": "coursier koerier glovo wolt bolt",
            "HR": "kurir glovo wolt bolt",
            "CY": "courier glovo wolt bolt",
            "CZ": "kurýr glovo wolt bolt",
            "DK": "bud chauffor glovo wolt bolt",
            "EE": "kuller glovo wolt bolt",
            "FI": "kuriiri glovo wolt bolt",
            "FR": "coursier glovo wolt bolt",
            "DE": "Kurierfahrer glovo wolt bolt",
            "GR": "διανομέας glovo wolt bolt",
            "HU": "futár glovo wolt bolt",
            "IE": "courier glovo wolt bolt",
            "IT": "corriere glovo wolt bolt",
            "LU": "coursier glovo wolt bolt",
            "MT": "courier glovo wolt bolt",
            "NL": "koerier glovo wolt bolt",
            "PL": "kurier glovo wolt bolt",
            "PT": "estafeta glovo wolt bolt",
            "SK": "kuriér glovo wolt bolt",
            "SI": "kurir glovo wolt bolt",
            "ES": "repartidor glovo wolt bolt",
            "SE": "budförare glovo wolt bolt",
        },
    ),
    "construction": JobCategory(
        key="construction",
        english_label="Construction worker",
        keywords={
            "RS": "građevinski radnik",
            "BA": "građevinski radnik",
            "ME": "građevinski radnik",
            "MK": "градежен работник",
            "BG": "строителен работник",
            "RO": "muncitor constructii",
            "LV": "būvdarbu strādnieks",
            "LT": "statybos darbuotojas",
            "AT": "Bauarbeiter",
            "BE": "ouvrier du bâtiment bouwvakker",
            "HR": "građevinski radnik",
            "CY": "construction worker",
            "CZ": "stavební dělník",
            "DK": "byggearbejder",
            "EE": "ehitustööline",
            "FI": "rakennustyöntekijä",
            "FR": "ouvrier du bâtiment",
            "DE": "Bauarbeiter",
            "GR": "εργάτης κατασκευών",
            "HU": "építőipari munkás",
            "IE": "construction worker",
            "IT": "operaio edile",
            "LU": "ouvrier du bâtiment",
            "MT": "construction worker",
            "NL": "bouwvakker",
            "PL": "pracownik budowlany",
            "PT": "trabalhador da construção",
            "SK": "stavebný robotník",
            "SI": "gradbeni delavec",
            "ES": "obrero de construcción",
            "SE": "byggnadsarbetare",
        },
    ),
    "factory": JobCategory(
        key="factory",
        english_label="Factory worker",
        keywords={
            "RS": "radnik u fabrici proizvodnja",
            "BA": "radnik u fabrici proizvodnja",
            "ME": "radnik u fabrici proizvodnja",
            "MK": "работник во фабрика",
            "BG": "работник във фабрика производство",
            "RO": "muncitor fabrica productie",
            "LV": "fabrikas strādnieks",
            "LT": "fabrikos darbuotojas gamyba",
            "AT": "Fabrikarbeiter Produktion",
            "BE": "ouvrier d'usine fabrieksarbeider",
            "HR": "radnik u tvornici proizvodnja",
            "CY": "factory worker",
            "CZ": "tovární dělník",
            "DK": "fabriksarbejder",
            "EE": "tehasetööline",
            "FI": "tehdastyöntekijä",
            "FR": "ouvrier d'usine",
            "DE": "Fabrikarbeiter Produktion",
            "GR": "εργάτης εργοστασίου",
            "HU": "gyári munkás",
            "IE": "factory worker",
            "IT": "operaio di fabbrica",
            "LU": "ouvrier d'usine",
            "MT": "factory worker",
            "NL": "fabrieksarbeider",
            "PL": "pracownik fabryczny produkcja",
            "PT": "trabalhador de fábrica",
            "SK": "továrenský robotník",
            "SI": "tovarniški delavec",
            "ES": "obrero de fábrica",
            "SE": "fabriksarbetare",
        },
    ),
}


# ---------------------------------------------------------------------------
# Settings (loaded from env)
# ---------------------------------------------------------------------------

def _project_root() -> str:
    """Return the directory that contains the `jobradar` package folder."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_db_path() -> str:
    return os.path.join(_project_root(), "jobradar.db")


def _default_screenshots_dir() -> str:
    return os.path.join(_project_root(), "download", "screenshots")


class Settings:
    # Database (legacy SQLite path — kept only as a fallback reference)
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", _default_db_path())

    # MySQL (now the primary datastore — see database.py)
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))  # MySQL default port
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "jobradar")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Scheduler
    SCAN_CRON_HOURS: str = os.getenv("SCAN_CRON_HOURS", "8,20")
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

    # Real-time notification: send Telegram the instant a new job is found.
    REALTIME_NOTIFY: bool = os.getenv("REALTIME_NOTIFY", "true").lower() == "true"

    # Webapp
    WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "0.0.0.0")
    WEBAPP_PORT: int = int(os.getenv("WEBAPP_PORT", "8000"))


settings = Settings()


def all_country_codes() -> List[str]:
    return list(COUNTRIES.keys())


def eu_country_codes() -> List[str]:
    return [c for c, v in COUNTRIES.items() if v.in_eu]


def all_category_keys() -> List[str]:
    return list(CATEGORIES.keys())
