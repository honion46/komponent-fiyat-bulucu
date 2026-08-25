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
import json

# Selenium (fallback / JS-rendered sayfalar için)
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# --- Ayarlar ---
DEBUG_DIR = "debug_snapshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT}

SEARCH_SITES: Dict[str, str] = {
    "Motorobit": "https://www.motorobit.com/arama?q={query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
}

SITE_WAIT_SELECTORS: Dict[str, str] = {
    "Samm Market": ".product, .product-list, .product-item, .product-card",
    "Motorobit": ".product-card, .products, .product-list, .product",
}

PRICE_RE = re.compile(r"([\d]{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺|TRY)", re.IGNORECASE)
_CLEAN_RE = re.compile(r"[^\d,.\-]")

@dataclass
class Product:
    site: str
    name: str
    price: Optional[float]
    url: str

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

def fetch_requests(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text, None
    except Exception as e:
        return None, str(e)

def get_driver(headless: bool = True, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f'--user-agent={USER_AGENT}')
    if chrome_binary and os.path.exists(chrome_binary):
        options.binary_location = chrome_binary
    try:
        if driver_path and os.path.exists(driver_path):
            service = Service(driver_path)
            return webdriver.Chrome(service=service, options=options)
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        raise RuntimeError("Chromedriver başlatılamadı: " + str(e))

def fetch_selenium(url: str, wait_selector: Optional[str] = None, wait_for_content: bool = False, timeout: int = 20, headless: bool = True, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    driver = None
    try:
        driver = get_driver(headless=headless, chrome_binary=chrome_binary, driver_path=driver_path)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        time.sleep(0.8)
        if wait_selector:
            try:
                WebDriverWait(driver, min(25, timeout)).until(EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector)))
            except Exception:
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
        time.sleep(0.6)
        html = driver.page_source
        screenshot = None
        try:
            screenshot = driver.get_screenshot_as_png()
        except Exception:
            screenshot = None
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

# Samm product page parser: JSON-LD / meta / büyük fiyat heuristiği
def parse_samm_productpage(html: str, base_url: str) -> Optional[Product]:
    soup = BeautifulSoup(html, "lxml")
    # 1) JSON-LD içinde Offer varsa dene
    try:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "{}")
                # JSON-LD bazen liste olabiliyor
                if isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and d.get("@type", "").lower() in ("product", "offer"):
                            offer = d.get("offers") or d
                            if isinstance(offer, dict):
                                price = offer.get("price") or offer.get("priceAmount")
                                if price:
                                    p = parse_price(str(price))
                                    title = d.get("name") or soup.find("h1") and soup.find("h1").get_text(" ", strip=True)
                                    return Product(site="Samm Market", name=(title or "").strip(), price=p, url=base_url)
                elif isinstance(data, dict):
                    if data.get("@type", "").lower() == "product":
                        offers = data.get("offers")
                        if isinstance(offers, dict):
                            price = offers.get("price") or offers.get("priceAmount")
                            if price:
                                p = parse_price(str(price))
                                title = data.get("name") or soup.find("h1") and soup.find("h1").get_text(" ", strip=True)
                                return Product(site="Samm Market", name=(title or "").strip(), price=p, url=base_url)
            except Exception:
                continue
    except Exception:
        pass

    # 2) meta tags (og:price:amount vb)
    try:
        meta_price = None
        for name in ("product:price:amount", "og:price:amount", "price"):
            m = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if m and m.get("content"):
                meta_price = m["content"]
                break
        if meta_price:
            p = parse_price(meta_price)
            title = (soup.find("h1") or soup.find("h2"))
            title_text = title.get_text(" ", strip=True) if title else ""
            return Product(site="Samm Market", name=title_text, price=p, url=base_url)
    except Exception:
        pass

    # 3) sayfadaki tüm TL eşleşmelerini topla; prefer '+ KDV' yanındaki veya son görünen fiyat (genellikle büyük final fiyat)
    text = soup.get_text(" ", strip=True)
    matches = list(PRICE_RE.finditer(text))
    if matches:
        # tercih sırası:
        # - match içeren "KDV" ifadesi varsa onu kullan
        for m in matches:
            span_start = m.start()
            window = text[max(0, span_start-60): m.end()+60].lower()
            if "kdv" in window:
                val = parse_price(m.group(1))
                title = (soup.find("h1") or soup.find("h2"))
                title_text = title.get_text(" ", strip=True) if title else ""
                return Product(site="Samm Market", name=title_text, price=val, url=base_url)
        # - yoksa son bulunan fiyatı al (sayfada küçük fiyat + büyük final fiyat varsa genelde sonuncu büyük olandır)
        last = matches[-1]
        val = parse_price(last.group(1))
        title = (soup.find("h1") or soup.find("h2"))
        title_text = title.get_text(" ", strip=True) if title else ""
        return Product(site="Samm Market", name=title_text, price=val, url=base_url)

    return None

