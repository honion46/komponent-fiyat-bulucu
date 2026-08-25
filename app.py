import concurrent.futures
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
    "Samm Market": "https://market.samm.com/search?s={query}",
    "Robolink": "https://www.robolinkmarket.com/?search_provider=aisearch&query={query}&page=1",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama/{query}",
    "F1 Depo": "https://www.f1depo.com/arama/{query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
}

# Bu siteler sonucu AJAX/JS ile geç dolduruyor, standart bekleme yetmiyor
SLOW_AJAX_SITES = {"Samm Market", "Robolink", "Motorobit"}

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


def parse_price(raw: str) -> float | None:
    raw = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_products(html: str, base_url: str, site_name: str, query: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]

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

    # Standart bileşen siteleri - metin akışı ayrıştırıcısı
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

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


def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
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
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'},
    )
    return driver


def wait_for_real_content(driver, timeout=15, min_matches=2):
    """Sayfada gerçek ürün fiyatları görünene kadar bekler.
    Tek bir eşleşme (kargo bedava banner'ı gibi) yanlış pozitif olabileceği için
    en az min_matches kadar fiyat deseni arar."""
    js_check = """
    const t = document.body.innerText || '';
    const matches = t.match(/\\d[\\d.,]*\\s*(TL|₺)/g) || [];
    return matches.length;
    """
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
    js = """
    const texts = arguments[0];
    const nodes = document.querySelectorAll('button, a, div[role="button"], span[role="button"]');
    for (const el of nodes) {
        const t = (el.innerText || '').trim().toLowerCase();
        if (t && texts.some(x => t.includes(x)) && t.length < 40) {
            el.click();
            return true;
        }
    }
    // Genel kapatma ikonlarını dene (aria-label ile)
    const closeEls = document.querySelectorAll(
        '[aria-label*="close" i], [aria-label*="kapat" i], .close, .modal-close, .popup-close'
    );
    for (const el of closeEls) {
        el.click();
        return true;
    }
    return false;
    """
    try:
        driver.execute_script(js, texts)
        # ESC tuşu da bazı modalleri kapatır
        from selenium.webdriver.common.keys import Keys
        driver.find_element("tag name", "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass


def scrape_site(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]

    driver = None
    try:
        driver = get_driver()
        driver.set_page_load_timeout(25)
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
        elif site in SLOW_AJAX_SITES:
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
            debug_html_snippet = html[:3000]

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
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(scrape_site, site, tmpl, query): site for site, tmpl in SEARCH_URL_TEMPLATES.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass
    return results


st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Stealth Selenium)")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"{len(SEARCH_URL_TEMPLATES)} site aranıyor (15-30sn sürebilir)..."):
            site_results = search_all_selenium(query)

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
