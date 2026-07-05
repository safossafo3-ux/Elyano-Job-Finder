"""
Job portals registry — Phase 4 multi-source.

Each portal defines:
  - name           Human-readable name (e.g. "LinkedIn")
  - build_url(country, keyword)  -> URL to scrape (job listing page)
  - portal_type    One of: 'indeed', 'linkedin', 'glassdoor', 'jooble',
                             'jora', 'talent', 'careerjet', 'monster',
                             'stepstone', 'xing', 'bayt', 'jobstreet',
                             'reed', 'totaljobs', 'indeed_region'
  - regions        Where this portal works (None = all)
  - countries      Specific country codes (None = all in regions)
  - weight         Priority (lower = tried first)

For 80+ countries we typically use:
  1. Indeed (country-specific subdomain if available, else region)
  2. LinkedIn Jobs (works globally)
  3. Jooble (works in 60+ countries)
  4. Jora (works in 30+ countries)
  5. Talent.com (works in 30+ countries)
  6. CareerJet (works in 90+ countries)
  7. Glassdoor (works in 10+ countries)
  8. Monster (works in 40+ countries, regional domains)
  9. Region-specific portals (Xing, StepStone, Bayt, JobStreet, Reed, TotalJobs)

For each (country, category) we aim to query AT LEAST 15 portals in parallel/sequential.
"""

from typing import List, Dict, Optional, Callable
from urllib.parse import quote_plus


# ---------------------------------------------------------------------------
# Portal definition
# ---------------------------------------------------------------------------

class Portal:
    __slots__ = ("name", "portal_type", "build_url", "regions", "countries", "weight", "needs_js")

    def __init__(self, name: str, portal_type: str, build_url: Callable[[str, str, str], str],
                 regions: Optional[List[str]] = None, countries: Optional[List[str]] = None,
                 weight: int = 50, needs_js: bool = True):
        self.name = name
        self.portal_type = portal_type
        self.build_url = build_url
        self.regions = regions                 # None = all regions
        self.countries = countries             # None = all countries (intersected with regions)
        self.weight = weight                   # lower = tried first
        self.needs_js = needs_js

    def applies_to(self, country_code: str, region: str) -> bool:
        if self.countries is not None:
            return country_code.upper() in [c.upper() for c in self.countries]
        if self.regions is not None:
            return region in self.regions
        return True


# ---------------------------------------------------------------------------
# URL builders for each portal type
# Each takes (country_code, keyword, country_name) and returns a search URL.
# ---------------------------------------------------------------------------

def _indeed_url(country_code: str, keyword: str, country_name: str) -> str:
    """Indeed uses country-specific subdomains (ro.indeed.com, de.indeed.com, etc.)."""
    from .config import COUNTRIES
    country = COUNTRIES.get(country_code.upper())
    domain = country.indeed_domain if country else "www.indeed.com"
    return f"https://{domain}/jobs?q={quote_plus(keyword)}&limit=25"


def _linkedin_url(country_code: str, keyword: str, country_name: str) -> str:
    """LinkedIn Jobs global search with location filter."""
    loc = quote_plus(country_name)
    kw = quote_plus(keyword)
    return f"https://www.linkedin.com/jobs/search/?keywords={kw}&location={loc}"


def _jooble_url(country_code: str, keyword: str, country_name: str) -> str:
    """Jooble — supports country subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    # Jooble has subdomains like ua.jooble.org, ro.jooble.org, etc.
    # Fallback to the main domain with location query.
    subdomain_supported = {
        "ua","ro","pl","de","fr","es","it","nl","be","cz","sk","hu","gr","bg","rs",
        "hr","si","ee","lv","lt","se","no","fi","dk","at","ch","pt","ie","uk","us",
        "ca","mx","br","ar","cl","co","pe","au","nz","za","in","pk","bd","ph","my",
        "sg","id","th","vn","jp","kr","cn","hk","tw","ae","sa","qa","kw","bh","om",
        "il","jo","eg","ma","tn","ng","ke","gh","ru","by","md","ge","am"
    }
    if cc in subdomain_supported:
        return f"https://{cc}.jooble.org/SearchResult?p=1&ukw={kw}"
    return f"https://jooble.org/SearchResult?p=1&ukw={kw}&location={quote_plus(country_name)}"


def _jora_url(country_code: str, keyword: str, country_name: str) -> str:
    """Jora — country-specific subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    supported = {
        "au","nz","us","ca","uk","ie","in","sg","my","ph","id","th","vn","hk","jp",
        "za","ng","ke","gh","eg","ma","ae","sa","qa","kw","bh","om","il","de","fr",
        "es","it","nl","be","at","ch","se","no","dk","fi","pl","cz","hu","ro","gr",
        "pt","br","mx","ar","cl","co","pe"
    }
    if cc in supported:
        return f"https://{cc}.jora.com/jobs?q={kw}&l={loc}"
    return f"https://jora.com/jobs?q={kw}&l={loc}"


