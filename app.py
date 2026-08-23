import sys
import types

# --- PYTHON 3.12+ İÇİN DISTUTILS YAMASI ---
# undetected-chromedriver'ın eski distutils paketini arayıp çökmesini engeller.
if "distutils" not in sys.modules:
    distutils = types.ModuleType("distutils")
    sys.modules["distutils"] = distutils
    distutils_version = types.ModuleType("distutils.version")
    sys.modules["distutils.version"] = distutils_version
    
    class LooseVersion:
        def __init__(self, v):
            self.v = str(v)
        def __lt__(self, other): return self.v < str(other.v if hasattr(other, 'v') else other)
        def __le__(self, other): return self.v <= str(other.v if hasattr(other, 'v') else other)
        def __eq__(self, other): return self.v == str(other.v if hasattr(other, 'v') else other)
        def __ge__(self, other): return self.v >= str(other.v if hasattr(other, 'v') else other)
        def __gt__(self, other): return self.v > str(other.v if hasattr(other, 'v') else other)
        def __str__(self): return self.v
        
    distutils_version.LooseVersion = LooseVersion
# -------------------------------------------

import concurrent.futures
import os
import re
import time
import urllib.parse
from dataclasses import dataclass

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 14 Adet Popüler Site
SEARCH_URL_TEMPLATES = {
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Direnc.net": "https://www.direnc.net/arama?q={query}",
    "Motorobit": "https://www.motorobit.com/arama?kelime={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
    "Robolink": "https://www.robolinkmarket.com/arama?q={query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama/{query}",
    "F1 Depo": "https://www.f1depo.com/arama/{query}",
    "Özdisan": "https://www.ozdisan.com/Product/Search?searchtext={query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Trendyol": "https://www.trendyol.com/sr?q={query}",
    "Hepsiburada": "https://www.hepsiburada.com/ara?q={query}",
    "N11": "https://www.n11.com/arama?q={query}",
    "Amazon TR": "https://www.amazon.com.tr/s?k={query}",
}

SITE_WAIT_SELECTORS = {
    "Hepsiburada": 'li[id^="i"]',
    "Trendyol": ".p-card-wrppr",
    "N11": ".column",
    "Amazon TR": 'div[data-component-type="s-search-result"]',
}

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|TRY)", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,]+$")

IGNORE_LINK_TEXT = {
    "add to cart", "sepete ekle", "favorilere ekle", "add to favorites",
    "i̇ncele", "incele", "javascript:void(0);", "see all", "tümü", "detay",
    "giriş yap", "üye ol", "sipariş takibi", "iletişim", "kategoriler", "yardım",
    "hesabım", "sepetim", "günün fırsatları", "müşteri hizmetleri", "satış yap",
}

@dataclass
class Product:
    site: str
    name: str
    price: float | None
    url: str

def parse_price(raw: str) -> float | None:
    raw = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None

def clean_text(text: str) -> str:
    cleaned = re.sub(r'\s+', ' ', text).strip()
    if len(cleaned) > 130:
        cleaned = cleaned[:127] + "..."
    return cleaned

def extract_products(html: str, base_url: str, site_name: str, query: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "template", "meta", "svg", "path"]):
        tag.decompose()

    if site_name == "Hepsiburada":
        products = []
        for card in soup.select('li[id^="i"]'):
            name_tag = card.select_one('h3[data-test-id="product-card-name"]')
            price_tag = card.select_one('div[data-test-id="price-current-price"]')
            link_tag = card.find("a", href=True)
            if name_tag and price_tag and link_tag:
                name = clean_text(name_tag.get("title", "") or name_tag.get_text(strip=True))
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
                name = clean_text(name_tag.get("title", "") or name_tag.get_text(strip=True))
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
                name = clean_text(name_tag.get("title", "") or name_tag.get_text(strip=True))
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
                name = clean_text(name_tag.get_text(strip=True))
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

    link_queue = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        raw_text = a.get("title", "")
        if len(raw_text) < 3:
            raw_text = a.get_text(separator=" ", strip=True)
            
        text = clean_text(raw_text)
        
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
            lines.append(clean_text(cur))
            i += 1

    results = []
    link_idx = 0
    candidate_name = None
    candidate_url = None
    gap_counter = 0
    seen_urls = set()

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

    return results

def get_uc_driver():
    options = uc.ChromeOptions()
    options.headless = True 
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    driver = uc.Chrome(options=options, use_subprocess=True)
    return driver

def scrape_site_uc(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]

    driver = None
    try:
        driver = get_uc_driver()
        driver.set_page_load_timeout(30)
        driver.get(url)

        selector = SITE_WAIT_SELECTORS.get(site)
        if selector:
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            except Exception:
                pass
        else:
            time.sleep(4.0)

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(2.0)

        html = driver.page_source
        products = extract_products(html, base_url, site, query)

        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status
    except Exception as e:
        return site, [], f"Bağlantı Hatası / Engellendi ({e.__class__.__name__})"
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def search_all_uc(query: str):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(scrape_site_uc, site, tmpl, query): site for site, tmpl in SEARCH_URL_TEMPLATES.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass
    return results

st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Undetected Motoru)")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"{len(SEARCH_URL_TEMPLATES)} site aranıyor (Lütfen bekleyin)..."):
            site_results = search_all_uc(query)

        with st.expander("🔍 Site Tarama Durumları", expanded=False):
            for site, prods, status in site_results:
                if "Hata" in status or "Engellendi" in status:
                    st.error(f"**{site}:** {status}")
                elif "bulunamadı" in status:
                    st.warning(f"**{site}:** {status}")
                else:
                    st.success(f"**{site}:** {status}")

        all_products = [p for _, prods, _ in site_results for p in prods]

        if not all_products:
            st.error("Hiçbir sitede sonuç bulunamadı.")
        else:
            all_products.sort(key=lambda p: (p.price is None, p.price or 0))
            data = [
                {
                    "Site": r.site,
                    "Fiyat": f"{r.price:,.2f} TL" if r.price is not None else "Fiyat Çekilemedi",
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
