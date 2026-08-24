# app.py
# Gereksinimler: streamlit, selenium, webdriver-manager, beautifulsoup4, lxml, pandas, requests
# pip install streamlit selenium webdriver-manager beautifulsoup4 lxml pandas requests

import concurrent.futures
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Konfigürasyon ----------
SEARCH_SITES: Dict[str, Dict[str, Any]] = {
    "Robotistan": {"template": "https://www.robotistan.com/arama?q={query}", "requires_js": False},
    "Motorobit": {"template": "https://www.motorobit.com/arama?q={query}", "requires_js": True},
    "Samm Market": {"template": "https://market.samm.com/search?s={query}", "requires_js": True},
    "Robolink": {"template": "https://www.robolinkmarket.com/?search_provider=aisearch&query={query}&page=1", "requires_js": True},
    "Robocombo": {"template": "https://www.robocombo.com/Arama?1&kelime={query}", "requires_js": False},
    "Kartal Otomasyon": {"template": "https://www.kartalotomasyon.com.tr/arama/{query}", "requires_js": False},
    "F1 Depo": {"template": "https://www.f1depo.com/arama/{query}", "requires_js": False},
    "Robotzade": {"template": "https://www.robotzade.com/arama/{query}", "requires_js": False},
}

DEBUG_DIR = "debug_snapshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

IGNORE_LINK_TEXT = {
    "add to cart", "sepete ekle", "favorilere ekle", "add to favorites",
    "incele", "javascript:void(0);", "see all", "tümü", "detay",
    "giriş yap", "üye ol", "sipariş takibi", "iletişim", "kategoriler", "yardım",
    "hesabım", "sepetim", "günün fırsatları", "müşteri hizmetleri", "satış yap",
}

PRICE_RE = re.compile(r"([\d]{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺|TRY)?", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,\s]+$")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

_CLEAN_RE = re.compile(r"[^\d,.\-]")

# ---------- Veri modeli ----------
@dataclass
class Product:
    site: str
    name: str
    price: Optional[float]
    url: str