def _talent_url(country_code: str, keyword: str, country_name: str) -> str:
    """Talent.com — country-specific subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    supported = {
        "us","ca","uk","ie","au","nz","de","fr","es","it","nl","be","at","ch","se",
        "no","dk","fi","pl","cz","hu","ro","gr","pt","br","mx","ar","cl","co","pe",
        "za","ng","ke","ae","sa","qa","om","in","sg","my","ph","id","th","vn","hk",
        "jp","kr","cn","eg","ma","tn","ru","ua"
    }
    if cc in supported:
        return f"https://{cc}.talent.com/jobs?k={kw}&l={loc}"
    return f"https://www.talent.com/jobs?k={kw}&l={loc}"


def _careerjet_url(country_code: str, keyword: str, country_name: str) -> str:
    """CareerJet — country-specific subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    # CareerJet uses country subdomains
    supported = {
        "us","ca","uk","ie","au","nz","de","at","ch","fr","be","es","pt","it","nl",
        "lu","se","no","dk","fi","is","pl","cz","sk","hu","ro","bg","gr","hr","si",
        "ee","lv","lt","ru","ua","by","md","ge","am","ae","sa","qa","kw","bh","om",
        "il","jo","eg","ma","tn","dz","za","ng","ke","gh","in","pk","bd","lk","sg",
        "my","ph","id","th","vn","hk","jp","kr","cn","tw","br","mx","ar","cl","co",
        "pe","uy","py","bo","ec","cr","pa","do"
    }
    if cc in supported:
        return f"https://www.careerjet.{cc}/search/jobs?s={kw}&l={loc}"
    # CareerJet also uses .com for some regions
    return f"https://www.careerjet.com/search/jobs?s={kw}&l={loc}"


def _glassdoor_url(country_code: str, keyword: str, country_name: str) -> str:
    """Glassdoor — country-specific subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    # Glassdoor uses ISO 2-letter subdomains
    supported = {
        "us","ca","uk","ie","au","nz","de","at","ch","fr","be","es","pt","it","nl",
        "se","no","dk","fi","pl","cz","hu","ro","gr","in","sg","hk","jp","br","mx",
        "ar","cl","co","za","ae"
    }
    if cc in supported:
        return f"https://www.glassdoor.{cc}/Job/jobs.htm?sc.keyword={kw}&locT=N&locId={loc}"
    return f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={kw}&locT=N&locName={loc}"


def _monster_url(country_code: str, keyword: str, country_name: str) -> str:
    """Monster — regional portals."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    domains = {
        "us": "www.monster.com/jobs/search?q={k}&where={l}",
        "uk": "www.monster.co.uk/jobs/search?q={k}&where={l}",
        "ie": "www.monster.ie/jobs/search?q={k}&where={l}",
        "de": "www.monster.de/jobs/suche?q={k}&where={l}",
        "fr": "www.monster.fr/emploi/recherche?q={k}&where={l}",
        "nl": "www.monsterboard.nl/vacatures/zoeken?q={k}&where={l}",
        "be": "jobs.monster.be/vacatures/zoeken?q={k}&where={l}",
        "se": "www.monster.se/jobb/sok?q={k}&where={l}",
        "no": "www.monster.no/jobb/sok?q={k}&where={l}",
        "fi": "www.monster.fi/tyopaikat/hae?q={k}&where={l}",
        "it": "www.monster.it/lavoro/ricerca?q={k}&where={l}",
        "es": "www.monster.es/empleo/buscar?q={k}&where={l}",
        "pl": "www.monsterpolska.pl/praca/szukaj?q={k}&where={l}",
        "cz": "www.monster.cz/prace/hledat?q={k}&where={l}",
        "hu": "www.monster.hu/allas/kereses?q={k}&where={l}",
        "ro": "www.monster.ro/locuri-de-munca/cauta?q={k}&where={l}",
        "gr": "www.monster.gr/ergasia/anazitisi?q={k}&where={l}",
        "in": "www.monsterindia.com/job-vacancies?q={k}&where={l}",
        "sg": "www.monster.com.sg/job-search?q={k}&where={l}",
        "my": "www.monster.com.my/job-search?q={k}&where={l}",
        "ph": "www.monster.com.ph/job-search?q={k}&where={l}",
        "th": "www.monster.co.th/job-search?q={k}&where={l}",
        "vn": "www.monster.com.vn/job-search?q={k}&where={l}",
        "hk": "www.monster.com.hk/job-search?q={k}&where={l}",
        "id": "www.monster.co.id/lowongan-kerja/cari?q={k}&where={l}",
        "za": "www.monster.co.za/jobs/search?q={k}&where={l}",
        "ng": "www.monster.com.ng/jobs/search?q={k}&where={l}",
        "ae": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "sa": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "qa": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "kw": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "bh": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "om": "www.monstergulf.com/jobs/search?q={k}&where={l}",
        "ca": "www.monster.ca/jobs/search?q={k}&where={l}",
        "au": "www.monster.com.au/jobs/search?q={k}&where={l}",
        "nz": "www.monster.co.nz/jobs/search?q={k}&where={l}",
    }
    tpl = domains.get(cc)
    if tpl:
        return f"https://{tpl.format(k=kw, l=loc)}"
    return f"https://www.monster.com/jobs/search?q={kw}&where={loc}"


