"""
Configuration for JobRadar — global edition.
80 countries across 9 regions, multilingual keywords for 3 job categories.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict

try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, ".env"))
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Regions (9) — used by the wizard UI
# ---------------------------------------------------------------------------

REGIONS = [
    {"code": "europe",        "name": "Europe",        "icon": "🇪🇺", "color": "#3b82f6"},
    {"code": "russia",        "name": "Russia & CIS",  "icon": "🇷🇺", "color": "#ef4444"},
    {"code": "middle_east",   "name": "Middle East",   "icon": "🕌",  "color": "#f59e0b"},
    {"code": "asia",          "name": "Asia",          "icon": "🌏",  "color": "#10b981"},
    {"code": "africa",        "name": "Africa",        "icon": "🌍",  "color": "#f97316"},
    {"code": "north_america", "name": "North America", "icon": "🌎",  "color": "#8b5cf6"},
    {"code": "latin_america", "name": "Latin America", "icon": "🌴",  "color": "#06b6d4"},
    {"code": "oceania",       "name": "Oceania",       "icon": "🦘",  "color": "#ec4899"},
    {"code": "balkans",       "name": "Balkans",       "icon": "⛰️",  "color": "#84cc16"},
]


# ---------------------------------------------------------------------------
# Countries — 80 total
# ---------------------------------------------------------------------------

@dataclass
class Country:
    code: str               # ISO 2-letter
    name: str
    dial_code: str
    language: str
    region: str             # one of REGIONS codes
    indeed_domain: str = "" # e.g. "ro.indeed.com" — empty means no Indeed


COUNTRIES: Dict[str, Country] = {}

def _c(code, name, dial, lang, region, indeed=""):
    COUNTRIES[code] = Country(code, name, dial, lang, region, indeed)

# ---- Europe (27 EU + 8 non-EU = 35) ----
_c("AT","Austria","+43","German","europe","at.indeed.com")
_c("BE","Belgium","+32","French","europe","be.indeed.com")
_c("BG","Bulgaria","+359","Bulgarian","europe","bg.indeed.com")
_c("HR","Croatia","+385","Croatian","europe","hr.indeed.com")
_c("CY","Cyprus","+357","English","europe","cy.indeed.com")
_c("CZ","Czech Republic","+420","Czech","europe","cz.indeed.com")
_c("DK","Denmark","+45","Danish","europe","dk.indeed.com")
_c("EE","Estonia","+372","Estonian","europe","ee.indeed.com")
_c("FI","Finland","+358","Finnish","europe","fi.indeed.com")
_c("FR","France","+33","French","europe","fr.indeed.com")
_c("DE","Germany","+49","German","europe","de.indeed.com")
_c("GR","Greece","+30","Greek","europe","gr.indeed.com")
_c("HU","Hungary","+36","Hungarian","europe","hu.indeed.com")
_c("IE","Ireland","+353","English","europe","ie.indeed.com")
_c("IT","Italy","+39","Italian","europe","it.indeed.com")
_c("LV","Latvia","+371","Latvian","europe","lv.indeed.com")
_c("LT","Lithuania","+370","Lithuanian","europe","lt.indeed.com")
_c("LU","Luxembourg","+352","French","europe","lu.indeed.com")
_c("MT","Malta","+356","English","europe","mt.indeed.com")
_c("NL","Netherlands","+31","Dutch","europe","nl.indeed.com")
_c("PL","Poland","+48","Polish","europe","pl.indeed.com")
_c("PT","Portugal","+351","Portuguese","europe","pt.indeed.com")
_c("RO","Romania","+40","Romanian","europe","ro.indeed.com")
_c("SK","Slovakia","+421","Slovak","europe","sk.indeed.com")
_c("SI","Slovenia","+386","Slovenian","europe","si.indeed.com")
_c("ES","Spain","+34","Spanish","europe","es.indeed.com")
_c("SE","Sweden","+46","Swedish","europe","se.indeed.com")
_c("GB","United Kingdom","+44","English","europe","uk.indeed.com")
_c("CH","Switzerland","+41","German","europe","ch.indeed.com")
_c("NO","Norway","+47","Norwegian","europe","no.indeed.com")
_c("IS","Iceland","+354","Icelandic","europe","is.indeed.com")
_c("IE","Ireland","+353","English","europe","ie.indeed.com")  # dup safe
# Balkans (non-EU)
_c("RS","Serbia","+381","Serbian","balkans")
_c("BA","Bosnia","+387","Bosnian","balkans")
_c("ME","Montenegro","+382","Montenegrin","balkans")
_c("MK","North Macedonia","+389","Macedonian","balkans")
_c("AL","Albania","+355","Albanian","balkans")

# ---- Russia & CIS (6) ----
_c("RU","Russia","+7","Russian","russia","ru.indeed.com")
_c("UA","Ukraine","+380","Ukrainian","russia","ua.indeed.com")
_c("BY","Belarus","+375","Belarusian","russia")
_c("MD","Moldova","+373","Romanian","russia")
_c("GE","Georgia","+995","Georgian","russia")
_c("AM","Armenia","+374","Armenian","russia")

# ---- Middle East (8) ----
_c("AE","UAE","+971","Arabic","middle_east","ae.indeed.com")
_c("SA","Saudi Arabia","+966","Arabic","middle_east","sa.indeed.com")
_c("QA","Qatar","+974","Arabic","middle_east")
_c("KW","Kuwait","+965","Arabic","middle_east")
_c("BH","Bahrain","+973","Arabic","middle_east")
_c("OM","Oman","+968","Arabic","middle_east")
_c("IL","Israel","+972","Hebrew","middle_east","il.indeed.com")
_c("JO","Jordan","+962","Arabic","middle_east")

# ---- Asia (15) ----
_c("JP","Japan","+81","Japanese","asia","jp.indeed.com")
_c("KR","South Korea","+82","Korean","asia","kr.indeed.com")
_c("CN","China","+86","Chinese","asia")
_c("IN","India","+91","English","asia","in.indeed.com")
_c("PK","Pakistan","+92","English","asia","pk.indeed.com")
_c("BD","Bangladesh","+880","English","asia","bd.indeed.com")
_c("SG","Singapore","+65","English","asia","sg.indeed.com")
_c("HK","Hong Kong","+852","English","asia","hk.indeed.com")
_c("MY","Malaysia","+60","Malay","asia","my.indeed.com")
_c("TH","Thailand","+66","Thai","asia","th.indeed.com")
_c("PH","Philippines","+63","English","asia","ph.indeed.com")
_c("ID","Indonesia","+62","Indonesian","asia","id.indeed.com")
_c("VN","Vietnam","+84","Vietnamese","asia","vn.indeed.com")
_c("TW","Taiwan","+886","Chinese","asia","tw.indeed.com")
_c("LK","Sri Lanka","+94","English","asia","lk.indeed.com")

# ---- Africa (8) ----
_c("ZA","South Africa","+27","English","africa","za.indeed.com")
_c("EG","Egypt","+20","Arabic","africa","eg.indeed.com")
_c("NG","Nigeria","+234","English","africa","ng.indeed.com")
_c("KE","Kenya","+254","English","africa","ke.indeed.com")
_c("MA","Morocco","+212","Arabic","africa","ma.indeed.com")
_c("TN","Tunisia","+216","Arabic","africa","tn.indeed.com")
_c("GH","Ghana","+233","English","africa","gh.indeed.com")
_c("ET","Ethiopia","+251","Amharic","africa")

# ---- North America (3) ----
_c("US","United States","+1","English","north_america","www.indeed.com")
_c("CA","Canada","+1","English","north_america","ca.indeed.com")
_c("MX","Mexico","+52","Spanish","north_america","mx.indeed.com")

# ---- Latin America (8) ----
_c("BR","Brazil","+55","Portuguese","latin_america","br.indeed.com")
_c("AR","Argentina","+54","Spanish","latin_america","ar.indeed.com")
_c("CL","Chile","+56","Spanish","latin_america","cl.indeed.com")
_c("CO","Colombia","+57","Spanish","latin_america","co.indeed.com")
_c("PE","Peru","+51","Spanish","latin_america","pe.indeed.com")
_c("CR","Costa Rica","+506","Spanish","latin_america","cr.indeed.com")
_c("PA","Panama","+507","Spanish","latin_america","pa.indeed.com")
_c("UY","Uruguay","+598","Spanish","latin_america","uy.indeed.com")

# ---- Oceania (2) ----
_c("AU","Australia","+61","English","oceania","au.indeed.com")
_c("NZ","New Zealand","+64","English","oceania","nz.indeed.com")


# ---------------------------------------------------------------------------
# Job categories — multilingual keywords
# Each category has English fallback + per-country overrides for major languages.
# ---------------------------------------------------------------------------

@dataclass
class JobCategory:
    key: str
    english_label: str
    icon: str
    keywords: Dict[str, str]  # keyword per country ISO code; fallback uses "default"


CATEGORIES: Dict[str, JobCategory] = {
    "courier": JobCategory(
        key="courier",
        english_label="Courier (Glovo/Wolt/Bolt/Tazz)",
        icon="🛵",
        keywords={
            "default":  "courier glovo wolt bolt delivery",
            # Local-language overrides
            "RS": "kurir glovo wolt bolt",
            "BA": "kurir glovo wolt bolt",
            "ME": "kurir glovo wolt bolt",
            "MK": "kurier glovo wolt bolt",
            "AL": "kuroer glovo wolt bolt",
            "BG": "куер glovo wolt bolt",
            "RO": "curier glovo wolt bolt tazz",
            "GR": "διανομέας glovo wolt bolt",
            "RU": "курьер glovo wolt bolt",
            "UA": "кур'єр glovo wolt bolt",
            "PL": "kurier glovo wolt bolt",
            "CZ": "kurýr glovo wolt bolt",
            "SK": "kuriér glovo wolt bolt",
            "HU": "futár glovo wolt bolt",
            "LT": "kurjeris glovo wolt bolt",
            "LV": "kurjers glovo wolt bolt",
            "EE": "kuller glovo wolt bolt",
            "FI": "kuriiri glovo wolt bolt",
            "SE": "budförare glovo wolt bolt",
            "DE": "Kurierfahrer glovo wolt bolt",
            "AT": "Kurierfahrer glovo wolt bolt",
            "CH": "Kurierfahrer glovo wolt bolt",
            "FR": "coursier glovo wolt bolt",
            "BE": "coursier koerier glovo wolt bolt",
            "ES": "repartidor glovo wolt bolt",
            "PT": "estafeta glovo wolt bolt",
            "BR": "entregador glovo wolt bolt",
            "IT": "corriere glovo wolt bolt",
            "NL": "koerier glovo wolt bolt",
            "TR": "kurye glovo wolt bolt",
            "JP": "バイク便 glovo wolt bolt",
            "KR": "배달원 glovo wolt bolt",
            "CN": "快递员 glovo wolt bolt",
            "TH": "พนักงานส่ง glovo wolt bolt",
            "VN": "giao hàng glovo wolt bolt",
            "ID": "kurir glovo wolt bolt",
            "PH": "courier glovo wolt bolt",
            "MY": "penghantaran glovo wolt bolt",
            "AE": "مندوب توصيل glovo wolt bolt",
            "SA": "مندوب توصيل glovo wolt bolt",
            "QA": "مندوب توصيل glovo wolt bolt",
            "EG": "مندوب توصيل glovo wolt bolt",
            "MA": "مندوب توصيل glovo wolt bolt",
            "TN": "مندوب توصيل glovo wolt bolt",
            "MX": "repartidor glovo wolt bolt",
            "AR": "repartidor glovo wolt bolt",
            "CL": "repartidor glovo wolt bolt",
            "CO": "domiciliario glovo wolt bolt",
            "PE": "repartidor glovo wolt bolt",
            "ZA": "courier glovo wolt bolt",
            "NG": "courier glovo wolt bolt",
            "KE": "courier glovo wolt bolt",
        },
    ),
    "construction": JobCategory(
        key="construction",
        english_label="Construction worker",
        icon="🏗️",
        keywords={
            "default":  "construction worker laborer building site",
            "RS": "građevinski radnik",
            "BA": "građevinski radnik",
            "ME": "građevinski radnik",
            "MK": "градежен работник",
            "BG": "строителен работник",
            "RO": "muncitor constructii",
            "GR": "εργάτης κατασκευών",
            "RU": "строитель",
            "UA": "будівельник",
            "PL": "pracownik budowlany",
            "CZ": "stavební dělník",
            "SK": "stavebný robotník",
            "HU": "építőipari munkás",
            "LT": "statybos darbuotojas",
            "LV": "būvdarbu strādnieks",
            "EE": "ehitustööline",
            "FI": "rakennustyöntekijä",
            "SE": "byggnadsarbetare",
            "DE": "Bauarbeiter",
            "AT": "Bauarbeiter",
            "CH": "Bauarbeiter",
            "FR": "ouvrier du bâtiment",
            "BE": "ouvrier du bâtiment bouwvakker",
            "ES": "obrero de construcción",
            "PT": "trabalhador da construção",
            "BR": "trabalhador da construção",
            "IT": "operaio edile",
            "NL": "bouwvakker",
            "TR": "inşaat işçisi",
            "JP": "建設作業員",
            "KR": "건설 노동자",
            "CN": "建筑工人",
            "TH": "คนงานก่อสร้าง",
            "VN": "công nhân xây dựng",
            "ID": "pekerja konstruksi",
            "PH": "construction worker",
            "MY": "pekerja binaan",
            "AE": "عامل بناء",
            "SA": "عامل بناء",
            "QA": "عامل بناء",
            "EG": "عامل بناء",
            "MA": "عامل بناء",
            "TN": "عامل بناء",
            "MX": "obrero de construcción",
            "AR": "obrero de construcción",
            "CL": "obrero de construcción",
            "CO": "obrero de construcción",
            "PE": "obrero de construcción",
            "ZA": "construction worker",
            "NG": "construction worker",
            "KE": "construction worker",
        },
    ),
    "factory": JobCategory(
        key="factory",
        english_label="Factory worker",
        icon="🏭",
        keywords={
            "default":  "factory worker production line manufacturing",
            "RS": "radnik u fabrici proizvodnja",
            "BA": "radnik u fabrici proizvodnja",
            "ME": "radnik u fabrici proizvodnja",
            "MK": "работник во фабрика",
            "BG": "работник във фабрика производство",
            "RO": "muncitor fabrica productie",
            "GR": "εργάτης εργοστασίου",
            "RU": "фабричный рабочий",
            "UA": "заводський робітник",
            "PL": "pracownik fabryczny produkcja",
            "CZ": "tovární dělník",
            "SK": "továrenský robotník",
            "HU": "gyári munkás",
            "LT": "fabrikos darbuotojas gamyba",
            "LV": "fabrikas strādnieks",
            "EE": "tehasetööline",
            "FI": "tehdastyöntekijä",
            "SE": "fabriksarbetare",
            "DE": "Fabrikarbeiter Produktion",
            "AT": "Fabrikarbeiter Produktion",
            "CH": "Fabrikarbeiter Produktion",
            "FR": "ouvrier d'usine production",
            "BE": "ouvrier d'usine fabrieksarbeider",
            "ES": "obrero de fábrica producción",
            "PT": "trabalhador de fábrica",
            "BR": "trabalhador de fábrica",
            "IT": "operaio di fabbrica",
            "NL": "fabrieksarbeider",
            "TR": "fabrika işçisi",
            "JP": "工場労働者",
            "KR": "공장 노동자",
            "CN": "工厂工人",
            "TH": "คนงานโรงงาน",
            "VN": "công nhân nhà máy",
            "ID": "pekerja pabrik",
            "PH": "factory worker",
            "MY": "pekerja kilang",
            "AE": "عامل مصنع",
            "SA": "عامل مصنع",
            "QA": "عامل مصنع",
            "EG": "عامل مصنع",
            "MA": "عامل مصنع",
            "TN": "عامل مصنع",
            "MX": "obrero de fábrica",
            "AR": "obrero de fábrica",
            "CL": "obrero de fábrica",
            "CO": "obrero de fábrica",
            "PE": "obrero de fábrica",
            "ZA": "factory worker",
            "NG": "factory worker",
            "KE": "factory worker",
        },
    ),
    "driver": JobCategory(
        key="driver",
        english_label="Driver (Truck / Taxi / Uber / Delivery van)",
        icon="🚚",
        keywords={
            "default":  "driver truck lorry van taxi uber bolt delivery driver",
            "RS": "vozač kamion dostavno vozilo",
            "BA": "vozač kamion dostavno vozilo",
            "ME": "vozač kamion dostavno vozilo",
            "MK": "возач камион доставно возило",
            "BG": "шофьор камион ван",
            "RO": "șofer camion furgonetă taxi",
            "GR": "οδηγός φορτηγό βαν",
            "RU": "водитель грузовик такси",
            "UA": "водій вантажівка таксі",
            "PL": "kierowca ciężarówka furgonetka",
            "CZ": "řidič kamion dodávka",
            "SK": "vodič kamión dodávka",
            "HU": "sofőr teherautó furgon",
            "LT": "vairuotojas sunkvežimis",
            "LV": "vadītājs kravas mašīna",
            "EE": "juht veoauto",
            "FI": "kuljettaja kuorma-auto",
            "SE": "förare lastbil",
            "DE": "Fahrer LKW Lieferwagen",
            "AT": "Fahrer LKW Lieferwagen",
            "CH": "Fahrer LKW Lieferwagen",
            "FR": "chauffeur camion fourgon",
            "BE": "chauffeur camion bestelwagen",
            "ES": "conductor camión furgoneta",
            "PT": "condutor camião carrinha",
            "BR": "motorista caminhão van",
            "IT": "autista camion furgone",
            "NL": "chauffeur vrachtwagen bestelbus",
            "TR": "şoför kamyon van",
            "JP": "トラック運転手",
            "KR": "트럭 운전사",
            "CN": "卡车司机",
            "TH": "คนขับรถบรรทุก",
            "VN": "tài xế xe tải",
            "ID": "sopir truk",
            "PH": "driver truck",
            "MY": "pemandu lori",
            "AE": "سائق شاحنة",
            "SA": "سائق شاحنة",
            "QA": "سائق شاحنة",
            "EG": "سائق شاحنة",
            "MA": "سائق شاحنة",
            "TN": "سائق شاحنة",
            "MX": "conductor camión",
            "AR": "conductor camión",
            "CL": "conductor camión",
            "CO": "conductor camión",
            "PE": "conductor camión",
            "ZA": "driver truck",
            "NG": "driver truck",
            "KE": "driver truck",
        },
    ),
    "warehouse": JobCategory(
        key="warehouse",
        english_label="Warehouse worker (Picker / Packer / Forklift)",
        icon="📦",
        keywords={
            "default":  "warehouse picker packer forklift storekeeper inventory",
            "RS": "magacioner skladište komisionar",
            "BA": "magacioner skladište komisionar",
            "ME": "magacioner skladište komisionar",
            "MK": "магационер складиште",
            "BG": "складово работник",
            "RO": "muncitor depozit magazioner",
            "GR": "εργάτης αποθήκης",
            "RU": "кладовщик склад",
            "UA": "комірник склад",
            "PL": "magazynier skład",
            "CZ": "skladník sklad",
            "SK": "skladník sklad",
            "HU": "raktáros rakodó",
            "LT": "sandėlininkas",
            "LV": "noliktavas darbinieks",
            "EE": "laotööline",
            "FI": "varastotyöntekijä",
            "SE": "lagerarbetare",
            "DE": "Lagermitarbeiter Kommissionierer",
            "AT": "Lagermitarbeiter Kommissionierer",
            "CH": "Lagermitarbeiter Kommissionierer",
            "FR": "préparateur de commande magasinier",
            "BE": "préparateur de commande magasinier",
            "ES": "mozo de almacén",
            "PT": "trabalhador de armazém",
            "BR": "auxiliar de armazém",
            "IT": "magazziniere",
            "NL": "magazijnmedewerker",
            "TR": "depo çalışanı",
            "JP": "倉庫作業員",
            "KR": "창고 작업자",
            "CN": "仓库工人",
            "TH": "คนงานคลังสินค้า",
            "VN": "nhân viên kho",
            "ID": "pekerja gudang",
            "PH": "warehouse worker",
            "MY": "pekerja gudang",
            "AE": "عامل مستودع",
            "SA": "عامل مستودع",
            "QA": "عامل مستودع",
            "EG": "عامل مخزن",
            "MA": "عامل مخزن",
            "TN": "عامل مخزن",
            "MX": "mozo de almacén",
            "AR": "mozo de almacén",
            "CL": "mozo de almacén",
            "CO": "mozo de almacén",
            "PE": "mozo de almacén",
            "ZA": "warehouse worker",
            "NG": "warehouse worker",
            "KE": "warehouse worker",
        },
    ),
    "hospitality": JobCategory(
        key="hospitality",
        english_label="Hospitality (Cook / Waiter / Kitchen helper)",
        icon="🍳",
        keywords={
            "default":  "cook chef waiter waitress kitchen helper hotel restaurant",
            "RS": "kuvar konobar kuhinja hotel restoran",
            "BA": "kuvar konobar kuhinja hotel restoran",
            "ME": "kuvar konobar kuhinja hotel restoran",
            "MK": "готвач келнер кујна",
            "BG": "готвач сервитьор кухня",
            "RO": "bucătar chelner ospătar",
            "GR": "μάγειρας σερβιτόρος",
            "RU": "повар официант кухня",
            "UA": "кухар офіціант",
            "PL": "kucharz kelner",
            "CZ": "kuchař číšník",
            "SK": "kuchár čašník",
            "HU": "szakács pincér",
            "LT": "virėjas padavėjas",
            "LV": "pavārs viesmīlis",
            "EE": "kokk ettekandja",
            "FI": "kokki tarjoilija",
            "SE": "kock servitör",
            "DE": "Koch Kellner",
            "AT": "Koch Kellner",
            "CH": "Koch Kellner",
            "FR": "cuisinier serveur",
            "BE": "cuisinier serveur",
            "ES": "cocinero camarero",
            "PT": "cozinheiro empregado de mesa",
            "BR": "cozinheiro garçom",
            "IT": "cuoco cameriere",
            "NL": "kok ober",
            "TR": "aşçı garson",
            "JP": "料理人 ウェイター",
            "KR": "요리사 웨이터",
            "CN": "厨师 服务员",
            "TH": "พนักงานทำอาหาร พนักงานเสิร์ฟ",
            "VN": "đầu bếp phục vụ",
            "ID": "koki pelayan",
            "PH": "cook waiter",
            "MY": "tukang masak pelayan",
            "AE": "طباخ نادل",
            "SA": "طباخ نادل",
            "QA": "طباخ نادل",
            "EG": "طباخ نادل",
            "MA": "طباخ نادل",
            "TN": "طباخ نادل",
            "MX": "cocinero mesero",
            "AR": "cocinero mesero",
            "CL": "cocinero mesero",
            "CO": "cocinero mesero",
            "PE": "cocinero mesero",
            "ZA": "cook waiter",
            "NG": "cook waiter",
            "KE": "cook waiter",
        },
    ),
    "cleaning": JobCategory(
        key="cleaning",
        english_label="Cleaning (Hotel / Office / Domestic)",
        icon="🧹",
        keywords={
            "default":  "cleaner cleaning maid housekeeping janitor",
            "RS": "čistačica spremanje sobarica",
            "BA": "čistačica spremanje sobarica",
            "ME": "čistačica spremanje sobarica",
            "MK": "чистачка собарица",
            "BG": "чистачка камериерка",
            "RO": "menajeră curățenie",
            "GR": "καθαρίστρια",
            "RU": "уборщица горничная",
            "UA": "прибиральниця покоївка",
            "PL": "sprzątaczka pokojówka",
            "CZ": "uklízečka",
            "SK": "upratovačka",
            "HU": "takarító szobalány",
            "LT": "valytoja",
            "LV": "kopēja",
            "EE": "koristaja",
            "FI": "siivooja",
            "SE": "städhjälp",
            "DE": "Reinigungskraft Zimmermädchen",
            "AT": "Reinigungskraft Zimmermädchen",
            "CH": "Reinigungskraft Zimmermädchen",
            "FR": "femme de ménage",
            "BE": "femme de ménage",
            "ES": "limpiadora",
            "PT": "auxiliar de limpeza",
            "BR": "auxiliar de limpeza",
            "IT": "addetto alle pulizie",
            "NL": "schoonmaker",
            "TR": "temizlikçi",
            "JP": "清掃員",
            "KR": "청소부",
            "CN": "清洁工",
            "TH": "พนักงานทำความสะอาด",
            "VN": "nhân viên vệ sinh",
            "ID": "petugas kebersihan",
            "PH": "cleaner",
            "MY": "pekerja pembersihan",
            "AE": "عامل نظافة",
            "SA": "عامل نظافة",
            "QA": "عامل نظافة",
            "EG": "عامل نظافة",
            "MA": "عامل نظافة",
            "TN": "عامل نظافة",
            "MX": "limpiador",
            "AR": "limpiador",
            "CL": "limpiador",
            "CO": "limpiador",
            "PE": "limpiador",
            "ZA": "cleaner",
            "NG": "cleaner",
            "KE": "cleaner",
        },
    ),
    "caregiving": JobCategory(
        key="caregiving",
        english_label="Caregiver (Elderly / Childcare / Nurse assistant)",
        icon="🧑‍🤝‍🧑",
        keywords={
            "default":  "caregiver elderly care nursing assistant nanny babysitter",
            "RS": "negovatelj starih dadilja",
            "BA": "negovatelj starih dadilja",
            "ME": "negovatelj starih dadilja",
            "MK": "неговател стари",
            "BG": "грижовник възрастни",
            "RO": "îngrijitor bătrâni bonă",
            "GR": "φροντιστής ηλικιωμένων",
            "RU": "сиделка няня",
            "UA": "доглядачка няня",
            "PL": "opiekun osób starszych niania",
            "CZ": "pečovatel kojná",
            "SK": "opatrovateľ",
            "HU": "ápoló gondozó",
            "LT": "slaugytojas auklė",
            "LV": "aprūpētājs",
            "EE": "hooldaja",
            "FI": "hoitaja lastenhoitaja",
            "SE": "vårdare",
            "DE": "Pflegekraft Altenpfleger",
            "AT": "Pflegekraft Altenpfleger",
            "CH": "Pflegekraft Altenpfleger",
            "FR": "aide-soignant garde d'enfants",
            "BE": "aide-soignant",
            "ES": "cuidador ancianos",
            "PT": "cuidador idosos",
            "BR": "cuidador idosos",
            "IT": "caregiver badante",
            "NL": "verzorger",
            "TR": "bakıcı yaşlı",
            "JP": "介護士",
            "KR": "간병인",
            "CN": "护工",
            "TH": "พนักงานดูแลผู้สูงอายุ",
            "VN": "người chăm sóc",
            "ID": "perawat",
            "PH": "caregiver",
            "MY": "penjaga",
            "AE": "ممرض رعاية",
            "SA": "ممرض رعاية",
            "QA": "ممرض رعاية",
            "EG": "ممرض رعاية",
            "MA": "ممرض رعاية",
            "TN": "ممرض رعاية",
            "MX": "cuidador",
            "AR": "cuidador",
            "CL": "cuidador",
            "CO": "cuidador",
            "PE": "cuidador",
            "ZA": "caregiver",
            "NG": "caregiver",
            "KE": "caregiver",
        },
    ),
    "sales": JobCategory(
        key="sales",
        english_label="Sales & Retail (Shop assistant / Cashier)",
        icon="🛍️",
        keywords={
            "default":  "sales shop assistant cashier retail store seller",
            "RS": "prodavac kasir prodavnica",
            "BA": "prodavac kasir prodavnica",
            "ME": "prodavac kasir prodavnica",
            "MK": "продавач касир",
            "BG": "продавач касиер",
            "RO": "vânzător casier magazin",
            "GR": "πωλητής ταμείας",
            "RU": "продавец кассир",
            "UA": "продавець касир",
            "PL": "sprzedawca kasjer",
            "CZ": "prodavač pokladní",
            "SK": "predavač pokladník",
            "HU": "eladó pénztáros",
            "LT": "pardavėjas kasininkas",
            "LV": "pārdevējs kasieris",
            "EE": "müüja kassapidaja",
            "FI": "myyjä kassanhoitaja",
            "SE": "säljare kassör",
            "DE": "Verkäufer Kassierer",
            "AT": "Verkäufer Kassierer",
            "CH": "Verkäufer Kassierer",
            "FR": "vendeur caissier",
            "BE": "vendeur caissier",
            "ES": "dependiente cajero",
            "PT": "vendedor caixa",
            "BR": "vendedor caixa",
            "IT": "commesso cassiere",
            "NL": "verkoper kassier",
            "TR": "satış elemanı kasiyer",
            "JP": "店員 レジ",
            "KR": "판매원",
            "CN": "销售员 收银员",
            "TH": "พนักงานขาย แคชเชียร์",
            "VN": "nhân viên bán hàng thu ngân",
            "ID": "pramuniaga kasir",
            "PH": "sales cashier",
            "MY": "jurujual juruwang",
            "AE": "بائع كاشير",
            "SA": "بائع كاشير",
            "QA": "بائع كاشير",
            "EG": "بائع كاشير",
            "MA": "بائع كاشير",
            "TN": "بائع كاشير",
            "MX": "vendedor cajero",
            "AR": "vendedor cajero",
            "CL": "vendedor cajero",
            "CO": "vendedor cajero",
            "PE": "vendedor cajero",
            "ZA": "sales cashier",
            "NG": "sales cashier",
            "KE": "sales cashier",
        },
    ),
    "security": JobCategory(
        key="security",
        english_label="Security guard",
        icon="👮",
        keywords={
            "default":  "security guard night watchman doorman",
            "RS": "zaštitar noćni čuvar",
            "BA": "zaštitar noćni čuvar",
            "ME": "zaštitar noćni čuvar",
            "MK": "чувар обезседување",
            "BG": "охрана пазач",
            "RO": "bodyguard paznic",
            "GR": "φύλακας ασφάλεια",
            "RU": "охранник",
            "UA": "охоронець",
            "PL": "ochroniarz",
            "CZ": "ochránce",
            "SK": "ochrankár",
            "HU": "biztonsági őr",
            "LT": "apsaugos darbuotojas",
            "LV": "apsardzes darbinieks",
            "EE": "turvatöötaja",
            "FI": "turvallisuusvartija",
            "SE": "vakt",
            "DE": "Sicherheitsmitarbeiter",
            "AT": "Sicherheitsmitarbeiter",
            "CH": "Sicherheitsmitarbeiter",
            "FR": "agent de sécurité",
            "BE": "agent de sécurité",
            "ES": "vigilante de seguridad",
            "PT": "segurança",
            "BR": "vigilante",
            "IT": "addetto alla sicurezza",
            "NL": "beveiliger",
            "TR": "güvenlik görevlisi",
            "JP": "警備員",
            "KR": "경비원",
            "CN": "保安",
            "TH": "พนักงานรักษาความปลอดภัย",
            "VN": "bảo vệ",
            "ID": "satpam",
            "PH": "security guard",
            "MY": "pengawal keselamatan",
            "AE": "حارس أمن",
            "SA": "حارس أمن",
            "QA": "حارس أمن",
            "EG": "حارس أمن",
            "MA": "حارس أمن",
            "TN": "حارس أمن",
            "MX": "guardia de seguridad",
            "AR": "guardia de seguridad",
            "CL": "guardia de seguridad",
            "CO": "guardia de seguridad",
            "PE": "guardia de seguridad",
            "ZA": "security guard",
            "NG": "security guard",
            "KE": "security guard",
        },
    ),
}


def get_keyword(category_key: str, country_code: str) -> str:
    cat = CATEGORIES.get(category_key)
    if not cat:
        return ""
    return cat.keywords.get(country_code) or cat.keywords["default"]


def countries_by_region(region_code: str) -> List[Country]:
    return [c for c in COUNTRIES.values() if c.region == region_code]


# ---------------------------------------------------------------------------
# Settings (loaded from env)
# ---------------------------------------------------------------------------

def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _default_db_path() -> str:
    return os.path.join(_project_root(), "jobradar.db")

def _default_screenshots_dir() -> str:
    return os.path.join(_project_root(), "download", "screenshots")


class Settings:
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", _default_db_path())

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # Default to the user's actual bot — they can override via env if they ever rename it.
    TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "EuropaElyano_bot")
    WEBAPP_PUBLIC_URL: str = os.getenv("WEBAPP_PUBLIC_URL", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")  # fallback admin chat

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    SCAN_CRON_HOURS: str = os.getenv("SCAN_CRON_HOURS", "8,20")
    SCAN_CRON_TZ: str = os.getenv("SCAN_CRON_TZ", "Africa/Cairo")

    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
    PAGE_TIMEOUT_MS: int = int(os.getenv("PAGE_TIMEOUT_MS", "30000"))
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    MAX_JOBS_PER_PORTAL: int = int(os.getenv("MAX_JOBS_PER_PORTAL", "15"))

    REALTIME_NOTIFY: bool = os.getenv("REALTIME_NOTIFY", "true").lower() == "true"

    WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "0.0.0.0")
    # Railway injects a dynamic PORT; honor it first, fall back to WEBAPP_PORT, then 8000.
    WEBAPP_PORT: int = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "8000")

    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change-me-in-production")

    # ----- Phase 3: Email (SMTP) -----
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "JobRadar")
    SMTP_USE_SSL: bool = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

    # ----- Phase 3: Admin -----
    # Comma-separated list of Telegram usernames that get admin privileges.
    ADMIN_TELEGRAM_USERNAMES: str = os.getenv("ADMIN_TELEGRAM_USERNAMES", "")

    # ----- Phase 3: Resume uploads -----
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", os.path.join(_project_root(), "upload", "resumes"))
    MAX_RESUME_SIZE_BYTES: int = int(os.getenv("MAX_RESUME_SIZE_BYTES", str(5 * 1024 * 1024)))  # 5 MB


def is_admin(username: str) -> bool:
    """Check whether a Telegram username is in the admin list."""
    if not username or not settings.ADMIN_TELEGRAM_USERNAMES:
        return False
    clean = username.strip().lstrip("@").lower()
    admins = [a.strip().lstrip("@").lower() for a in settings.ADMIN_TELEGRAM_USERNAMES.split(",") if a.strip()]
    return clean in admins


settings = Settings()