# ---------- Yardımcı fonksiyonlar ----------
def parse_price(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    s = str(raw).strip()
    s = _CLEAN_RE.sub("", s)
    if not s:
        return None
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None

def build_search_url(template: str, query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    return template.format(query=encoded)

def url_join(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def normalize_text(s: str) -> str:
    return " ".join(s.lower().split())

def is_relevant(name: str, keywords: List[str], threshold: float) -> bool:
    if not keywords:
        return True
    lname = name.lower()
    matched = sum(1 for k in keywords if k in lname)
    ratio = matched / len(keywords)
    return ratio >= threshold

def save_debug(site: str, html: Optional[str], png: Optional[bytes]) -> Tuple[Optional[str], Optional[str]]:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    html_path = None
    png_path = None
    if html:
        html_path = os.path.join(DEBUG_DIR, f"{site}_{ts}.html")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            logger.debug("debug html kaydedilemedi: %s", e)
            html_path = None
    if png:
        png_path = os.path.join(DEBUG_DIR, f"{site}_{ts}.png")
        try:
            with open(png_path, "wb") as f:
                f.write(png)
        except Exception as e:
            logger.debug("debug png kaydedilemedi: %s", e)
            png_path = None
    return html_path, png_path

# ---------- Parsers ----------
def parse_generic(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.5) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    link_queue: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        ntext = normalize_text(text)
        if ntext in IGNORE_LINK_TEXT or ntext.startswith("#") or "javascript:" in a["href"]:
            continue
        href = url_join(base_url, a["href"])
        link_queue.append((text.strip(), href))
    raw_lines = [ln.strip() for ln in soup.get_text(separator="\n").split("\n") if ln.strip()]
    lines: List[str] = []
    i = 0
    while i < len(raw_lines):
        cur = raw_lines[i].strip()
        if i + 1 < len(raw_lines) and NUM_ONLY_RE.match(cur) and raw_lines[i + 1].strip().upper() in ("TL", "₺", "TRY"):
            lines.append(f"{cur} TL")
            i += 2
        else:
            lines.append(cur)
            i += 1
    results: List[Product] = []
    seen_urls = set()
    for (link_text, link_url) in link_queue:
        if link_url in seen_urls:
            continue
        if not is_relevant(link_text, keywords, relevance_threshold):
            found_idx = None
            for idx, l in enumerate(lines):
                if link_text in l:
                    found_idx = idx
                    break
            found_keyword_nearby = False
            if found_idx is not None:
                window = lines[max(0, found_idx - 2): found_idx + 4]
                for w in window:
                    lw = w.lower()
                    if any(k in lw for k in keywords):
                        found_keyword_nearby = True
                        break
            if not found_keyword_nearby:
                continue
        price = None
        found_idx = None
        for idx, l in enumerate(lines):
            if link_text in l:
                found_idx = idx
                break
        if found_idx is not None:
            for w in lines[found_idx: found_idx + 6]:
                m = PRICE_RE.search(w)
                if m:
                    price = parse_price(m.group(1))
                    break
        if price is None:
            for w in lines[:80]:
                m = PRICE_RE.search(w)
                if m:
                    price = parse_price(m.group(1))
                    break
        results.append(Product(site=site, name=link_text, price=price, url=link_url))
        seen_urls.add(link_url)
    return results

def parse_sammmarket(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.5) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    candidates: List[Product] = []
    card_selectors = [
        ".product", ".product-card", ".product-item", ".product-list .product", ".prd", ".productBox", ".prduct", ".product-grid-item"
    ]
    price_selectors = [".price", ".product-price", ".prd-price", ".price-new", ".prc", ".price-item", ".amount", ".priceLabel"]
    for sel in card_selectors:
        cards = soup.select(sel)
        if not cards:
            continue
        for card in cards:
            name_tag = card.select_one("h3") or card.select_one(".title") or card.select_one("a") or card.select_one(".name")
            link_tag = card.find("a", href=True)
            price_tag = None
            for ps in price_selectors:
                price_tag = price_tag or card.select_one(ps)
            if not name_tag:
                name_attr = card.get("data-name") or card.get("data-title")
                if name_attr:
                    name_text = name_attr.strip()
                else:
                    continue
            else:
                name_text = name_tag.get_text(" ", strip=True)
            if not link_tag:
                href = card.get("data-href") or card.get("data-url") or None
                full = url_join(base_url, href) if href else base_url
            else:
                full = url_join(base_url, link_tag["href"])
            price = None
            if price_tag:
                price = parse_price(price_tag.get_text(" ", strip=True))
            if is_relevant(name_text, keywords, relevance_threshold):
                candidates.append(Product(site=site, name=name_text, price=price, url=full))
        if candidates:
            logger.debug("parse_sammmarket: selector %s ile %d ürün bulundu", sel, len(candidates))
            break
    if not candidates:
        logger.debug("parse_sammmarket: kart bulunamadı, generic fallback")
        candidates = parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)
    return candidates

def parse_robotzade(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.5) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []
    text = soup.get_text(" ", strip=True)
    m = re.search(r"kdv\s*dahil\s*fiyat[iı]?\s*[:\-]?\s*([\d\.,\s]+)\s*(?:TL|₺|TRY)?", text, re.IGNORECASE)
    if m:
        price_val = parse_price(m.group(1))
        title_tag = soup.find(["h1", "h2", "title"])
        name = title_tag.get_text(" ", strip=True) if title_tag else query
        canonical = None
        can_tag = soup.find("link", rel="canonical")
        if can_tag and can_tag.get("href"):
            canonical = url_join(base_url, can_tag["href"])
        else:
            canonical = base_url
        if is_relevant(name, keywords, relevance_threshold):
            results.append(Product(site=site, name=name, price=price_val, url=canonical))
            return results
    texts_with_kdv = soup.find_all(string=re.compile(r"kdv", re.IGNORECASE))
    for txt in texts_with_kdv:
        parent = txt.parent
        for _ in range(4):
            if parent is None:
                break
            block_text = parent.get_text(" ", strip=True)
            m2 = PRICE_RE.search(block_text)
            if m2:
                price_val = parse_price(m2.group(1))
                h = soup.find(["h1", "h2"])
                name = h.get_text(" ", strip=True) if h else txt.strip()
                link = base_url
                if is_relevant(name, keywords, relevance_threshold):
                    results.append(Product(site=site, name=name, price=price_val, url=link))
                    return results
            parent = parent.parent
    price_candidates = []
    for selector in [".price", ".product-price", ".prd-price", ".price-new", ".amount", ".prc", ".priceLabel"]:
        for el in soup.select(selector):
            txt = el.get_text(" ", strip=True)
            m3 = PRICE_RE.search(txt)
            if m3:
                parent_text = ""
                if el.parent:
                    parent_text = el.parent.get_text(" ", strip=True)
                if re.search(r"kdv", parent_text, re.IGNORECASE) or re.search(r"kdv", txt, re.IGNORECASE):
                    price_candidates.append((el, parse_price(m3.group(1))))
    if price_candidates:
        el, price_val = price_candidates[0]
        h = soup.find(["h1", "h2"])
        name = h.get_text(" ", strip=True) if h else (el.get("aria-label") or query)
        link = base_url
        if is_relevant(name, keywords, relevance_threshold):
            results.append(Product(site=site, name=name, price=price_val, url=link))
            return results
    logger.debug("parse_robotzade: site-özel tespit yok, generic fallback")
    return parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)

PARSERS: Dict[str, Any] = {
    "Samm Market": parse_sammmarket,
    "Robotzade": parse_robotzade,
    # ek site-özel parser'lar buraya
}

# ---------- Fetch katmanı ----------
def fetch_page_requests(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text, None
    except Exception as e:
        logger.debug("requests fetch hata: %s -> %s", url, e)
        return None, str(e)

def get_driver(chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, headless: bool = True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f'--user-agent={DEFAULT_HEADERS["User-Agent"]}')
    binary_candidates = [chrome_binary] if chrome_binary else ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
    for path in binary_candidates:
        if path and os.path.exists(path):
            options.binary_location = path
            break
    try:
        if driver_path and os.path.exists(driver_path):
            service = Service(driver_path)
        else:
            service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        logger.debug("Local chromedriver kullanıldı")
        return driver
    except Exception:
        logger.debug("Local chromedriver başarısız, webdriver-manager ile kurulum deneniyor")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'},
            )
        except Exception:
            logger.debug("CDP script eklenemedi")
        return driver
    except Exception as e:
        logger.exception("Chromedriver başlatılamadı")
        raise RuntimeError("Chromedriver başlatılamadı") from e

def fetch_page_selenium(url: str, wait_for_content: bool = False, timeout: int = 20, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, debug: bool = False) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    driver = None
    try:
        driver = get_driver(chrome_binary=chrome_binary, driver_path=driver_path, headless=not debug)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        time.sleep(0.8)
        try:
            driver.execute_script("""
            try {
                const nodes = document.querySelectorAll('button, a, div[role="button"], span[role="button"]');
                for (const el of nodes) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t && ['kabul et','accept all','accept','tamam','anladım','izin ver'].some(x => t.includes(x))) { el.click(); break; }
                }
            } catch(e) {}
            """)
        except Exception:
            pass
        if wait_for_content:
            end = time.time() + timeout
            while time.time() < end:
                try:
                    t = driver.execute_script("return document.body && document.body.innerText ? document.body.innerText : ''")
                    if re.search(r"\d[\d.,\s]*\s*(TL|₺)", str(t)):
                        break
                except Exception:
                    pass
                time.sleep(0.5)
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        except Exception:
            pass
        time.sleep(0.6)
        html = driver.page_source
        screenshot = None
        try:
            screenshot = driver.get_screenshot_as_png()
        except Exception:
            screenshot = None
        # Eğer screenshot None veya görünür değil görünüyorsa element bazlı screenshot dene (fiyata yakın)
        if (not screenshot) or (screenshot and len(screenshot) < 1000):
            # element selector denemeleri
            price_selectors = ["[class*=price]", "[id*=price]", ".price", ".amount", ".product-price", ".priceLabel"]
            for sel in price_selectors:
                try:
                    elems = driver.find_elements(By.CSS_SELECTOR, sel)
                    if elems:
                        try:
                            # en büyük elementi al
                            el = max(elems, key=lambda e: (e.size['height'] * e.size['width'] if e.size else 0))
                            screenshot = el.screenshot_as_png
                            if screenshot and len(screenshot) > 500:
                                break
                        except Exception:
                            continue
                except Exception:
                    continue
        return html, screenshot, None
    except TimeoutException as e:
        logger.warning("Selenium timeout: %s", e)
        return None, None, f"Timeout: {e}"
    except WebDriverException as e:
        logger.exception("Selenium WebDriver hata")
        return None, None, str(e)
    except Exception as e:
        logger.exception("Selenium fetch hata")
        return None, None, str(e)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

# ---------- Site scraping orchestration ----------
def scrape_site(site: str, template: str, query: str, requires_js: bool, relevance_threshold: float, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, debug: bool = False) -> Tuple[str, List[Product], str, Optional[bytes], Optional[str]]:
    url = build_search_url(template, query)
    base_url = urllib.parse.urlparse(url).scheme + "://" + urllib.parse.urlparse(url).netloc
    html = None
    debug_png = None
    error_msg = None
    if requires_js:
        html, debug_png, error_msg = fetch_page_selenium(url, wait_for_content=True, timeout=25, chrome_binary=chrome_binary, driver_path=driver_path, debug=debug)
        if error_msg:
            status = f"Bağlantı Hatası / Engellendi ({error_msg})"
            return site, [], status, None, None
    else:
        html, error = fetch_page_requests(url, timeout=12)
        if error or not html:
            html, debug_png, error_msg = fetch_page_selenium(url, wait_for_content=False, timeout=20, chrome_binary=chrome_binary, driver_path=driver_path, debug=debug)
            if error_msg and not html:
                status = f"Bağlantı Hatası / Engellendi ({error_msg or error})"
                return site, [], status, None, None
    if debug:
        html_path, png_path = save_debug(site, html, debug_png)
        logger.info("Debug saved: html=%s png=%s", html_path, png_path)
    parser = PARSERS.get(site)
    try:
        if parser is not None:
            # Samm Market ve Robotzade parser'ları relevance parametresini alır
            products = parser(html, base_url, site, query, relevance_threshold=relevance_threshold)
        else:
            products = parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)
        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        debug_html_snippet = html[:3000] if debug and html else None
        return site, products, status, debug_png, debug_html_snippet
    except Exception as e:
        logger.exception("Parsing sırasında hata: %s", e)
        return site, [], f"Ayrıştırma Hatası ({e.__class__.__name__})", debug_png, (html[:3000] if html else None)