def _stepstone_url(country_code: str, keyword: str, country_name: str) -> str:
    """StepStone — DACH region."""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    cc = country_code.lower()
    if cc == "de":
        return f"https://www.stepstone.de/jobs/{kw}/in-{loc}?radius=30"
    if cc == "at":
        return f"https://www.stepstone.at/jobs/{kw}/in-{loc}?radius=30"
    if cc == "ch":
        return f"https://www.stepstone.ch/jobs/{kw}/in-{loc}?radius=30"
    return ""


def _xing_url(country_code: str, keyword: str, country_name: str) -> str:
    """Xing — DACH region (German-speaking)."""
    if country_code.upper() not in ("DE", "AT", "CH"):
        return ""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    return f"https://www.xing.com/jobs/search?keywords={kw}&location={loc}"


def _bayt_url(country_code: str, keyword: str, country_name: str) -> str:
    """Bayt — Middle East focused."""
    me = {"AE","SA","QA","KW","BH","OM","IL","JO","EG","MA","TN","SA","LB","IQ","IR","YE","PS","SY","LY","SD","DZ","MR","DJ","KM","SO"}
    if country_code.upper() not in me:
        return ""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    return f"https://www.bayt.com/en/jobs/?q={kw}&loc={loc}"


def _jobstreet_url(country_code: str, keyword: str, country_name: str) -> str:
    """JobStreet — Southeast Asia."""
    cc = country_code.lower()
    domains = {
        "id": "www.jobstreet.co.id",
        "my": "www.jobstreet.com.my",
        "sg": "www.jobstreet.com.sg",
        "ph": "www.jobstreet.com.ph",
        "vn": "www.jobstreet.vn",
        "th": "www.jobstreet.co.th",
    }
    domain = domains.get(cc)
    if not domain:
        return ""
    kw = quote_plus(keyword)
    return f"https://{domain}/en/job-search/job-vacancy.php?keywords={kw}"


def _reed_url(country_code: str, keyword: str, country_name: str) -> str:
    """Reed — UK-focused."""
    if country_code.upper() not in ("GB", "UK"):
        return ""
    kw = quote_plus(keyword)
    return f"https://www.reed.co.uk/jobs/{kw}"


def _totaljobs_url(country_code: str, keyword: str, country_name: str) -> str:
    """Totaljobs — UK-focused."""
    if country_code.upper() not in ("GB", "UK"):
        return ""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    return f"https://www.totaljobs.com/jobs/{kw}/in-{loc}"


def _cvbankas_url(country_code: str, keyword: str, country_name: str) -> str:
    """CV Bankas — Lithuania."""
    if country_code.upper() != "LT":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.cvbankas.lt/?pavadinimas={kw}"


def _cv_lv_url(country_code: str, keyword: str, country_name: str) -> str:
    """CV.lv — Latvia & Estonia."""
    if country_code.upper() not in ("LV", "EE"):
        return ""
    kw = quote_plus(keyword)
    return f"https://www.cv.lv/en/jobs?keywords={kw}"


def _cv_keskus_url(country_code: str, keyword: str, country_name: str) -> str:
    """CV.ee — Estonia."""
    if country_code.upper() != "EE":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.cv.ee/en/search?keywords={kw}"


def _infopraca_url(country_code: str, keyword: str, country_name: str) -> str:
    """Infopraca — Poland."""
    if country_code.upper() != "PL":
        return ""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    return f"https://www.infopraca.pl/szukaj-pracy?szukaj={kw}&lokalizacja={loc}"


def _olx_url(country_code: str, keyword: str, country_name: str) -> str:
    """OLX Jobs — popular in Eastern Europe, Latin America, India."""
    cc = country_code.lower()
    supported = {
        "ua": "www.olx.ua",
        "kz": "www.olx.kz",
        "uz": "www.olx.uz",
        "bg": "www.olx.bg",
        "ro": "www.olx.ro",
        "pl": "www.olx.pl",
        "pt": "www.olx.pt",
        "in": "www.olx.in",
        "id": "www.olx.co.id",
        "za": "www.olx.co.za",
        "eg": "www.olx.com.eg",
        "ng": "www.olx.com.ng",
        "ke": "www.olx.co.ke",
        "gh": "www.olx.com.gh",
        "pe": "www.olx.pe",
        "ar": "www.olx.com.ar",
        "br": "www.olx.com.br",
        "co": "www.olx.com.co",
        "cl": "www.olx.cl",
        "mx": "www.olx.com.mx",
        "ec": "www.olx.com.ec",
        "do": "www.olx.com.do",
        "pk": "www.olx.com.pk",
        "bd": "www.olx.com.bd",
    }
    domain = supported.get(cc)
    if not domain:
        return ""
    kw = quote_plus(keyword)
    return f"https://{domain}/jobs/q-{kw}/"