def parse_samm_listing(html: str, base_url: str, query: str, relevance_threshold: float = 0.3) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    results: List[Product] = []
    card_selectors = [".product", ".product-item", ".product-card", ".product-list li", ".search-result", ".product-grid-item"]
    price_selectors = [".price", ".product-price", ".prd-price", ".priceLabel", ".amount", ".priceText"]
    keywords = [k.lower() for k in re.split(r"\s+", query) if k and len(k)>0]

    for sel in card_selectors:
        cards = soup.select(sel)
        if not cards:
            continue
        for card in cards:
            name_tag = card.select_one("h2, h3, .title, .product-title, a")
            if not name_tag:
                continue
            name = name_tag.get_text(" ", strip=True)
            # link
            a = card.find("a", href=True)
            href = url_join(base_url, a["href"]) if a else base_url
            # fiyat
            price = None
            for ps in price_selectors:
                el = card.select_one(ps)
                if el:
                    mm = PRICE_RE.search(el.get_text(" ", strip=True))
                    if mm:
                        price = parse_price(mm.group(1))
                        break
            if price is None:
                mm2 = PRICE_RE.search(card.get_text(" ", strip=True))
                if mm2:
                    price = parse_price(mm2.group(1))
            # basit alaka kontrolü (query kısa kodsa normalize ederek eşle)
            def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())
            if keywords:
                score = sum(1 for k in keywords if k in norm(name))
                if score == 0:
                    continue
            results.append(Product(site="Samm Market", name=name, price=price, url=href))
        if results:
            break
    return results

PARSERS: Dict[str, Any] = {
    "Samm Market": lambda html, base, site, q, relevance_threshold=0.3: (parse_samm_productpage(html, base) and [parse_samm_productpage(html, base)]) or parse_samm_listing(html, base, q, relevance_threshold),
}

def scrape_site(site: str, template: str, query: str, relevance_threshold: float = 0.3, debug: bool = False, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[str, List[Product], str, Optional[str]]:
    url = template.format(query=urllib.parse.quote_plus(query))
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = None
    screenshot_path = None

    html, err = fetch_requests(url, timeout=10)
    if not html:
        wait_selector = SITE_WAIT_SELECTORS.get(site)
        html, png, serr = fetch_selenium(url, wait_selector=wait_selector, wait_for_content=(wait_selector is None), timeout=20, headless=not debug, chrome_binary=chrome_binary, driver_path=driver_path)
        if debug:
            hpath, ppath = save_debug(site, html, png)
            screenshot_path = ppath
        if serr and not html:
            return site, [], f"Fetch Hatası: {serr}", screenshot_path

    parser = PARSERS.get(site)
    try:
        prods = []
        if parser:
            parsed_res = parser(html, base_url, site, query, relevance_threshold=relevance_threshold)
            if isinstance(parsed_res, list):
                prods = parsed_res
            elif isinstance(parsed_res, Product):
                prods = [parsed_res]
            elif parsed_res:
                prods = parsed_res
        status = f"{len(prods)} ürün bulundu" if prods else "Ürün bulunamadı"
        return site, prods, status, screenshot_path
    except Exception as e:
        return site, [], f"Ayrıştırma Hatası: {e}", screenshot_path

def search_all(query: str, sites: List[str], max_workers: int = 3, relevance_threshold: float = 0.3, debug: bool = False, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None):
    results = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scrape_site, site, SEARCH_SITES[site], query, relevance_threshold, debug, chrome_binary, driver_path): site for site in sites if site in SEARCH_SITES}
        for fut in cf.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                pass
    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Ürün-Fiyat (Samm uyumlu)", layout="wide")
