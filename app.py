# app.py
# Gereksinimler:
# pip install streamlit requests beautifulsoup4 lxml pandas selenium webdriver-manager

import os
import re
import time
import urllib.parse
import concurrent.futures as cf
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# Selenium (fallback / JS-rendered sayfalar için)
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# --- Konfig ---
DEBUG_DIR = "debug_snapshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {"User-Agent": USER_AGENT}

# Arama şablonları - önemli siteler
SEARCH_SITES: Dict[str, str] = {
    "Motorobit": "https://www.motorobit.com/arama?q={query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
}

# Site-özel bekleyiciler (Selenium için)
SITE_WAIT_SELECTORS: Dict[str, str] = {
    "Motorobit": ".product-card, .products, .product-list, .product",
    "Samm Market": ".product, .product-list",
    "Robocombo": ".product, .product-card",
}

# Fiyat regex
PRICE_RE = re.compile(
    r"([\d]{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺|TRY)",
    re.IGNORECASE,
)
_CLEAN_RE = re.compile(r"[^\d,.\-]")

# --- Model ---
@dataclass
class Product:
    site: str
    name: str
    price: Optional[float]
    url: str

# --- Yardımcılar ---
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
    except Exception:
        return None

def url_join(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def save_debug(site: str, html: Optional[str], png: Optional[bytes]) -> Tuple[Optional[str], Optional[str]]:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    html_path = None
    png_path = None
    try:
        if html:
            html_path = os.path.join(DEBUG_DIR, f"{site}_{ts}.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        if png:
            png_path = os.path.join(DEBUG_DIR, f"{site}_{ts}.png")
            with open(png_path, "wb") as f:
                f.write(png)
    except Exception:
        pass
    return html_path, png_path

# --- Fetch: requests ve selenium fallback ---
def fetch_page_requests(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text, None
    except Exception as e:
        return None, str(e)

def get_driver(headless: bool = True, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None):
    options = Options()
    if headless:
        # bazı Chrome sürümlerinde "--headless=new" deneyebilirsin
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f'--user-agent={USER_AGENT}')
    if chrome_binary and os.path.exists(chrome_binary):
        options.binary_location = chrome_binary

    try:
        if driver_path and os.path.exists(driver_path):
            service = Service(driver_path)
            return webdriver.Chrome(service=service, options=options)
        # yerel sürücü yoksa webdriver-manager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'},
            )
        except Exception:
            pass
        return driver
    except Exception as e:
        raise RuntimeError("Chromedriver başlatılamadı: " + str(e))

def fetch_page_selenium(
    url: str,
    wait_selector: Optional[str] = None,
    wait_for_content: bool = False,
    timeout: int = 20,
    headless: bool = True,
    chrome_binary: Optional[str] = None,
    driver_path: Optional[str] = None,
) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    driver = None
    try:
        driver = get_driver(headless=headless, chrome_binary=chrome_binary, driver_path=driver_path)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        # küçük bekleme
        time.sleep(0.7)
        # cookie/banner kapatma denemesi
        try:
            driver.execute_script(
                """
                try {
                  document.querySelectorAll('button, a, div[role="button"]').forEach(el => {
                    const t = (el.innerText||'').toLowerCase();
                    if (t.includes('kabul') || t.includes('accept') || t.includes('tamam')) { el.click(); }
                  });
                } catch(e) {}
                """
            )
        except Exception:
            pass

        # site-özel selector bekle
        if wait_selector:
            try:
                WebDriverWait(driver, min(25, timeout)).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                # yinede devam et
                pass
        elif wait_for_content:
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
        time.sleep(0.7)
        html = driver.page_source
        screenshot = None
        try:
            screenshot = driver.get_screenshot_as_png()
        except Exception:
            screenshot = None

        # Eğer screenshot küçükse, element bazlı dene (fiyat elemanı)
        if (not screenshot) or (screenshot and len(screenshot) < 1000):
            try:
                price_elems = driver.find_elements(By.CSS_SELECTOR, "[class*=price], [id*=price], .price, .amount")
                if price_elems:
                    el = max(price_elems, key=lambda e: (e.size.get("height", 0) * e.size.get("width", 0)))
                    try:
                        png2 = el.screenshot_as_png
                        if png2 and len(png2) > 500:
                            screenshot = png2
                    except Exception:
                        pass
            except Exception:
                pass

        return html, screenshot, None
    except TimeoutException as e:
        return None, None, f"Timeout: {e}"
    except WebDriverException as e:
        return None, None, str(e)
    except Exception as e:
        return None, None, str(e)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

# --- Parsers ---
def parse_generic(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.4) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []
    # link tabanlı basit tarama (fallback)
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        # basit keyword kontrolü
        if keywords and not any(k in text.lower() for k in keywords):
            continue
        # çevresinde fiyat ara
        parent = a.parent
        price = None
        if parent:
            m = PRICE_RE.search(parent.get_text(" ", strip=True))
            if m:
                price = parse_price(m.group(1))
        if price is None:
            m2 = PRICE_RE.search(soup.get_text(" ", strip=True))
            if m2:
                price = parse_price(m2.group(1))
        if price is not None:
            results.append(Product(site=site, name=text, price=price, url=url_join(base_url, a["href"])))
    # dedupe
    seen = set()
    out = []
    for p in results:
        key = (p.name.strip().lower(), p.price)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

def parse_motorobit(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.4) -> List[Product]:
    """
    Motorobit için site-özel parser:
    - Ürün sayfası (h1/h2/og:title) -> aynı blokta fiyat
    - Arama/listing sayfası -> ürün kartlarını (.product-card vb.) kontrol et
    """
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []

    # ÜRÜN SAYFASI HEURİSTİĞİ
    title_tag = soup.find(["h1", "h2"]) or soup.find("meta", property="og:title")
    if title_tag:
        title = title_tag.get_text(" ", strip=True) if hasattr(title_tag, "get_text") else (title_tag.get("content") if title_tag else "")
        if title and (not keywords or any(k in title.lower() for k in keywords)):
            # title etrafında fiyat ara
            parent = title_tag.parent if hasattr(title_tag, "parent") else soup
            text_block = parent.get_text(" ", strip=True)
            # ilk önce 'KDV' etiketli fiyat arama
            m_kdv = re.search(r"([\d\.,\s]+)\s*(?:TL|₺|TRY).*kdv", text_block, re.IGNORECASE)
            if m_kdv:
                price = parse_price(m_kdv.group(1))
                if price is not None:
                    results.append(Product(site=site, name=title, price=price, url=base_url))
                    return results
            m = PRICE_RE.search(text_block)
            if m:
                price = parse_price(m.group(1))
                if price is not None:
                    results.append(Product(site=site, name=title, price=price, url=base_url))
                    return results

    # ARAMA / LİSTING SAYFASI - kart seçicileri
    card_selectors = [".product-card", ".product-item", ".product", ".search-result", ".product-grid-item", ".products .item"]
    price_selectors = [".price", ".product-price", ".prd-price", ".priceLabel", ".amount", ".priceText"]
    for sel in card_selectors:
        cards = soup.select(sel)
        if not cards:
            continue
        for card in cards:
            name_tag = card.select_one("h2, h3, .title, .product-title, a")
            if not name_tag:
                continue
            name = name_tag.get_text(" ", strip=True)
            if keywords and not any(k in name.lower() for k in keywords):
                continue
            price = None
            for ps in price_selectors:
                el = card.select_one(ps)
                if el:
                    m = PRICE_RE.search(el.get_text(" ", strip=True))
                    if m:
                        price = parse_price(m.group(1))
                        break
            if price is None:
                m2 = PRICE_RE.search(card.get_text(" ", strip=True))
                if m2:
                    price = parse_price(m2.group(1))
            link_tag = card.find("a", href=True)
            href = url_join(base_url, link_tag["href"]) if link_tag else base_url
            if price is not None:
                results.append(Product(site=site, name=name, price=price, url=href))
        if results:
            break

    # fallback generic
    if not results:
        results = parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)

    # dedupe
    seen = set()
    out = []
    for p in results:
        key = (p.name.strip().lower(), p.price)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