def _posao_url(country_code: str, keyword: str, country_name: str) -> str:
    """Posao.ba — Bosnia & Herzegovina."""
    if country_code.upper() != "BA":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.posao.ba/JobSearch/Result?keywords={kw}"


def _infostud_url(country_code: str, keyword: str, country_name: str) -> str:
    """Infostud — Serbia."""
    if country_code.upper() != "RS":
        return ""
    kw = quote_plus(keyword)
    return f"https://poslovi.infostud.com/oglas/kljucna-rech/{kw}"


def _helloastronaut_url(country_code: str, keyword: str, country_name: str) -> str:
    """Astronaut — Romania-focused."""
    if country_code.upper() != "RO":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.helloastronaut.com/jobs?q={kw}"


def _ejobs_url(country_code: str, keyword: str, country_name: str) -> str:
    """eJobs — Romania."""
    if country_code.upper() != "RO":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.ejobs.ro/locuri-de-munca/{kw}/"


def _trovit_url(country_code: str, keyword: str, country_name: str) -> str:
    """Trovit — global aggregator with country subdomains."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    domains = {
        "us":"jobs.trovit.com","uk":"jobs.trovit.co.uk","ca":"jobs.trovit.ca",
        "au":"jobs.trovit.com.au","nz":"jobs.trovit.co.nz","ie":"jobs.trovit.ie",
        "de":"jobs.trovit.de","at":"jobs.trovit.at","ch":"jobs.trovit.ch",
        "fr":"jobs.trovit.fr","be":"jobs.trovit.be","es":"jobs.trovit.es",
        "it":"jobs.trovit.it","pt":"jobs.trovit.pt","nl":"jobs.trovit.nl",
        "lu":"jobs.trovit.lu","se":"jobs.trovit.se","no":"jobs.trovit.no",
        "fi":"jobs.trovit.fi","dk":"jobs.trovit.dk","pl":"jobs.trovit.pl",
        "cz":"jobs.trovit.cz","hu":"jobs.trovit.hu","ro":"jobs.trovit.ro",
        "bg":"jobs.trovit.bg","gr":"jobs.trovit.gr","hr":"jobs.trovit.hr",
        "si":"jobs.trovit.si","sk":"jobs.trovit.sk","ee":"jobs.trovit.ee",
        "lv":"jobs.trovit.lv","lt":"jobs.trovit.lt","ru":"jobs.trovit.ru",
        "ua":"jobs.trovit.ua","by":"jobs.trovit.by","in":"jobs.trovit.in",
        "pk":"jobs.trovit.pk","bd":"jobs.trovit.com.bd","sg":"jobs.trovit.sg",
        "my":"jobs.trovit.my","ph":"jobs.trovit.ph","id":"jobs.trovit.co.id",
        "th":"jobs.trovit.co.th","vn":"jobs.trovit.vn","hk":"jobs.trovit.hk",
        "jp":"jobs.trovit.jp","kr":"jobs.trovit.kr","cn":"jobs.trovit.cn",
        "tw":"jobs.trovit.tw","za":"jobs.trovit.co.za","ng":"jobs.trovit.com.ng",
        "ke":"jobs.trovit.co.ke","eg":"jobs.trovit.com.eg","ma":"jobs.trovit.ma",
        "ae":"jobs.trovit.ae","sa":"jobs.trovit.com","br":"jobs.trovit.com.br",
        "mx":"jobs.trovit.com.mx","ar":"jobs.trovit.com.ar","cl":"jobs.trovit.cl",
        "co":"jobs.trovit.co","pe":"jobs.trovit.pe","uy":"jobs.trovit.uy",
        "ec":"jobs.trovit.com.ec","cr":"jobs.trovit.cr","pa":"jobs.trovit.pa",
        "do":"jobs.trovit.com.do","py":"jobs.trovit.com.py","bo":"jobs.trovit.bo",
    }
    domain = domains.get(cc, "jobs.trovit.com")
    return f"https://{domain}/index.php?c=search_jobs&what={kw}&where={loc}"


def _ziprecruiter_url(country_code: str, keyword: str, country_name: str) -> str:
    """ZipRecruiter — primarily US/CA but allows search worldwide."""
    cc = country_code.upper()
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    if cc == "US":
        return f"https://www.ziprecruiter.com/search?search={kw}&location={loc}"
    if cc == "CA":
        return f"https://www.ziprecruiter.ca/search?search={kw}&location={loc}"
    if cc == "GB":
        return f"https://www.ziprecruiter.co.uk/search?search={kw}&location={loc}"
    # Fallback: US site with country name in location
    return f"https://www.ziprecruiter.com/search?search={kw}&location={loc}"


def _simplyhired_url(country_code: str, keyword: str, country_name: str) -> str:
    """SimplyHired — US/CA primarily."""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    cc = country_code.upper()
    if cc == "CA":
        return f"https://www.simplyhired.ca/search?q={kw}&l={loc}"
    return f"https://www.simplyhired.com/search?q={kw}&l={loc}"


def _neuvoo_url(country_code: str, keyword: str, country_name: str) -> str:
    """Neuvoo — Talent.com predecessor, but still mirrors some content."""
    # Same as Talent.com — skip duplicate
    return ""


def _hays_url(country_code: str, keyword: str, country_name: str) -> str:
    """Hays — global recruitment agency."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "us":"www.hays.com","uk":"www.hays.co.uk","ie":"www.hays.ie",
        "de":"www.hays.de","at":"www.hays.at","ch":"www.hays.ch",
        "fr":"www.hays.fr","be":"www.hays.be","nl":"www.hays.nl",
        "es":"www.hays.es","it":"www.hays.it","pt":"www.hays.pt",
        "se":"www.hays.se","no":"www.hays.no","dk":"www.hays.dk",
        "pl":"www.hays.pl","cz":"www.hays.cz","hu":"www.hays.hu",
        "ro":"www.hays.ro","ae":"www.hays.ae","sa":"www.hays.com",
        "qa":"www.hays.com","ca":"www.hays.ca","au":"www.hays.com.au",
        "nz":"www.hays.net.nz","jp":"www.hays.co.jp","sg":"www.hays.com.sg",
        "my":"www.hays.com.my","hk":"www.hays.com.hk","cn":"www.hays.cn",
        "in":"www.hays.com","za":"www.hays.co.za","br":"www.hays.com.br",
        "mx":"www.hays.com.mx","ar":"www.hays.com.ar","cl":"www.hays.cl",
        "co":"www.hays.com.co","pe":"www.hays.com.pe",
    }
    domain = domains.get(cc, "www.hays.com")
    return f"https://{domain}/job-search/?keywords={kw}"


