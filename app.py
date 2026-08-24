# Tek dosya refactor: app.py
# Gereksinimler: streamlit, selenium, webdriver-manager, beautifulsoup4, lxml, pandas, requests
# pip install streamlit selenium webdriver-manager beautifulsoup4 lxml pandas requests

import concurrent.futures
import logging
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
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Konfigürasyon ----------
# Her site için template ve js gereksinimi (True ise Selenium kullanılır)
SEARCH_SITES: Dict[str, Dict[str, Any]] = {
    "Robotistan": {"template": "https://www.robotistan.com/arama?q={query}", "requires_js": False},
    "Motorobit": {"template": "https://www.motorobit.com/arama?q={query}", "requires_js": True},
    "Samm Market": {"template": "https://market.samm.com/search?s={query}", "requires_js": True},
    "Robolink": {"template": "https://www.robolinkmarket.com/?search_provider=aisearch&query={query}&page=1", "requires_js": True},
    "Robocombo": {"template": "https://www.robocombo.com/Arama?1&kelime={query}", "requires_js": False},
    "Kartal Otomasyon": {"template": "https://www.kartalotomasyon.com.tr/arama/{query}", "requires_js": False},
    "F1 Depo": {"template": "https://www.f1depo.com/arama/{query}", "requires_js": False},
    "Robotzade": {"template": "https://www.robotzade.com/arama/{query}", "requires_js": False},
    # örnek ek: "Amazon TR": {"template": "...", "requires_js": False}
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

# ---------- Veri model ----------
@dataclass
class Product:
    site: str
    name: str
    price: Optional[float]
    url: str

# ---------- Yardımcı fonksiyonlar ----------
_CLEAN_RE = re.compile(r"[^\d,.\-]")

def parse_price(raw: Optional[str]) -> Optional[float]:
    """
    Daha sağlam fiyat ayrıştırıcı:
    - Hem '.' hem ',' varsa Türkçe formatı ('.' bin, ',' ondalık) varsayar
    - Sadece ',' varsa ondalık ayırıcı olarak kabul eder
    - Sadece '.' varsa ondalık kabul edilir
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Temizle: para sembolleri vb.
    s = _CLEAN_RE.sub("", s)
    if not s:
        return None
    # Normalize spacing and thousands separators
    if "." in s and "," in s:
        # e.g. "1.234,56" -> "1234.56"
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        # e.g. "1234,56" -> "1234.56"
        s = s.replace(",", ".")
    else:
        # only dots or only digits: keep as-is
        s = s
    # remove spaces
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

# ---------- Parsers (site-özel/parsers registry) ----------
def parse_generic(html: str, base_url: str, site: str, query: str) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    # kaldır: script/style vb.
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()
    keywords = [k.lower() for k in query.split() if len(k) > 1]

    # Topla linkler (normalize edilmiş metin ile)
    link_queue: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        ntext = normalize_text(text)
        if ntext in IGNORE_LINK_TEXT or ntext.startswith("#") or "javascript:" in a["href"]:
            continue
        # basit kontrol: keyword'lerden en az biri link metninde varsa al
        if keywords and not any(k in ntext for k in keywords):
            # yine de link ekleyebiliriz, fiyatla ilişkilendirmek adına
            pass
        href = url_join(base_url, a["href"])
        link_queue.append((text.strip(), href))

    # Satır bazlı text
    raw_lines = [ln.strip() for ln in soup.get_text(separator="\n").split("\n") if ln.strip()]
    # Birleştir: eğer satır sadece sayılarsa ve yanındaki TL ibaresi ayrıysa onu birleştir
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
    # Basit eşleme: link metni geçtiyse sonraki N satır içinde fiyat aranır
    for (link_text, link_url) in link_queue:
        # normalize link_text
        nlink = normalize_text(link_text)
        # check keywords presence
        if keywords and not any(k in nlink for k in keywords):
            # yine de fiyatla eşleşirse almak isteyebiliriz; burada yine de devam ediyoruz
            pass
        # arama: link_text'in geçtiği satır index'i bul
        found_idx = None
        for idx, l in enumerate(lines):
            if link_text in l or normalize_text(l) == nlink:
                found_idx = idx
                break
        # arama yap: bulunduysa birkaç satır ileri geri tara fiyat için
        price: Optional[float] = None
        if found_idx is not None:
            search_window = lines[found_idx: found_idx + 6] + (lines[max(0, found_idx - 3):found_idx] if found_idx > 0 else [])
            for w in search_window:
                m = PRICE_RE.search(w)
                if m:
                    price = parse_price(m.group(1))
                    break
        # fallback: sayfadaki herhangi yerde fiyat aranırsa al
        if price is None:
            for w in lines[:60]:
                m = PRICE_RE.search(w)
                if m:
                    price = parse_price(m.group(1))
                    break
        if link_url not in seen_urls:
            results.append(Product(site=site, name=link_text, price=price, url=link_url))
            seen_urls.add(link_url)
    # Eğer link_queue boşsa, direkt sayfa içinde item-benzeri bloklar kontrol edilebilir (basit heuristic)
    return results

# Site-özel parser örneği (gerekirse genişlet)
def parse_robotistan(html: str, base_url: str, site: str, query: str) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    products: List[Product] = []
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    for card in soup.select(".product-list .product") or soup.select(".product-card"):
        name_tag = card.select_one(".product-title") or card.select_one("h3")
        price_tag = card.select_one(".price") or card.select_one(".prd-price")
        link_tag = card.find("a", href=True)
        if name_tag and link_tag:
            name = name_tag.get_text(strip=True)
            if keywords and not any(k in name.lower() for k in keywords):
                continue
            price = None
            if price_tag:
                price = parse_price(price_tag.get_text(strip=True))
            url = url_join(base_url, link_tag["href"])
            products.append(Product(site=site, name=name, price=price, url=url))
    if products:
        return products
    # fallback generic
    return parse_generic(html, base_url, site, query)

# Registry
PARSERS = {
    "Robotistan": parse_robotistan,
    # site-özel fonksiyonlar buraya eklenebilir
    # "Amazon TR": parse_amazon,
}

# ---------- HTML fetch katmanı ----------
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def fetch_page_requests(url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    """
    requests ile sayfayı çek. (HTML, error_message)
    """
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text, None
    except Exception as e:
        logger.warning("requests fetch hata: %s -> %s", url, e)
        return None, str(e)

def get_driver(chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, headless: bool = True):
    """
    Selenium Chrome driver oluştur (webdriver-manager fallback).
    driver_path: eğer sistemde chromedriver yeri belli ise verilebilir.
    chrome_binary: chrome/chrimium binary yolu verilebilir.
    """
    options = Options()
    if headless:
        # newer headless mode flag
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f'--user-agent={DEFAULT_HEADERS["User-Agent"]}')

    # binary candidates
    binary_candidates = [chrome_binary] if chrome_binary else ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
    for path in binary_candidates:
        if path and os.path.exists(path):
            options.binary_location = path
            break

    # try local driver first, then webdriver-manager
    try:
        if driver_path and os.path.exists(driver_path):
            service = Service(driver_path)
        else:
            # may throw if not present
            service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        logger.debug("Local chromedriver kullanıldı")
        return driver
    except Exception:
        logger.debug("Local chromedriver başarısız, webdriver-manager ile kurulum deneniyor")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        # stealth flag (dikkat: sitelerin policy'sine göre problem olabilir)
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

def fetch_page_selenium(url: str, wait_for_content: bool = False, timeout: int = 20, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None) -> Tuple[Optional[str], Optional[bytes], Optional[str]]:
    """
    Selenium ile sayfayı getirir ve HTML döndürür. Eğer debug istenirse ekran görüntüsünü png bytes olarak getirir.
    Returns: (html_text, screenshot_bytes_or_None, error_message_or_None)
    """
    driver = None
    try:
        driver = get_driver(chrome_binary=chrome_binary, driver_path=driver_path, headless=True)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        # küçük beklemeler ve cookie kapatma denemesi
        time.sleep(0.8)
        try:
            # basit cookie/banner kapatma
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
            # basit: sayfada TL ibaresi görünene kadar bekle
            end = time.time() + timeout
            while time.time() < end:
                try:
                    t = driver.execute_script("return document.body && document.body.innerText ? document.body.innerText : ''")
                    if re.search(r"\d[\d.,\s]*\s*(TL|₺)", str(t)):
                        break
                except Exception:
                    pass
                time.sleep(0.5)

        # scroll to bottom a bit
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
def scrape_site(site: str, template: str, query: str, requires_js: bool, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, debug: bool = False) -> Tuple[str, List[Product], str, Optional[bytes], Optional[str]]:
    """
    Tek bir site için arama yapar ve ürünleri döner.
    Returns: (site, products, status_msg, debug_png_or_None, debug_html_snippet_or_None)
    """
    url = build_search_url(template, query)
    base_url = urllib.parse.urlparse(url).scheme + "://" + urllib.parse.urlparse(url).netloc

    html = None
    debug_png = None
    error_msg = None

    if requires_js:
        # Selenium ile çek
        html, debug_png, error_msg = fetch_page_selenium(url, wait_for_content=True, timeout=20, chrome_binary=chrome_binary, driver_path=driver_path)
        if error_msg:
            status = f"Bağlantı Hatası / Engellendi ({error_msg})"
            return site, [], status, None, None
    else:
        # requests fallback
        html, error = fetch_page_requests(url, timeout=12)
        if error or not html:
            # son çare Selenium ile dene
            html, debug_png, error_msg = fetch_page_selenium(url, wait_for_content=False, timeout=18, chrome_binary=chrome_binary, driver_path=driver_path)
            if error_msg and not html:
                status = f"Bağlantı Hatası / Engellendi ({error_msg or error})"
                return site, [], status, None, None

    # parse
    parser = PARSERS.get(site, None)
    try:
        parser_func = parser if parser else parse_generic
        products = parser_func(html, base_url, site, query)
        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        debug_html_snippet = html[:3000] if debug and html else None
        return site, products, status, debug_png, debug_html_snippet
    except Exception as e:
        logger.exception("Parsing sırasında hata: %s", e)
        return site, [], f"Ayrıştırma Hatası ({e.__class__.__name__})", debug_png, (html[:3000] if html else None)

def search_all(query: str, max_workers: int = 3, chrome_binary: Optional[str] = None, driver_path: Optional[str] = None, debug: bool = False) -> List[Tuple[str, List[Product], str, Optional[bytes], Optional[str]]]:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scrape_site, site, cfg["template"], query, cfg.get("requires_js", False), chrome_binary, driver_path, debug): site
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
st.title("⚡ Komponent Fiyat Karşılaştırma (Refactor v1)")

with st.sidebar:
    st.header("Ayarlar")
    max_workers = st.slider("Eşzamanlı site tarama (max_workers)", 1, 4, 2)
    debug_mode = st.checkbox("Debug modu (hata ekran görüntüsü / HTML göster)", value=False)
    chrome_bin = st.text_input("Chrome/Chromium binary path (opsiyonel)", value="")
    driver_path = st.text_input("Chromedriver path (opsiyonel)", value="")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

def format_price_for_display(p: Optional[float]) -> str:
    if p is None:
        return "Fiyat Çekilemedi"
    # Türkiye stili için nokta bin ayracı, virgül ondalık: basit format
    try:
        # 1234.56 -> '1.234,56'
        s = f"{p:,.2f}"
        # python default uses comma as thousands sep; replace to dot thousands and comma decimal
        # depending on locale, but simplest transform:
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} TL"
    except Exception:
        return f"{p} TL"

if st.button("Fiyatları Getir", type="primary"):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"{len(SEARCH_SITES)} site aranıyor (birkaç saniye - birkaç dakika sürebilir)..."):
            site_results = search_all(query.strip(), max_workers=max_workers, chrome_binary=(chrome_bin or None), driver_path=(driver_path or None), debug=debug_mode)

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
            # Sırala: fiyatı olmayanlar en sona
            all_products.sort(key=lambda p: (p.price is None, p.price or 0))
            # DataFrame oluştur
            rows = []
            for r in all_products:
                rows.append({
                    "Site": r.site,
                    "Fiyat": format_price_for_display(r.price),
                    "Ürün Adı": r.name,
                    "Link": r.url,
                })
            df = pd.DataFrame(rows)
            # Gösterim: dataframe + ayrıca linkleri tıklanabilir olarak listele
            st.dataframe(df.drop(columns=["Link"]), use_container_width=True)
            with st.expander("Satın Alma Bağlantıları", expanded=False):
                for r in all_products:
                    st.markdown(f"- **{r.site}** — [{r.name}]({r.url}) — {format_price_for_display(r.price)}")

            # İstersen CSV indir
            csv = pd.DataFrame(rows).to_csv(index=False)
            st.download_button("Sonuçları CSV indir", csv, file_name="fiyatlar.csv", mime="text/csv")

# ---------- Son notlar ----------
st.caption("Not: Selenium ile yapılan istekler bazı sitelerin kullanım şartlarını ihlal edebilir veya IP bloklamaya sebep olabilir. Yasal/etik çerçeveyi unutmayın.")
