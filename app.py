# app.py
# Gereksinimler:
# pip install streamlit requests beautifulsoup4 lxml pandas selenium webdriver-manager

import os
import re
import time
import json
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

# --- Parsers ---

def parse_generic(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.4) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        if keywords and not any(k in text.lower() for k in keywords):
            continue
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
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []
    title_tag = soup.find(["h1", "h2"]) or soup.find("meta", property="og:title")
    if title_tag:
        title = title_tag.get_text(" ", strip=True) if hasattr(title_tag, "get_text") else (title_tag.get("content") if title_tag else "")
        if title and (not keywords or any(k in title.lower() for k in keywords)):
            parent = title_tag.parent if hasattr(title_tag, "parent") else soup
            text_block = parent.get_text(" ", strip=True)
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
    if not results:
        results = parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)
    seen = set()
    out = []
    for p in results:
        key = (p.name.strip().lower(), p.price)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

# --- Samm specific parsing (improved) ---

def parse_samm_productpage(html: str, base_url: str) -> Optional[Product]:
    """
    Robust Samm product page parser:
    - Try JSON-LD / meta first
    - Then find TL matches near the product title (ignore USD)
    - Prefer matches mentioning 'kdv' or close to title; fallback to last TL match
    """
    soup = BeautifulSoup(html, "lxml")
    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og.get("content").strip()
    if not title:
        h = soup.find(["h1", "h2"])
        if h:
            title = h.get_text(" ", strip=True)

    # 1) JSON-LD offers
    try:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    t = data.get("@type", "")
                    if isinstance(t, str) and t.lower() == "product":
                        offers = data.get("offers", {})
                        if isinstance(offers, dict):
                            price = offers.get("price") or offers.get("priceAmount")
                            if price:
                                p = parse_price(str(price))
                                title_text = title or (data.get("name") or "")
                                return Product(site="Samm Market", name=title_text.strip(), price=p, url=base_url)
                elif isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and isinstance(d.get("@type", ""), str) and d.get("@type", "").lower() == "product":
                            offers = d.get("offers", {})
                            if isinstance(offers, dict):
                                price = offers.get("price") or offers.get("priceAmount")
                                if price:
                                    p = parse_price(str(price))
                                    title_text = title or (d.get("name") or "")
                                    return Product(site="Samm Market", name=title_text.strip(), price=p, url=base_url)
            except Exception:
                continue
    except Exception:
        pass

    # 2) meta tags
    try:
        meta_price = None
        for name in ("product:price:amount", "og:price:amount", "price"):
            m = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if m and m.get("content"):
                meta_price = m["content"]
                break
        if meta_price:
            p = parse_price(meta_price)
            title_text = (soup.find("h1") or soup.find("h2"))
            title_text = title_text.get_text(" ", strip=True) if title_text else (title or "")
            return Product(site="Samm Market", name=title_text, price=p, url=base_url)
    except Exception:
        pass

    # 3) text-based TL matching with title proximity and KDV preference
    text = soup.get_text(" ", strip=True)
    matches = list(PRICE_RE.finditer(text))
    # filter TL/₺/KDV-containing matches in surrounding snippet
    tl_matches = []
    for m in matches:
        snippet = text[max(0, m.start()-60): m.end()+60]
        if re.search(r"(tl|₺|kdv|kdv dahil)", snippet, re.IGNORECASE):
            tl_matches.append(m)
    # if title exists and TL matches, pick closest to title
    if title and tl_matches:
        title_pos = text.lower().find(title.lower())
        if title_pos >= 0:
            best = min(tl_matches, key=lambda mm: abs(mm.start() - title_pos))
            val = parse_price(best.group(1))
            if val is not None:
                return Product(site="Samm Market", name=title.strip(), price=val, url=base_url)
    # prefer KDV-window match
    if tl_matches:
        for m in tl_matches:
            window = text[max(0, m.start()-80): m.end()+80].lower()
            if "kdv" in window:
                v = parse_price(m.group(1))
                if v is not None:
                    return Product(site="Samm Market", name=(title or "").strip(), price=v, url=base_url)
        # fallback to last TL match
        last = tl_matches[-1]
        v = parse_price(last.group(1))
        if v is not None:
            return Product(site="Samm Market", name=(title or "").strip(), price=v, url=base_url)

    # fallback: non-USD last match
    if matches:
        non_usd = []
        for m in matches:
            snippet = text[max(0, m.start()-20): m.end()+20]
            if "$" in snippet:
                continue
            non_usd.append(m)
        if non_usd:
            last = non_usd[-1]
            v = parse_price(last.group(1))
            if v is not None:
                return Product(site="Samm Market", name=(title or "").strip(), price=v, url=base_url)

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
            a = card.find("a", href=True)
            href = url_join(base_url, a["href"]) if a else base_url
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
    "Motorobit": parse_motorobit,
    "Robotzade": parse_generic,
    "Robocombo": parse_generic,
    "Robotistan": parse_generic,
}

# --- Orchestration ---
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