def _michaelpage_url(country_code: str, keyword: str, country_name: str) -> str:
    """Michael Page — global."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "us":"www.michaelpage.com","uk":"www.michaelpage.co.uk","us":"www.michaelpage.com",
        "de":"www.michaelpage.de","fr":"www.michaelpage.fr","es":"www.michaelpage.es",
        "it":"www.michaelpage.it","nl":"www.michaelpage.nl","be":"www.michaelpage.be",
        "pt":"www.michaelpage.pt","se":"www.michaelpage.se","ch":"www.michaelpage.ch",
        "at":"www.michaelpage.at","ae":"www.michaelpage.ae","sa":"www.michaelpage.sa",
        "jp":"www.michaelpage.co.jp","sg":"www.michaelpage.com.sg","my":"www.michaelpage.com.my",
        "hk":"www.michaelpage.com.hk","cn":"www.michaelpage.com.cn","in":"www.michaelpage.co.in",
        "au":"www.michaelpage.com.au","nz":"www.michaelpage.co.nz","za":"www.michaelpage.co.za",
        "br":"www.michaelpage.com.br","mx":"www.michaelpage.com.mx","ar":"www.michaelpage.com.ar",
        "cl":"www.michaelpage.cl","co":"www.michaelpage.com.co",
    }
    domain = domains.get(cc, "www.michaelpage.com")
    return f"https://{domain}/job-search?keywords={kw}"


def _adecco_url(country_code: str, keyword: str, country_name: str) -> str:
    """Adecco — global staffing agency."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "us":"www.adecco.com","uk":"www.adecco.co.uk","ie":"www.adecco.ie",
        "de":"www.adecco.de","at":"www.adecco.at","ch":"www.adecco.ch",
        "fr":"www.adecco.fr","be":"www.adecco.be","nl":"www.adecco.nl",
        "es":"www.adecco.es","it":"www.adecco.it","pt":"www.adecco.pt",
        "se":"www.adecco.se","no":"www.adecco.no","fi":"www.adecco.fi",
        "dk":"www.adecco.dk","pl":"www.adecco.pl","cz":"www.adecco.cz",
        "hu":"www.adecco.hu","ro":"www.adecco.ro","gr":"www.adecco.gr",
        "in":"www.adecco.co.in","sg":"www.adecco.com.sg","my":"www.adecco.com.my",
        "ph":"www.adecco.com.ph","hk":"www.adecco.com.hk","th":"www.adecco.co.th",
        "vn":"www.adecco.com.vn","id":"www.adecco.co.id","jp":"www.adecco.co.jp",
        "kr":"www.adecco.co.kr","au":"www.adecco.com.au","nz":"www.adecco.co.nz",
        "za":"www.adecco.co.za","ae":"www.adecco.ae","sa":"www.adecco.com.sa",
        "ca":"www.adecco.ca","mx":"www.adecco.com.mx","br":"www.adecco.com.br",
        "ar":"www.adecco.com.ar","cl":"www.adecco.cl","co":"www.adecco.com.co",
        "pe":"www.adecco.pe","eg":"www.adecco.com.eg","ma":"www.adecco.ma",
        "ua":"www.adecco.ua","ru":"www.adecco.ru",
    }
    domain = domains.get(cc, "www.adecco.com")
    return f"https://{domain}/jobs/?keywords={kw}"


