# app.py
# Gereksinimler: streamlit, requests, beautifulsoup4, lxml, pandas, selenium, webdriver-manager
# pip install streamlit requests beautifulsoup4 lxml pandas selenium webdriver-manager

import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# Selenium yalnızca fallback için (opsiyonel)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By

DEBUG_DIR = "debug_snapshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT}

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
        # webdriver-manager fallback
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        raise RuntimeError("Chrome driver başlatılamadı: " + str(e))

def fetch_selenium(url: str, wait_seconds: int = 1, headless: bool = True, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    driver = None
    try:
        driver = get_driver(headless=headless, chrome_binary=chrome_binary, driver_path=driver_path)
        driver.set_page_load_timeout(20)
        driver.get(url)
        time.sleep(wait_seconds)
        try:
            # basit cookie kapatma denemesi
            driver.execute_script("""
            try {
              document.querySelectorAll('button, a').forEach(el => {
                const t = (el.innerText||'').toLowerCase();
                if (t.includes('kabul') || t.includes('accept')) { el.click(); }
              });
            } catch(e) {}
            """)
        except Exception:
            pass
        html = driver.page_source
        png = None
        try:
            png = driver.get_screenshot_as_png()
        except Exception:
            png = None
        return html, png, None
    except TimeoutException as e:
        return None, None, f"timeout: {e}"
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

def save_debug(site: str, html: Optional[str], png: Optional[bytes]):
    from datetime import datetime
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if html:
        with open(os.path.join(DEBUG_DIR, f"{site}_{ts}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    if png:
        with open(os.path.join(DEBUG_DIR, f"{site}_{ts}.png"), "wb") as f:
            f.write(png)

# Core extraction: hedef = yalnızca ürün adı ve o ürünün fiyatı
def extract_products_strict(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.5) -> List[Product]:
    """
    Sıkı kurallar:
    1) Eğer sayfa ürün sayfasıysa: h1/h2/og:title/meta title al -> aynı blokta veya yakınında PRICE_RE arar.
    2) Eğer listing (çoklu ürün) ise: ürün kartlarını tek tek kontrol eder, başlık + fiyat aynı kartta olmalı.
    3) Başlık query ile anlamlı şekilde eşleşmeli (keyword overlap >= threshold).
    """
    soup = BeautifulSoup(html, "lxml")
    # Temizle
    for tag in soup(["nav", "footer", "header", "script", "style", "aside", "noscript"]):
        tag.decompose()

    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []

    # 1) Ürün sayfası heuristiği: h1/h2 varsa onlara yakın fiyat ara
    title_el = soup.find(["h1", "h2"])
    if title_el:
        title = title_el.get_text(" ", strip=True)
        if title:
            # aynı parent ve sibling'larda fiyat arama
            parent = title_el.parent
            search_text = parent.get_text(" ", strip=True) if parent else soup.get_text(" ", strip=True)
            # öncelikle 'KDV' içeren ifadeler
            m_kdv = re.search(r"([\d\.,\s]+)\s*(?:TL|₺|TRY).*kdv", search_text, re.IGNORECASE)
            if m_kdv:
                price = parse_price(m_kdv.group(1))
                if price is not None and _is_relevant(title, keywords, relevance_threshold):
                    results.append(Product(site=site, name=title, price=price, url=base_url))
                    return results
            # normal price search near title
            m = PRICE_RE.search(search_text)
            if m:
                price = parse_price(m.group(1))
                if price is not None and _is_relevant(title, keywords, relevance_threshold):
                    results.append(Product(site=site, name=title, price=price, url=base_url))
                    return results

    # 2) Kart bazlı arama: tipik "card" seçicilerini dene
    card_selectors = [".product", ".product-card", ".product-item", ".product-list li", ".prd", ".productBox", ".search-result", ".product-grid-item"]
    price_selectors = [".price", ".product-price", ".prd-price", ".price-new", ".prc", ".price-item", ".amount", ".priceLabel"]
    for sel in card_selectors:
        cards = soup.select(sel)
        if not cards:
            continue
        for card in cards:
            # başlık bul
            name_tag = card.select_one("h2") or card.select_one("h3") or card.select_one(".title") or card.select_one("a")
            if not name_tag:
                continue
            name = name_tag.get_text(" ", strip=True)
            if not _is_relevant(name, keywords, relevance_threshold):
                continue
            # fiyat aynı kartta
            price = None
            for ps in price_selectors:
                el = card.select_one(ps)
                if el:
                    m = PRICE_RE.search(el.get_text(" ", strip=True))
                    if m:
                        price = parse_price(m.group(1))
                        break
            # fallback: kart metni içinde ara
            if price is None:
                m2 = PRICE_RE.search(card.get_text(" ", strip=True))
                if m2:
                    price = parse_price(m2.group(1))
            if name and (price is not None):
                # link bul
                link_tag = card.find("a", href=True)
                url = url_join(base_url, link_tag["href"]) if link_tag else base_url
                results.append(Product(site=site, name=name, price=price, url=url))
        if results:
            # eğer bu selector ile sonuç bulduysak diğer selector'leri denemeye gerek yok
            break

    # 3) Eğer hala yoksa, sayfa genelinden başlık + en yakın fiyattan dene (çok sıkı filtre: başlık query ile uyuşmalı)
    if not results:
        # possible product titles from links but filter them
        candidates = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            if not text or len(text) < 3:
                continue
            if _looks_like_nav(text):
                continue
            if _is_relevant(text, keywords, relevance_threshold):
                # fiyatı link etrafından ara: parent veya next siblings
                parent = a.parent
                price = None
                if parent:
                    m = PRICE_RE.search(parent.get_text(" ", strip=True))
                    if m:
                        price = parse_price(m.group(1))
                # fallback global
                if price is None:
                    m2 = PRICE_RE.search(soup.get_text(" ", strip=True))
                    if m2:
                        price = parse_price(m2.group(1))
                if price is not None:
                    candidates.append((text, url_join(base_url, a["href"]), price))
        for nm, url, price in candidates:
            results.append(Product(site=site, name=nm, price=price, url=url))

    # dedupe by (name, price)
    seen = set()
    unique: List[Product] = []
    for p in results:
        key = (p.name.strip().lower(), p.price)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique

def _is_relevant(name: str, keywords: List[str], threshold: float) -> bool:
    if not keywords:
        return True
    lname = name.lower()
    matched = sum(1 for k in keywords if k in lname)
    return (matched / len(keywords)) >= threshold

def _looks_like_nav(text: str) -> bool:
    nav_words = {"anasayfa", "bildirim", "giriş", "kayıt", "iletişim", "yardım", "satış", "tükendi", "favori", "sepet"}
    t = text.strip().lower()
    return any(w in t for w in nav_words)

# Basit site listesi (sadece arama URL template'leri)
SITES: Dict[str, str] = {
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
}

def scrape_site_strict(site: str, url_template: str, query: str, relevance: float = 0.6, debug: bool = False, selenium_fallback: bool = True) -> Tuple[str, List[Product], Optional[str], Optional[str]]:
    url = url_template.format(query=urllib.parse.quote_plus(query))
    base = urllib.parse.urlparse(url).scheme + "://" + urllib.parse.urlparse(url).netloc
    html, err = fetch_requests(url, timeout=10)
    png_path = None
    if not html and selenium_fallback:
        try:
            html, png, serr = fetch_selenium(url, wait_seconds=1, headless=not debug)
            if debug:
                # kaydet
                if html or png:
                    from datetime import datetime
                    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                    if html:
                        hpath = os.path.join(DEBUG_DIR, f"{site}_{ts}.html")
                        with open(hpath, "w", encoding="utf-8") as f:
                            f.write(html)
                    if png:
                        ppath = os.path.join(DEBUG_DIR, f"{site}_{ts}.png")
                        with open(ppath, "wb") as f:
                            f.write(png)
                        png_path = ppath
        except Exception:
            html = None
    if not html:
        return site, [], f"fetch error: {err}", png_path
    prods = extract_products_strict(html, base, site, query, relevance_threshold=relevance)
    return site, prods, None, png_path

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Sade Ürün+Fiyat Arama", layout="wide")
st.title("Ürün ve Fiyat (sadece ürün adı ve fiyat gösterilir)")

with st.sidebar:
    st.header("Ayarlar")
    relevance = st.slider("Alaka eşiği (başlıkta kaç anahtar bulunmalı)", 0.0, 1.0, 0.6, 0.1)
    max_sites = st.multiselect("Siteler", list(SITES.keys()), default=list(SITES.keys()))
    debug = st.checkbox("Debug (HTML/PNG kaydı ve Selenium GUI)", value=False)
    chrome_bin = st.text_input("Chrome binary (opsiyonel)", value="")
    driver_path = st.text_input("Chromedriver path (opsiyonel)", value="")

query = st.text_input("Aranacak ürün (örn: mp1584):", placeholder="örn: mp1584")

if st.button("Ara"):
    if not query.strip():
        st.warning("Lütfen arama terimi girin.")
    else:
        all_products: List[Product] = []
        for site in max_sites:
            template = SITES.get(site)
            site_name, prods, err, png_path = scrape_site_strict(site, template, query.strip(), relevance=relevance, debug=debug, selenium_fallback=True)
            if err:
                st.info(f"{site_name}: {err}")
                continue
            for p in prods:
                all_products.append(p)
            # debug görsel linki
            if debug and png_path:
                st.write(f"{site_name} debug ekran görüntüsü: {png_path}")

        if not all_products:
            st.error("Hiç ürün bulunamadı. Relevance eşiğini düşürmeyi veya debug modunu açıp HTML'i kontrol etmeyi deneyin.")
        else:
            # Sadece Ürün Adı ve Fiyat göster
            # Tekilleştir ve küçük bir tablo
            rows = []
            seen = set()
            for p in all_products:
                key = (p.name.strip().lower(), p.price)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"Site": p.site, "Ürün": p.name, "Fiyat": f"{p.price:,.2f} TL" if p.price is not None else "Bilinmiyor", "Link": p.url})
            df = pd.DataFrame(rows)
            # Sırala fiyat göre (bilinmeyen fiyatları sona al)
            df["sort_key"] = df["Fiyat"].apply(lambda x: float(x.replace(".", "").replace(",", ".").split()[0]) if x != "Bilinmiyor" else float("inf"))
            df = df.sort_values("sort_key").drop(columns=["sort_key"])
            st.dataframe(df[["Ürün", "Fiyat", "Site", "Link"]], use_container_width=True)
            # ayrıca düz liste (sadece ürün - fiyat)
            st.markdown("### Sadece Ürün - Fiyat")
            for _, row in df.iterrows():
                st.write(f"- {row['Ürün']} — {row['Fiyat']} — ({row['Site']})")