st.title("Ürün ve Fiyat — sadece ürün adı ve fiyat gösterilir")

with st.sidebar:
    st.header("Ayarlar")
    sites = st.multiselect("Siteler", list(SEARCH_SITES.keys()), default=list(SEARCH_SITES.keys()))
    max_workers = st.slider("Eşzamanlı site tarama", 1, 4, 2)
    relevance = st.slider("Alaka eşiği", 0.0, 1.0, 0.3, 0.1)
    debug_mode = st.checkbox("Debug (Selenium kaydı)", value=False)
    chrome_bin = st.text_input("Chrome binary (opsiyonel)", value="")
    driver_path = st.text_input("Chromedriver path (opsiyonel)", value="")

query = st.text_input("Aranacak ürün (örn: HC05 veya ürün URL'si):")

if st.button("Ara"):
    if not query or not query.strip():
        st.warning("Lütfen arama terimi girin.")
    else:
        q = query.strip()
        direct_site = None
        parsed_q = urllib.parse.urlparse(q)
        if parsed_q.scheme and parsed_q.netloc:
            domain = parsed_q.netloc.lower()
            for s, tmpl in SEARCH_SITES.items():
                if urllib.parse.urlparse(tmpl.format(query='x')).netloc in domain:
                    direct_site = s
                    break
        results = []
        if direct_site:
            st.info(f"Doğrudan URL tespiti: {direct_site}")
            # doğrudan ürün sayfası çek
            html, err = fetch_requests(q, timeout=10)
            png = None
            if not html:
                html, png, serr = fetch_selenium(q, wait_selector=SITE_WAIT_SELECTORS.get(direct_site), wait_for_content=True, timeout=20, headless=not debug_mode, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None))
                if debug_mode:
                    save_debug(direct_site, html, png)
                if serr and not html:
                    st.error(f"{direct_site}: Fetch Hatası: {serr}")
            if html:
                prod = parse_samm_productpage(html, q)
                if prod:
                    st.success(f"{prod.name} — {prod.price:,.2f} TL")
                    results.append((direct_site, [prod], f"{1} ürün bulundu", None))
                else:
                    st.warning(f"{direct_site}: Ürün bulunamadı (detay sayfa parser eşleşmedi).")
                    results.append((direct_site, [], "Ürün bulunamadı", None))
        else:
            with st.spinner(f"{len(sites)} site aranıyor..."):
                results = search_all(q, sites, max_workers=max_workers, relevance_threshold=relevance, debug=debug_mode, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None))

        all_products: List[Product] = []
        for site, prods, status, png in results:
            if "Hata" in status:
                st.error(f"{site}: {status}")
            elif "bulunamadı" in status:
                st.warning(f"{site}: {status}")
            else:
                st.info(f"{site}: {status}")
            all_products.extend(prods)

        if not all_products:
            st.error("Hiç ürün bulunamadı. Debug modunu açıp debug_snapshots içindeki HTML'i paylaş.")
        else:
            seen = set()
            rows = []
            for p in all_products:
                key = (p.name.strip().lower(), p.price)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"Ürün": p.name, "Fiyat": f"{p.price:,.2f} TL" if p.price is not None else "Bilinmiyor", "Site": p.site, "Link": p.url})
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True)
            st.markdown("### Sadece Ürün - Fiyat")
            for _, r in df.iterrows():
                st.write(f"- {r['Ürün']} — {r['Fiyat']} — ({r['Site']})")

st.caption("Not: Eğer telefon üzerinden kullanıyorsan ve sorun devam ederse ekran görüntüsü at; ben manuel çıkarırım.")