def _randstad_url(country_code: str, keyword: str, country_name: str) -> str:
    """Randstad — global staffing."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "us":"www.randstadusa.com","uk":"www.randstad.co.uk","ie":"www.randstad.ie",
        "de":"www.randstad.de","at":"www.randstad.at","ch":"www.randstad.ch",
        "fr":"www.randstad.fr","be":"www.randstad.be","nl":"www.randstad.nl",
        "es":"www.randstad.es","it":"www.randstad.it","pt":"www.randstad.pt",
        "se":"www.randstad.se","no":"www.randstad.no","fi":"www.randstad.fi",
        "dk":"www.randstad.dk","pl":"www.randstad.pl","cz":"www.randstad.cz",
        "hu":"www.randstad.hu","ro":"www.randstad.ro","gr":"www.randstad.gr",
        "in":"www.randstad.in","sg":"www.randstad.com.sg","my":"www.randstad.com.my",
        "hk":"www.randstad.com.hk","jp":"www.randstad.co.jp","au":"www.randstad.com.au",
        "nz":"www.randstad.co.nz","za":"www.randstad.co.za","ca":"www.randstad.ca",
        "mx":"www.randstad.com.mx","br":"www.randstad.com.br","ar":"www.randstad.com.ar",
        "cl":"www.randstad.cl","co":"www.randstad.com.co","lu":"www.randstad.lu",
    }
    domain = domains.get(cc, "www.randstad.com")
    return f"https://{domain}/jobs/?keyword={kw}"


def _manpower_url(country_code: str, keyword: str, country_name: str) -> str:
    """Manpower — global staffing."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "us":"www.manpower.com","uk":"www.manpower.co.uk","us":"www.manpower.com",
        "de":"www.manpower.de","fr":"www.manpower.fr","es":"www.manpower.es",
        "it":"www.manpower.it","nl":"www.manpower.nl","be":"www.manpower.be",
        "se":"www.manpower.se","no":"www.manpower.no","fi":"www.manpower.fi",
        "dk":"www.manpower.dk","pl":"www.manpower.pl","at":"www.manpower.at",
        "ch":"www.manpower.ch","gr":"www.manpower.gr","pt":"www.manpower.pt",
        "in":"www.manpower.co.in","jp":"www.manpower.co.jp","au":"www.manpower.com.au",
        "nz":"www.manpower.co.nz","ca":"www.manpower.ca","za":"www.manpower.co.za",
        "mx":"www.manpower.com.mx","ar":"www.manpower.com.ar","br":"www.manpower.com.br",
    }
    domain = domains.get(cc, "www.manpower.com")
    return f"https://{domain}/jobs/?keyword={kw}"


def _jobindex_url(country_code: str, keyword: str, country_name: str) -> str:
    """Jobindex — Denmark."""
    if country_code.upper() != "DK":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.jobindex.dk/jobsoegning?q={kw}"


def _infojobs_url(country_code: str, keyword: str, country_name: str) -> str:
    """InfoJobs — Spain & Italy."""
    cc = country_code.upper()
    kw = quote_plus(keyword)
    if cc == "ES":
        return f"https://www.infojobs.net/jobsearch/search-results/list?q={kw}"
    if cc == "IT":
        return f"https://www.infojobs.it/offerte-lavoro?q={kw}"
    return ""