# Parsers registry
PARSERS: Dict[str, Any] = {
    "Motorobit": parse_motorobit,
    "Robotzade": parse_generic,  # istersen site-özel ekle
    "Robocombo": parse_generic,
    "Robotistan": parse_generic,
    "Samm Market": parse_generic,
}

# --- Direct URL fetch + parse helper ---
def fetch_and_parse_direct_url(url: str, site_name: str, relevance_threshold: float = 0.4, debug: bool = False, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[List[Product], Optional[str]]:
    """
    Doğrudan kullanıcı bir ürün URL'si verdiğinde bu fonksiyon kullanılır.
    """
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    # İlk önce requests ile dene
    html, err = fetch_page_requests(url, timeout=12)
    png_path = None
    if not html:
        # Selenium fallback
        wait_selector = SITE_WAIT_SELECTORS.get(site_name)
        html, png, serr = fetch_page_selenium(url, wait_selector=wait_selector, wait_for_content=(wait_selector is None), timeout=25, headless=not debug, chrome_binary=chrome_binary, driver_path=driver_path)
        if debug:
            hpath, ppath = save_debug(site_name, html, png)
            png_path = ppath
        if serr and not html:
            return [], f"Fetch Hatası: {serr}"
    try:
        parser = PARSERS.get(site_name, parse_generic)
        products = parser(html, base_url, site_name, query="", relevance_threshold=relevance_threshold)
        return products, None
    except Exception as e:
        return [], f"Ayrıştırma Hatası: {e}"

# --- Orchestration ---
def scrape_site(site: str, template: str, query: str, relevance_threshold: float = 0.4, debug: bool = False, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[str, List[Product], str, Optional[str]]:
    url = template.format(query=urllib.parse.quote_plus(query))
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = None
    screenshot_path = None
    err_msg = None

    # requests ilk tercih
    html, err = fetch_page_requests(url, timeout=10)
    if not html:
        # Selenium ile çek (JS gerekebilir)
        wait_selector = SITE_WAIT_SELECTORS.get(site)
        html, png, serr = fetch_page_selenium(url, wait_selector=wait_selector, wait_for_content=(wait_selector is None), timeout=25, headless=not debug, chrome_binary=chrome_binary, driver_path=driver_path)
        if debug:
            hpath, ppath = save_debug(site, html, png)
            screenshot_path = ppath
        if serr and not html:
            return site, [], f"Fetch Hatası: {serr}", screenshot_path

    # parse
    parser = PARSERS.get(site, parse_generic)
    try:
        products = parser(html, base_url, site, query, relevance_threshold=relevance_threshold)
        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status, screenshot_path
    except Exception as e:
        return site, [], f"Ayrıştırma Hatası: {e}", screenshot_path

def search_all(query: str, sites: List[str], max_workers: int = 3, relevance_threshold: float = 0.4, debug: bool = False, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None):
    results = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scrape_site, site, SEARCH_SITES[site], query, relevance_threshold, debug, chrome_binary, driver_path): site
            for site in sites if site in SEARCH_SITES
        }
        for fut in cf.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                # log istersen ekle
                pass
    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Ürün-Fiyat (Motorobit düzeltmeli)", layout="wide")