def search_all(query: str, max_workers: int = 3, relevance_threshold: float = 0.5, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, debug: bool = False) -> List[Tuple[str, List[Product], str, Optional[bytes], Optional[str]]]:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scrape_site, site, cfg["template"], query, cfg.get("requires_js", False), relevance_threshold, chrome_binary, driver_path, debug): site
            for site, cfg in SEARCH_SITES.items()
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                logger.exception("search_all thread hata")
    return results

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Refactor - Debug Mode)")

with st.sidebar:
    st.header("Ayarlar")
    max_workers = st.slider("Eşzamanlı site tarama (max_workers)", 1, 4, 2)
    relevance_threshold = st.slider("Alaka eşiği (0 gevşek - 1 sıkı)", 0.0, 1.0, 0.5, 0.1)
    debug_mode = st.checkbox("Debug modu (headless kapalı, HTML/PNG kaydı)", value=False)
    chrome_bin = st.text_input("Chrome/Chromium binary path (opsiyonel)", value="")
    driver_path = st.text_input("Chromedriver path (opsiyonel)", value="")

query = st.text_input("Aranacak Komponent (örn: mp1584, esp32, direnç 10k):", placeholder="örn: esp32, direnç 10k, mp1584")

def format_price_for_display(p: Optional[float]) -> str:
    if p is None:
        return "Fiyat Çekilemedi"
    try:
        s = f"{p:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} TL"
    except Exception:
        return f"{p} TL"