def _computrabajo_url(country_code: str, keyword: str, country_name: str) -> str:
    """Computrabajo — Latin America & Spain."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "ar":"www.computrabajo.com.ar","bo":"www.computrabajo.com.bo",
        "br":"www.computrabajo.com.br","cl":"www.computrabajo.cl",
        "co":"www.computrabajo.com.co","cr":"www.computrabajo.co.cr",
        "do":"www.computrabajo.com.do","ec":"www.computrabajo.com.ec",
        "es":"www.computrabajo.es","gt":"www.computrabajo.com.gt",
        "mx":"www.computrabajo.com.mx","pa":"www.computrabajo.com.pa",
        "pe":"www.computrabajo.com.pe","pr":"www.computrabajo.com.pr",
        "pt":"www.computrabajo.pt","py":"www.computrabajo.com.py",
        "uy":"www.computrabajo.com.uy","ve":"www.computrabajo.com.ve",
        "us":"www.computrabajo.com",
    }
    domain = domains.get(cc)
    if not domain:
        return ""
    return f"https://{domain}/trabajo-de-{kw}"


def _bumeran_url(country_code: str, keyword: str, country_name: str) -> str:
    """Bumeran — Latin America."""
    cc = country_code.lower()
    kw = quote_plus(keyword)
    domains = {
        "ar":"www.bumeran.com.ar","bo":"www.bumeran.com.bo",
        "cl":"www.bumeran.cl","co":"www.bumeran.com.co",
        "cr":"www.bumeran.cr","do":"www.bumeran.com.do",
        "ec":"www.bumeran.com.ec","mx":"www.bumeran.com.mx",
        "pa":"www.bumeran.com.pa","py":"www.bumeran.com.py",
        "pe":"www.bumeran.com.pe","pr":"www.bumeran.com.pr",
        "uy":"www.bumeran.com.uy","ve":"www.bumeran.com.ve",
    }
    domain = domains.get(cc)
    if not domain:
        return ""
    return f"https://{domain}/empleos-busqueda-{kw}"


def _catho_url(country_code: str, keyword: str, country_name: str) -> str:
    """Catho — Brazil."""
    if country_code.upper() != "BR":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.catho.com.br/vagas/{kw}/"


def _ojt_url(country_code: str, keyword: str, country_name: str) -> str:
    """OJT — generic; works as a Google search aggregator fallback."""
    return ""  # disabled


def _duckduckgo_url(country_code: str, keyword: str, country_name: str) -> str:
    """DuckDuckGo HTML search — used as universal fallback to discover more jobs."""
    kw = quote_plus(f"{keyword} jobs {country_name}")
    return f"https://html.duckduckgo.com/html/?q={kw}"


def _googlejobs_url(country_code: str, keyword: str, country_name: str) -> str:
    """Google Jobs — via search query (no API)."""
    kw = quote_plus(f"{keyword} jobs {country_name} site:linkedin.com/jobs OR site:glassdoor.com OR site:jooble.org")
    return f"https://html.duckduckgo.com/html/?q={kw}"


def _linkup_url(country_code: str, keyword: str, country_name: str) -> str:
    """LinkUp — direct employer jobs (US/UK/CA/AU)."""
    cc = country_code.upper()
    if cc not in ("US","UK","GB","CA","AU"):
        return ""
    kw = quote_plus(keyword)
    loc = quote_plus(country_name)
    return f"https://www.linkup.com/search/results/?q={kw}&l={loc}"


def _wellfound_url(country_code: str, keyword: str, country_name: str) -> str:
    """Wellfound (formerly AngelList Talent) — startup jobs."""
    if country_code.upper() != "US":
        return ""
    kw = quote_plus(keyword)
    return f"https://wellfound.com/jobs?q={kw}"


def _hired_url(country_code: str, keyword: str, country_name: str) -> str:
    """Hired — tech jobs (US/UK/CA)."""
    if country_code.upper() not in ("US","GB","UK","CA"):
        return ""
    kw = quote_plus(keyword)
    return f"https://hired.com/jobs/?q={kw}"


def _usa_jobs_url(country_code: str, keyword: str, country_name: str) -> str:
    """US Government jobs — USA.gov."""
    if country_code.upper() != "US":
        return ""
    kw = quote_plus(keyword)
    return f"https://www.usajobs.gov/Search/Results?k={kw}"


def _ojt_eea_url(country_code: str, keyword: str, country_name: str) -> str:
    """EURES — European Job Mobility Portal."""
    cc = country_code.upper()
    european = {"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU",
                "IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES",
                "SE","GB","IS","NO","LI","CH"}
    if cc not in european:
        return ""
    kw = quote_plus(keyword)
    return f"https://eures.ec.europa.eu/en/jobs-search?keywords={kw}"


# ---------------------------------------------------------------------------
# Portal registry
# ---------------------------------------------------------------------------

PORTALS: List[Portal] = [
    # 1. Indeed (priority 10) — works for all 60+ Indeed-supported countries
    Portal("Indeed", "indeed", _indeed_url, weight=10),

    # 2. LinkedIn Jobs (priority 15) — global
    Portal("LinkedIn", "linkedin", _linkedin_url, weight=15),

    # 3. Jooble (priority 20) — 60+ countries
    Portal("Jooble", "jooble", _jooble_url, weight=20),

    # 4. Talent.com (priority 25) — 40+ countries
    Portal("Talent.com", "talent", _talent_url, weight=25),

    # 5. Jora (priority 30) — 30+ countries
    Portal("Jora", "jora", _jora_url, weight=30),

    # 6. CareerJet (priority 35) — 90+ countries
    Portal("CareerJet", "careerjet", _careerjet_url, weight=35),

    # 7. Glassdoor (priority 40) — 30+ countries
    Portal("Glassdoor", "glassdoor", _glassdoor_url, weight=40),

    # 8. Monster (priority 45) — 40+ countries
    Portal("Monster", "monster", _monster_url, weight=45),

    # 9. StepStone (priority 50) — DACH only
    Portal("StepStone", "stepstone", _stepstone_url,
           countries=["DE","AT","CH"], weight=50),

    # 10. Xing (priority 55) — DACH only
    Portal("Xing", "xing", _xing_url, countries=["DE","AT","CH"], weight=55),

    # 11. Bayt (priority 55) — Middle East
    Portal("Bayt", "bayt", _bayt_url, weight=55),

    # 12. JobStreet (priority 55) — SEA
    Portal("JobStreet", "jobstreet", _jobstreet_url, weight=55),

    # 13. Reed (priority 55) — UK
    Portal("Reed", "reed", _reed_url, countries=["GB"], weight=55),

    # 14. Totaljobs (priority 55) — UK
    Portal("Totaljobs", "totaljobs", _totaljobs_url, countries=["GB"], weight=55),

    # 15. CV Bankas (priority 60) — LT
    Portal("CV Bankas", "cvbankas", _cvbankas_url, countries=["LT"], weight=60),

    # 16. CV.lv (priority 60) — LV/EE
    Portal("CV.lv", "cv_lv", _cv_lv_url, countries=["LV","EE"], weight=60),

    # 17. Infopraca (priority 60) — PL
    Portal("Infopraca", "infopraca", _infopraca_url, countries=["PL"], weight=60),

    # 18. OLX Jobs (priority 65) — many countries
    Portal("OLX Jobs", "olx", _olx_url, weight=65),

    # 19. Posao.ba (priority 65) — BA
    Portal("Posao.ba", "posao", _posao_url, countries=["BA"], weight=65),

    # 20. Infostud (priority 65) — RS
    Portal("Infostud", "infostud", _infostud_url, countries=["RS"], weight=65),

    # 21. HelloAstronaut (priority 65) — RO
    Portal("HelloAstronaut", "helloastronaut", _helloastronaut_url, countries=["RO"], weight=65),

    # 22. eJobs (priority 65) — RO
    Portal("eJobs", "ejobs", _ejobs_url, countries=["RO"], weight=65),

    # 23. CV.ee (priority 65) — EE
    Portal("CV.ee", "cv_keskus", _cv_keskus_url, countries=["EE"], weight=65),

    # 24. Trovit (priority 50) — global aggregator
    Portal("Trovit", "trovit", _trovit_url, weight=50),

    # 25. ZipRecruiter (priority 50) — US/CA/UK + global fallback
    Portal("ZipRecruiter", "ziprecruiter", _ziprecruiter_url, weight=50),

    # 26. SimplyHired (priority 55) — US/CA + global
    Portal("SimplyHired", "simplyhired", _simplyhired_url, weight=55),

    # 27. Hays (priority 60) — global recruitment
    Portal("Hays", "hays", _hays_url, weight=60),

    # 28. Michael Page (priority 60) — global
    Portal("Michael Page", "michaelpage", _michaelpage_url, weight=60),

    # 29. Adecco (priority 60) — global staffing
    Portal("Adecco", "adecco", _adecco_url, weight=60),

    # 30. Randstad (priority 60) — global staffing
    Portal("Randstad", "randstad", _randstad_url, weight=60),

    # 31. Manpower (priority 60) — global staffing
    Portal("Manpower", "manpower", _manpower_url, weight=60),

    # 32. Jobindex (priority 65) — DK
    Portal("Jobindex", "jobindex", _jobindex_url, countries=["DK"], weight=65),

    # 33. InfoJobs (priority 60) — ES/IT
    Portal("InfoJobs", "infojobs", _infojobs_url, countries=["ES","IT"], weight=60),

    # 34. Computrabajo (priority 55) — Latin America
    Portal("Computrabajo", "computrabajo", _computrabajo_url, weight=55),

    # 35. Bumeran (priority 55) — Latin America
    Portal("Bumeran", "bumeran", _bumeran_url, weight=55),

    # 36. Catho (priority 65) — BR
    Portal("Catho", "catho", _catho_url, countries=["BR"], weight=65),

    # 37. LinkUp (priority 60) — US/UK/CA/AU
    Portal("LinkUp", "linkup", _linkup_url, countries=["US","GB","CA","AU"], weight=60),

    # 38. Wellfound (priority 65) — US startup jobs
    Portal("Wellfound", "wellfound", _wellfound_url, countries=["US"], weight=65),

    # 39. Hired (priority 65) — US/UK/CA tech
    Portal("Hired", "hired", _hired_url, countries=["US","GB","CA"], weight=65),

    # 40. USAJobs (priority 70) — US gov
    Portal("USAJobs", "usajobs", _usa_jobs_url, countries=["US"], weight=70),

    # 41. EURES (priority 70) — European portal
    Portal("EURES", "eures", _ojt_eea_url, weight=70),

    # 42. DuckDuckGo jobs (priority 75) — universal fallback
    Portal("DuckDuckGo", "duckduckgo", _duckduckgo_url, weight=75),

    # 43. Google Jobs via DDG (priority 80) — universal fallback
    Portal("GoogleJobs", "googlejobs", _googlejobs_url, weight=80),
]


def portals_for(country_code: str, region: str) -> List[Portal]:
    """Return all portals that apply to a given (country, region), sorted by weight."""
    out = [p for p in PORTALS if p.applies_to(country_code, region)]
    # Filter out portals whose build_url returned empty (country not supported)
    out = [p for p in out if p.build_url(country_code, "", "")]
    out.sort(key=lambda p: p.weight)
    return out


def count_portals_for(country_code: str, region: str) -> int:
    """Count how many portals are available for a given country."""
    return len(portals_for(country_code, region))


# Quick smoke test when run directly
if __name__ == "__main__":
    from .config import COUNTRIES
    print(f"Total portals configured: {len(PORTALS)}")
    for code, country in list(COUNTRIES.items())[:10]:
        avail = portals_for(code, country.region)
        print(f"  {code} ({country.name}): {len(avail)} portals → {[p.name for p in avail]}")
