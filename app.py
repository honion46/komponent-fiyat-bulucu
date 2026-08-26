import concurrent.futures
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

SEARCH_URL_TEMPLATES = {
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Motorobit": "https://www.motorobit.com/arama?q={query}",
    "Robolink": "https://www.robolinkmarket.com/arama?q={query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama/{query}",
    "F1 Depo": "https://www.f1depo.com/arama/{query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Elektrodepo": "https://www.elektrodepo.com/arama/{query}",
    "Komponentci": "https://www.komponentci.net/arama?tip=1&kat=0&word={query}&search=",
    "RoboShop": "https://www.roboshop.com.tr/arama?k={query}",
    "Görsu Elektronik": "https://gorsuelektronik.com/arama?q={query}",
    "Robot Sepeti": "https://www.robotsepeti.com/arama?q={query}",
    "Robo90": "https://www.robo90.com/arama?q={query}",
}

# Bu siteler sonucu AJAX/JS ile geç dolduruyor, standart bekleme yetmiyor
SLOW_AJAX_SITES = {"Robolink", "Motorobit"}

# Bu siteler headless tarayıcıyı Cloudflare üzerinden tespit edip engelliyor
CLOUDFLARE_SITES = set()

SITE_WAIT_SELECTORS = {}

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|TRY)", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,]+$")

IGNORE_LINK_TEXT = {
    "add to cart", "sepete ekle", "favorilere ekle", "add to favorites",
    "i̇ncele", "incele", "javascript:void(0);", "see all", "tümü", "detay",
    "giriş yap", "üye ol", "sipariş takibi", "iletişim", "kategoriler", "yardım",
    "hesabım", "sepetim", "günün fırsatları", "müşteri hizmetleri", "satış yap",
}

DEBUG_DIR = "debug_snapshots"


@dataclass
class Product:
    site: str
    name: str
    price: float | None
    url: str
    mpn: str = ""
    stock: str = "Bilinmiyor"
    match_score: int = 0
    quantity: int = 1


def parse_price(raw: str) -> float | None:
    raw = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_products_jsonld(soup: BeautifulSoup, site_name: str, keywords: list[str]) -> list[Product]:
    """schema.org JSON-LD (Product/ItemList) verisinden ürünleri çıkarır. Metin/HTML tahminine göre çok daha güvenilir; birçok e-ticaret sitesi SEO için bunu sayfaya gömer."""
    found = []

    def collect(obj):
        if isinstance(obj, dict):
            t = obj.get("@type")
            if t == "Product":
                found.append(obj)
            elif t == "ItemList":
                for el in obj.get("itemListElement", []):
                    if isinstance(el, dict):
                        collect(el.get("item", el))
            else:
                for v in obj.values():
                    collect(v)
        elif isinstance(obj, list):
            for el in obj:
                collect(el)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        collect(data)

    results = []
    seen_urls = set()
    for p in found:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if keywords and not any(k in name.lower() for k in keywords):
            continue
        offers = p.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        url = p.get("url") or offers.get("url") or ""
        if not url:
            continue
        price = None
        raw_price = offers.get("price")
        if raw_price is not None:
            try:
                price = float(str(raw_price).replace(",", "."))
            except (TypeError, ValueError):
                price = None
        if url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(Product(site_name, name, price, url))

    return results



def parse_query_quantity(query: str) -> tuple[str, int]:
    """MP1584 x 5 / MP1584 5 adet gibi sorgulardan miktarı ayırır."""
    q = query.strip()
    for pattern in (
        r"\s*[xX×]\s*(\d+)\s*$",
        r"\s+(\d+)\s*(?:adet|ad)\s*$",
    ):
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            return q[:m.start()].strip(), max(1, int(m.group(1)))
    return q, 1