if st.button("Fiyatları Getir", type="primary"):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"{len(SEARCH_SITES)} site aranıyor..."):
            site_results = search_all(query.strip(), max_workers=max_workers, relevance_threshold=relevance_threshold, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None), debug=debug_mode)
        with st.expander("🔍 Site Tarama Durumları", expanded=True):
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
                    st.success(f"**{site}:** {status} (örn: {len(prods)} ürün)")
        all_products = [p for _, prods, _, _, _ in site_results for p in prods]
        if not all_products:
            st.error("Hiçbir sitede sonuç bulunamadı.")
        else:
            all_products.sort(key=lambda p: (p.price is None, p.price or 0))
            rows = []
            for r in all_products:
                rows.append({
                    "Site": r.site,
                    "Fiyat": format_price_for_display(r.price),
                    "Ürün Adı": r.name,
                    "Link": r.url,
                })
            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=["Link"]), use_container_width=True)
            with st.expander("Satın Alma Bağlantıları", expanded=False):
                for r in all_products:
                    st.markdown(f"- **{r.site}** — [{r.name}]({r.url}) — {format_price_for_display(r.price)}")
            csv = pd.DataFrame(rows).to_csv(index=False)
            st.download_button("Sonuçları CSV indir", csv, file_name="fiyatlar.csv", mime="text/csv")
st.caption("Not: Selenium ile yapılan istekler bazı sitelerin kullanım şartlarını ihlal edebilir veya IP bloklamaya sebep olabilir. Debug modunda headless kapalı çalışır; server ortamında GUI yoksa headless=False başarısız olabilir.")