st.title("Ürün ve Fiyat (sadece ürün adı ve fiyat gösterilir)")

with st.sidebar:
    st.header("Ayarlar")
    sites = st.multiselect("Siteler", list(SEARCH_SITES.keys()), default=list(SEARCH_SITES.keys()))
    max_workers = st.slider("Eşzamanlı site tarama", 1, 6, 3)
    relevance = st.slider("Alaka eşiği (0 gevşek - 1 sıkı)", 0.0, 1.0, 0.4, 0.1)
    debug_mode = st.checkbox("Debug (Selenium GUI & debug kayıtları)", value=False)
    chrome_bin = st.text_input("Chrome binary (opsiyonel)", value="")
    driver_path = st.text_input("Chromedriver path (opsiyonel)", value="")

query = st.text_input("Aranacak ürün (örn: mp1584, HC06 veya ürün URL'si):")

if st.button("Ara"):
    if not query or not query.strip():
        st.warning("Lütfen bir arama terimi girin.")
    else:
        q = query.strip()
        # Eğer kullanıcı doğrudan ürün URL'si verdi ise site'yi tespit et ve doğrudan çek
        direct_site = None
        parsed_q = urllib.parse.urlparse(q)
        if parsed_q.scheme and parsed_q.netloc:
            domain = parsed_q.netloc.lower()
            for s, tmpl in SEARCH_SITES.items():
                tmpl_netloc = urllib.parse.urlparse(tmpl.format(query="x")).netloc
                if tmpl_netloc and tmpl_netloc in domain:
                    direct_site = s
                    break

        results = []
        if direct_site:
            st.info(f"Doğrudan URL tespiti: {direct_site}")
            prods, err = fetch_and_parse_direct_url(q, direct_site, relevance_threshold=relevance, debug=debug_mode, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None))
            status = f"{len(prods)} ürün bulundu" if prods else (err or "Ürün bulunamadı")
            if err:
                st.error(f"{direct_site}: {err}")
            else:
                st.success(f"{direct_site}: {status}")
            results.append((direct_site, prods, status, None))
        else:
            with st.spinner(f"{len(sites)} site aranıyor..."):
                results = search_all(q, sites, max_workers=max_workers, relevance_threshold=relevance, debug=debug_mode, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None))

        # Sonuçları topla ve göster (yalnızca Ürün - Fiyat)
        all_products: List[Product] = []
        for site, prods, status, png in results:
            if isinstance(status, str) and ("Hata" in status or "Engellendi" in status):
                st.error(f"{site}: {status}")
            elif isinstance(status, str) and "bulunamadı" in status:
                st.warning(f"{site}: {status}")
                # debug görsel
                if debug_mode and png:
                    st.image(png, caption=f"{site} - debug")
            else:
                # başarı durumu
                st.info(f"{site}: {status}")
            all_products.extend(prods)

        if not all_products:
            st.error("Hiç ürün bulunamadı. Debug modunu açıp HTML kaydını kontrol et veya alaka eşiğini düşür.")
        else:
            # Tekilleştir ve sırala (fiyat bilinmeyenleri sona)
            seen = set()
            rows = []
            for p in all_products:
                key = (p.name.strip().lower(), p.price)
                if key in seen:
                    continue
                seen.add(key)
                price_display = f"{p.price:,.2f} TL" if p.price is not None else "Bilinmiyor"
                rows.append({"Ürün": p.name, "Fiyat": price_display, "Site": p.site, "Link": p.url, "price_val": (p.price if p.price is not None else float("inf"))})
            df = pd.DataFrame(rows)
            df = df.sort_values("price_val").drop(columns=["price_val"])
            st.dataframe(df[["Ürün", "Fiyat", "Site", "Link"]], use_container_width=True)
            st.markdown("### Sadece Ürün - Fiyat")
            for _, r in df.iterrows():
                st.write(f"- {r['Ürün']} — {r['Fiyat']} — ({r['Site']})")

st.caption("Not: Selenium ile yapılan istekler bazı sitelerin kullanım şartlarını etkileyebilir. Debug açmak local'de GUI gerektirebilir.")