def detect_stock(text: str) -> str:
    """Ürün sayfasındaki satın alma davranışına göre stok durumunu belirler. Sepete ekle/satın al varsa stokta; gelince haber ver/tükendi vb. varsa yok."""
    t = (text or "").lower()

    # Kesin stok dışı ifadeleri önce kontrol et.
    out_of_stock = (
        "gelince haber ver",
        "stokta yok",
        "stok dışı",
        "stok disi",
        "tükendi",
        "tukendi",
        "stokta bulunmuyor",
        "stokta bulunmamaktadır",
        "satışta değil",
        "satisda degil",
        "ön sipariş",
        "on siparis",
    )
    if any(x in t for x in out_of_stock):
        return "Yok"

    # Satın alma aksiyonu varsa ürün alınabilir/stokta kabul edilir.
    in_stock = (
        "sepete ekle",
        "satın al",
        "satınalma",
        "hemen al",
        "sipariş ver",
        "siparis ver",
    )
    if any(x in t for x in in_stock):
        return "Var"

    return "Bilinmiyor"


def extract_mpn(text: str) -> str:
    patterns = [
        r"(?:ürün|stok|parça)\s*(?:kodu|no|numarası)\s*:?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,})",
        r"\bMPN\s*:?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def calculate_match_score(query: str, name: str, mpn: str = "") -> int:
    clean_query, _ = parse_query_quantity(query)
    q = clean_query.lower().strip()
    n = (name or "").lower()
    m = (mpn or "").lower()

    if not q:
        return 0

    score = 0
    if m and m == q:
        score += 60
    elif m and q in m:
        score += 45
    elif q in n:
        score += 40

    words = [w for w in re.findall(r"[a-z0-9çğıöşü.+/-]+", q) if len(w) > 1]
    if words:
        matched = sum(1 for w in words if w in n or w in m)
        score += round(40 * matched / len(words))

    return min(100, score)



def normalize_component_token(value: str) -> str:
    """Elektronik parça kodlarını karşılaştırma için normalize eder."""
    value = (value or "").lower()
    value = value.replace("×", "x")
    value = re.sub(r"[^a-z0-9çğıöşü]+", "", value)
    return value


def extract_manufacturer(text: str) -> str:
    patterns = [
        r"(?:üretici|marka|manufacturer)\s*:?\s*([A-Za-z0-9][A-Za-z0-9 ._-]{1,40})",
        r"(?:manufacturername|brand)\s*[:=]\s*[\"']?([A-Za-z0-9][A-Za-z0-9 ._-]{1,40})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            return m.group(1).strip(" ._-")
    return ""


def extract_package(text: str) -> str:
    patterns = [
        r"\b(DIP[- ]?\d+)\b",
        r"\b(SOIC[- ]?\d+)\b",
        r"\b(TSSOP[- ]?\d+)\b",
        r"\b(SSOP[- ]?\d+)\b",
        r"\b(QFN[- ]?\d+)\b",
        r"\b(QFP[- ]?\d+)\b",
        r"\b(LQFP[- ]?\d+)\b",
        r"\b(TQFP[- ]?\d+)\b",
        r"\b(TO[- ]?\d+)\b",
        r"\b(SMD)\b",
        r"\b(THT)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            return re.sub(r"\s+", "-", m.group(1).upper())
    return ""


def calculate_match_score(query: str, name: str, mpn: str = "", package: str = "", manufacturer: str = "") -> int:
    """V1.2 eşleşme: tam parça kodunu benzer kodlardan ayırır."""
    clean_query, _ = parse_query_quantity(query)
    q = clean_query.strip().lower()
    n = (name or "").lower()
    m = (mpn or "").lower()
    p = (package or "").lower()
    mf = (manufacturer or "").lower()

    if not q:
        return 0

    qn = normalize_component_token(q)
    mn = normalize_component_token(m)
    nn = normalize_component_token(n)

    score = 0

    # Tam MPN: en güçlü sinyal.
    if mn and mn == qn:
        score += 65
    elif mn and qn and qn in mn:
        score += 48

    # Ürün adında exact component token.
    if qn and qn in nn:
        score += 25

    # Kelime eşleşmesi.
    words = [w for w in re.findall(r"[a-z0-9çğıöşü.+/-]+", q) if len(w) > 1]
    if words:
        matched = sum(1 for w in words if w in n or w in m)
        score += round(20 * matched / len(words))

    # Kullanıcı açıkça paket/üretici yazdıysa bonus.
    query_package = extract_package(clean_query)
    query_manufacturer = extract_manufacturer(clean_query)

    if query_package:
        if p and normalize_component_token(query_package) == normalize_component_token(p):
            score += 10
        elif p:
            score -= 8

    if query_manufacturer:
        if mf and normalize_component_token(query_manufacturer) == normalize_component_token(mf):
            score += 10
        elif mf:
            score -= 8

    return max(0, min(100, score))


def enrich_product(product: Product, query: str, source_text: str = "") -> Product:
    combined = f"{product.name} {product.mpn} {source_text}"
    if not product.mpn:
        product.mpn = extract_mpn(combined)
    if product.stock == "Bilinmiyor":
        product.stock = detect_stock(combined)
    product.match_score = calculate_match_score(
        query,
        product.name,
        product.mpn,
        extract_package(combined),
        extract_manufacturer(combined),
    )
    return product


def extract_products(html: str, base_url: str, site_name: str, query: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]

    jsonld_results = extract_products_jsonld(soup, site_name, keywords)
    if jsonld_results and any(p.price is not None for p in jsonld_results):
        for p in jsonld_results:
            p.mpn = extract_mpn(p.name)
            p.stock = detect_stock(p.name)
            enrich_product(p, query, p.name)
        return jsonld_results

    if site_name == "Hepsiburada":
        products = []
        for card in soup.select('li[id^="i"]'):
            name_tag = card.select_one('h3[data-test-id="product-card-name"]')
            price_tag = card.select_one('div[data-test-id="price-current-price"]')
            link_tag = card.find("a", href=True)
            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)
                if keywords and not any(k in name.lower() for k in keywords):
                    continue
                price = parse_price(price_tag.get_text(strip=True).replace("TL", "").strip())
                href = link_tag["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                products.append(Product(site_name, name, price, full_url))
        return products

    if site_name == "Trendyol":
        products = []
        for card in soup.select(".p-card-wrppr"):
            name_tag = card.select_one(".prdct-desc-cntnr-name")
            price_tag = card.select_one(".prc-box-dscntd") or card.select_one(".prc-box-sllng")
            link_tag = card.find("a", href=True)
            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)
                if keywords and not any(k in name.lower() for k in keywords):
                    continue
                price = parse_price(price_tag.get_text(strip=True).replace("TL", "").strip())
                href = link_tag["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                products.append(Product(site_name, name, price, full_url))
        return products

    if site_name == "N11":
        products = []
        for card in soup.select(".column"):
            name_tag = card.select_one("h3.productName")
            price_tag = card.select_one("ins") or card.select_one(".newPrice")
            link_tag = card.find("a", href=True)
            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)
                if keywords and not any(k in name.lower() for k in keywords):
                    continue
                price = parse_price(price_tag.get_text(strip=True).replace("TL", "").strip())
                href = link_tag["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                products.append(Product(site_name, name, price, full_url))
        return products

    if site_name == "Amazon TR":
        products = []
        for card in soup.select('div[data-component-type="s-search-result"]'):
            name_tag = card.select_one("h2 a span")
            price_tag = card.select_one("span.a-price-whole")
            price_fraction = card.select_one("span.a-price-fraction")
            link_tag = card.select_one("h2 a")
            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)
                if keywords and not any(k in name.lower() for k in keywords):
                    continue
                p_text = price_tag.get_text(strip=True)
                if price_fraction:
                    p_text += "," + price_fraction.get_text(strip=True)
                price = parse_price(p_text)
                href = link_tag["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                products.append(Product(site_name, name, price, full_url))
        return products

    # Standart bileşen siteleri - genel ayrıştırıcı
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    results = []
    seen_urls = set()

    # YÖNTEM 1: Ürün kartının tamamı tek bir <a> içinde ise (çok yaygın kalıp),
    # linkin kendi metninden hem ismi hem fiyatı çıkar.
    badge_res = [
        re.compile(r"peşin fiyatına \d+ taksit", re.IGNORECASE),
        re.compile(r"\btaksit\b", re.IGNORECASE),
        re.compile(r"ücretsiz kargo", re.IGNORECASE),
        re.compile(r"stoktan teslim", re.IGNORECASE),
        re.compile(r"\byeni\b", re.IGNORECASE),
        re.compile(r"sepete ekle", re.IGNORECASE),
        re.compile(r"favorilere ekle", re.IGNORECASE),
        re.compile(r"i̇ncele|incele", re.IGNORECASE),
        re.compile(r"\(\s*\d+\s*\)"),
        re.compile(r"%\s*\d+"),
        re.compile(r"\d+\s*yorum", re.IGNORECASE),
        re.compile(r"stokta\s*yok", re.IGNORECASE),
    ]

    def clean_name(raw_text: str) -> str:
        t = raw_text
        for pat in badge_res:
            t = pat.sub(" ", t)
        t = PRICE_RE.sub(" ", t)
        t = re.sub(r"\s+", " ", t).strip(" -–|")
        return t.strip()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        full_text = a.get_text(separator=" ", strip=True)
        if not full_text:
            continue
        prices = PRICE_RE.findall(full_text)
        if not prices:
            continue  # fiyat içermeyen link muhtemelen kategori/menü linki
        name = clean_name(full_text)
        if not name or len(name) < 3:
            continue
        if keywords and not any(k in name.lower() for k in keywords):
            continue
        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        if full_url in seen_urls:
            continue
        price = parse_price(prices[-1])  # birden fazla fiyat varsa sonuncusu (indirimli/güncel fiyat)
        results.append(Product(site_name, name, price, full_url))
        seen_urls.add(full_url)

    if results:
        return results

    # YÖNTEM 2 (yedek): İsim ve fiyatın ayrı satırlarda olduğu eski kalıp
    link_queue = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if not text or not href or text.lower() in IGNORE_LINK_TEXT or href.startswith("#") or "javascript:" in href:
            continue
        if keywords and not any(k in text.lower() for k in keywords):
            continue
        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        link_queue.append((text, full_url))

    raw_lines = [ln.strip() for ln in soup.get_text(separator="\n").split("\n") if ln.strip()]
    lines = []
    i = 0
    while i < len(raw_lines):
        cur = raw_lines[i]
        if i + 1 < len(raw_lines) and NUM_ONLY_RE.match(cur) and raw_lines[i + 1].strip().upper() in ("TL", "₺", "TRY"):
            lines.append(f"{cur} TL")
            i += 2
        else:
            lines.append(cur)
            i += 1

    link_idx = 0
    candidate_name = None
    candidate_url = None
    gap_counter = 0

    for line in lines:
        if link_idx < len(link_queue) and line == link_queue[link_idx][0]:
            candidate_name, candidate_url = link_queue[link_idx]
            link_idx += 1
            gap_counter = 0
            continue

        price_match = PRICE_RE.search(line)
        if price_match and candidate_name:
            if candidate_url not in seen_urls:
                price = parse_price(price_match.group(1))
                results.append(Product(site_name, candidate_name, price, candidate_url))
                seen_urls.add(candidate_url)
            candidate_name = None
            candidate_url = None
            continue

        if candidate_name:
            gap_counter += 1
            if gap_counter > 30:
                candidate_name = None
                candidate_url = None

    # Son güvenlik katmanı: bütün sonuçlara MPN/stok/eşleşme bilgisi ekle.
    for p in results:
        if not p.mpn:
            p.mpn = extract_mpn(p.name)
        if p.stock == "Bilinmiyor":
            p.stock = detect_stock(p.name)
        enrich_product(p, query, p.name)

    return results


_virtual_display = None


def get_driver(stealth: bool = False):
    global _virtual_display
    options = Options()

    display_ready = False
    if stealth:
        # Cloudflare gibi gelişmiş bot tespiti olan siteler için:
        # headless kapalı + sanal ekran (Xvfb) kullan, daha fazla iz gizle.
        if _virtual_display is None:
            try:
                from pyvirtualdisplay import Display
                _virtual_display = Display(visible=0, size=(1920, 1080))
                _virtual_display.start()
                display_ready = True
            except Exception:
                _virtual_display = False  # tekrar denemesin
        elif _virtual_display is not False:
            display_ready = True

    if not display_ready:
        # Sanal ekran yoksa (kurulamadıysa) güvenli şekilde headless kullan
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=tr-TR")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    binary_candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    for path in binary_candidates:
        if os.path.exists(path):
            options.binary_location = path
            break

    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            # Son çare: stealth/görünür mod hiç çalışmadıysa zorla headless dene
            if "--headless=new" not in options.arguments:
                options.add_argument("--headless=new")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)

    stealth_js = """ Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]}); Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']}); window.chrome = { runtime: {} }; const originalQuery = window.navigator.permissions.query; window.navigator.permissions.query = (parameters) => ( parameters.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : originalQuery(parameters) ); """
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
    return driver


def wait_for_real_content(driver, timeout=15, min_matches=2):
    """Sayfada gerçek ürün fiyatları görünene kadar bekler. Tek bir eşleşme (kargo bedava banner'ı gibi) yanlış pozitif olabileceği için en az min_matches kadar fiyat deseni arar."""
    js_check = """ const t = document.body.innerText || ''; const matches = t.match(/\\d[\\d.,]*\\s*(TL|₺)/g) || []; return matches.length; """
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            count = driver.execute_script(js_check)
            if count and count >= min_matches:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def dismiss_cookie_banner(driver):
    """Yaygın çerez/onay/bildirim popup'larını otomatik kapatmayı dener."""
    texts = [
        "kabul et", "kabul ediyorum", "onayla", "onaylıyorum", "tümünü kabul et",
        "accept", "accept all", "i agree", "anladım", "tamam", "izin ver",
        "daha sonra", "şimdi değil", "hayır", "vazgeç", "kapat", "reddet",
        "not now", "no thanks", "dismiss", "close",
    ]
    js = """ const texts = arguments[0]; const nodes = document.querySelectorAll('button, a, div[role="button"], span[role="button"]'); for (const el of nodes) { const t = (el.innerText || '').trim().toLowerCase(); if (t && texts.some(x => t.includes(x)) && t.length < 40) { el.click(); return true; } } // Genel kapatma ikonlarını dene (aria-label ile) const closeEls = document.querySelectorAll( '[aria-label*="close" i], [aria-label*="kapat" i], .close, .modal-close, .popup-close' ); for (const el of closeEls) { el.click(); return true; } return false; """
    try:
        driver.execute_script(js, texts)
        # ESC tuşu da bazı modalleri kapatır
        from selenium.webdriver.common.keys import Keys
        driver.find_element("tag name", "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass


def scrape_site(site: str, url_tmpl: str, query: str):
    for attempt in range(2):  # zaman aşımında bir kez daha dene
        result = _scrape_site_once(site, url_tmpl, query)
        status = result[2]
        if "TimeoutException" not in status or attempt == 1:
            return result
    return result


def _scrape_site_once(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]

    driver = None
    try:
        driver = get_driver(stealth=(site in CLOUDFLARE_SITES))
        driver.set_page_load_timeout(35)
        driver.get(url)

        time.sleep(1.0)
        dismiss_cookie_banner(driver)
        time.sleep(0.5)

        selector = SITE_WAIT_SELECTORS.get(site)
        if selector:
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            except Exception:
                pass
        elif site in SLOW_AJAX_SITES or site in CLOUDFLARE_SITES:
            wait_for_real_content(driver, timeout=20)
        else:
            time.sleep(3.0)

        dismiss_cookie_banner(driver)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(1.0)

        html = driver.page_source
        products = extract_products(html, base_url, site, query)

        debug_png = None
        debug_html_snippet = None
        if not products:
            try:
                debug_png = driver.get_screenshot_as_png()
            except Exception:
                pass
            try:
                body_text = driver.execute_script("return document.body.innerText || '';")
            except Exception:
                body_text = ""
            price_matches = len(re.findall(r"\d[\d.,]*\s*(?:TL|₺)", body_text))

            # Gerçek bir ürün adını bul (banner değil): body_text'te bir satırın
            # hemen ardından fiyat satırı geliyorsa, o satır muhtemelen ürün adıdır.
            raw_html_snippet = ""
            body_lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
            price_line_re = re.compile(r"^\d[\d.,]*\s*(?:TL|₺)$", re.IGNORECASE)
            candidate_name_line = None
            for idx in range(len(body_lines) - 1):
                if price_line_re.match(body_lines[idx + 1]) and len(body_lines[idx]) > 8 and not price_line_re.match(body_lines[idx]):
                    candidate_name_line = body_lines[idx]
                    break

            if candidate_name_line:
                pos = html.find(candidate_name_line)
                if pos == -1:
                    # HTML entity kaçışları yüzünden bulunamadıysa ilk birkaç kelimeyi dene
                    first_word = candidate_name_line.split(" ")[0]
                    pos = html.find(first_word)
                if pos != -1:
                    start = max(0, pos - 1000)
                    end = min(len(html), pos + 800)
                    raw_html_snippet = html[start:end]

            if not raw_html_snippet:
                m = re.search(r"\d[\d.,]*\s*(?:TL|₺)", html)
                if m:
                    start = max(0, m.start() - 1200)
                    end = min(len(html), m.end() + 300)
                    raw_html_snippet = html[start:end]

            debug_html_snippet = (
                f"[TOPLAM HTML UZUNLUĞU: {len(html)} karakter]\n"
                f"[GÖRÜNÜR METİNDE FİYAT DESENİ SAYISI: {price_matches}]\n\n"
                f"--- GÖRÜNÜR SAYFA METNİ (ilk 2500 karakter) ---\n"
                f"{body_text[:2500]}\n\n"
                f"--- İLK FİYATIN ETRAFINDAKİ HAM HTML ---\n"
                f"{raw_html_snippet}"
            )

        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status, debug_png, debug_html_snippet
    except Exception as e:
        return site, [], f"Bağlantı Hatası / Engellendi ({e.__class__.__name__})", None, None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def search_all_selenium(query: str):
    clean_query, quantity = parse_query_quantity(query)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(scrape_site, site, tmpl, clean_query): site
            for site, tmpl in SEARCH_URL_TEMPLATES.items()
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                site, products, status, debug_png, debug_html = future.result()
                for product in products:
                    product.quantity = quantity
                    enrich_product(product, clean_query, product.name)
                results.append((site, products, status, debug_png, debug_html))
            except Exception:
                pass
    return results


def search_basket(items: list[str], progress_callback=None) -> dict:
    """Sepetteki her ürün için tüm siteleri arar. Döner: {ürün: site_results}."""
    all_results = {}
    for idx, item in enumerate(items):
        if progress_callback:
            progress_callback(idx, item)
        all_results[item] = search_all_selenium(item)
    return all_results


def build_basket_comparison(items: list[str], all_results: dict) -> dict:
    """Her site için sepetteki ürünlerin en ucuz eşleşmesini bulup toplar."""
    comparison = {
        site: {"total": 0.0, "found_count": 0, "missing": [], "picks": {}}
        for site in SEARCH_URL_TEMPLATES
    }

    for item in items:
        site_results = all_results.get(item, [])
        found_sites = set()
        for site, prods, status, _debug_png, _debug_html in site_results:
            priced = [p for p in prods if p.price is not None]
            if priced:
                cheapest = min(priced, key=lambda p: p.price)
                comparison[site]["total"] += cheapest.price * max(1, cheapest.quantity)
                comparison[site]["found_count"] += 1
                comparison[site]["picks"][item] = cheapest
                found_sites.add(site)
        for site in SEARCH_URL_TEMPLATES:
            if site not in found_sites:
                comparison[site]["missing"].append(item)

    return comparison


st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")
st.caption("Türkiye'deki elektronik komponent mağazalarını tek aramada karşılaştır.")

f1, f2, f3, f4 = st.columns(4)
f1.metric("🏪 Mağaza", len(SEARCH_URL_TEMPLATES))
f2.metric("🎯 Eşleşme", "Akıllı")
f3.metric("📦 Stok", "Kontrol")
f4.metric("💰 Fiyat", "Karşılaştır")

tab_single, tab_basket = st.tabs(["🔍 Tek Ürün", "🛒 Sepet Karşılaştırma"])

with tab_single:
    query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

    if st.button("Fiyatları Getir", type="primary", use_container_width=True, key="single_search"):
        if not query.strip():
            st.warning("Lütfen bir ürün adı girin.")
        else:
            search_status = st.empty()
            search_status.markdown(
                "### 🔎 Mağazalar taranıyor...\n"
                "Ürünleri, fiyatları ve stok durumlarını karşılaştırıyorum."
            )

            # Küçük bir animasyon hissi: teknik bekleme mesajı yerine kullanıcıya
            # ne yapıldığını göster.
            with st.spinner("⚡ Fiyatlar karşılaştırılıyor..."):
                site_results = search_all_selenium(query)

            search_status.success("✅ Tarama tamamlandı — en uygun sonuçlar hazırlanıyor.")

            with st.expander("🔍 Site Tarama Durumları", expanded=False):
                for site, prods, status, debug_png, debug_html in site_results:
                    if "Hata" in status or "Engellendi" in status:
                        st.error(f"**{site}:** {status}")
                    elif "bulunamadı" in status:
                        st.warning(f"**{site}:** {status}")
                        if debug_png:
                            st.image(debug_png, caption=f"{site} - o an görünen sayfa", width=400)
                        if debug_html:
                            st.code(debug_html, language="html")
                    else:
                        st.success(f"**{site}:** {status}")

            all_products = [p for _, prods, _, _, _ in site_results for p in prods]

            if not all_products:
                st.error("Hiçbir sitede sonuç bulunamadı.")
            else:
                all_products.sort(
                    key=lambda p: (-p.match_score, p.price is None, p.price or 0)
                )
                data = [
                    {
                        "Site": r.site,
                        "Eşleşme": f"%{r.match_score}",
                        "Stok": r.stock,
                        "MPN": r.mpn,
                        "Birim Fiyat": f"{r.price:,.2f} TL" if r.price is not None else "—",
                        "Adet": r.quantity,
                        "Toplam": (
                            f"{r.price * r.quantity:,.2f} TL"
                            if r.price is not None else "—"
                        ),
                        "Ürün Adı": r.name,
                        "Link": r.url,
                    }
                    for r in all_products
                ]
                df = pd.DataFrame(data)
                st.dataframe(
                    df,
                    column_config={"Link": st.column_config.LinkColumn("Satın Al")},
                    hide_index=True,
                    use_container_width=True,
                )

with tab_basket:
    st.caption("Her satıra bir ürün yazın. Her ürün tüm sitelerde aranıp, hangi sitenin sepetin tamamını en ucuza karşıladığı hesaplanır.")
    basket_text = st.text_area(
        "Sepetiniz (her satıra bir ürün):",
        placeholder="esp32\nhc05\n1k direnç\nbreadboard",
        height=150,
    )

    if st.button("Sepeti Karşılaştır", type="primary", use_container_width=True, key="basket_search"):
        items = [ln.strip() for ln in basket_text.split("\n") if ln.strip()]
        if not items:
            st.warning("Lütfen en az bir ürün girin.")
        else:
            st.info(
                f"🛒 {len(items)} ürün için mağaza fiyatları karşılaştırılıyor. "
                "Sonuçlar tamamlandıkça toplam sepet maliyeti hesaplanacak."
            )
            progress_bar = st.progress(0, text="Sepet hazırlanıyor...")

            def _update_progress(idx, item):
                progress_bar.progress(
                    idx / len(items),
                    text=f"({idx + 1}/{len(items)}) '{item}' tüm sitelerde aranıyor...",
                )

            all_results = search_basket(items, progress_callback=_update_progress)
            progress_bar.progress(1.0, text="Tamamlandı!")

            comparison = build_basket_comparison(items, all_results)

            # Tam sepeti karşılayan (hiçbir eksik olmayan) siteleri önce, sonra en ucuzdan pahalıya sırala.
            # Eksik ürünü olanları da en az eksikten en ucuza doğru sırala.
            ranked_sites = sorted(
                comparison.items(),
                key=lambda kv: (len(kv[1]["missing"]), kv[1]["total"] if kv[1]["found_count"] > 0 else float("inf")),
            )

            full_coverage = [(s, d) for s, d in ranked_sites if not d["missing"] and d["found_count"] > 0]

            if full_coverage:
                best_site, best_data = full_coverage[0]
                st.success(
                    f"🏆 **En ucuz tam sepet: {best_site}** — Toplam: **{best_data['total']:,.2f} TL** "
                    f"({best_data['found_count']}/{len(items)} ürün bulundu)"
                )
            else:
                st.warning("Sepetin tamamını tek başına karşılayan bir site bulunamadı. Aşağıda en iyi kısmi eşleşmeler listeleniyor.")

            summary_rows = []
            for site, d in ranked_sites:
                if d["found_count"] == 0:
                    continue
                summary_rows.append({
                    "Site": site,
                    "Bulunan": f"{d['found_count']}/{len(items)}",
                    "Toplam Fiyat": f"{d['total']:,.2f} TL",
                    "Eksik Ürünler": ", ".join(d["missing"]) if d["missing"] else "—",
                })

            if summary_rows:
                st.subheader("📊 Site Karşılaştırması")
                st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)
            else:
                st.error("Hiçbir sitede sepetteki ürünlerden herhangi biri bulunamadı.")

            with st.expander("📋 Ürün Bazında Detay (hangi site hangi fiyatı verdi)", expanded=False):
                for item in items:
                    st.markdown(f"**{item}**")
                    item_rows = []
                    for site, d in ranked_sites:
                        pick = d["picks"].get(item)
                        if pick:
                            item_rows.append({
                                "Site": site,
                                "Birim Fiyat": f"{pick.price:,.2f} TL",
                                "Adet": pick.quantity,
                                "Toplam": f"{pick.price * max(1, pick.quantity):,.2f} TL",
                                "Eşleşme": f"%{pick.match_score}",
                                "Stok": pick.stock,
                                "Ürün Adı": pick.name,
                                "Link": pick.url,
                            })
                    if item_rows:
                        item_rows.sort(key=lambda r: float(r["Birim Fiyat"].replace(" TL", "").replace(".", "").replace(",", ".")))
                        st.dataframe(
                            pd.DataFrame(item_rows),
                            column_config={"Link": st.column_config.LinkColumn("Satın Al")},
                            hide_index=True,
                            use_container_width=True,
                        )
                    else:
                        st.caption("Hiçbir sitede bulunamadı.")        # Kullanıcıya yalnızca sade tarama sonucu göster.
        found_count = sum(1 for _, prods, _, _, _ in site_results if prods)
        total_sites = len(SEARCH_URL_TEMPLATES)

        with st.expander("🔍 Site Tarama Durumları", expanded=False):
            cols = st.columns(3)
            for idx, (site, prods, status, _debug_png, _debug_html) in enumerate(site_results):
                col = cols[idx % 3]
                if prods:
                    col.success(f"**{site}** — bulundu")
                else:
                    col.info(f"**{site}** — bulunamadı")

        st.caption(
            f"🔎 {total_sites} site tarandı • "
            f"{found_count} sitede bulundu • "
            f"{total_sites - found_count} sitede bulunamadı"
        )
