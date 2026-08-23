import concurrent.futures
import re
import urllib.parse
from dataclasses import dataclass
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

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

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|TRY)", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,]+$")

IGNORE_LINK_TEXT = {
    "add to cart", "sepete ekle", "favorilere ekle", "add to favorites",
    "i̇ncele", "incele", "javascript:void(0);", "see all", "tümü", "detay",
    "giriş yap", "üye ol", "sipariş takibi", "iletişim", "kategoriler", "yardım",
    "hesabım", "sepetim", "günün fırsatları", "müşteri hizmetleri", "satış yap"
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

def extract_products(html: str, base_url: str, site_name: str, query: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    link_queue = []
    keywords = [k.lower() for k in query.split() if len(k) > 1]

    if site_name == "Hepsiburada":
        items = soup.select('li[id^="i"]')
        for item in items:
            name_tag = item.select_one('h3[data-test-id="product-card-name"]')
            price_tag = item.select_one('div[data-test-id="price-current-price"]')
            link_tag = item.find('a', href=True)
            
            if name_tag and link_tag:
                name = name_tag.get_text(strip=True)
                href = link_tag.get('href', "")
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                
                if keywords and not any(k in name.lower() for k in keywords):
                    continue

                price = None
                if price_tag:
                    p_text = price_tag.get_text(strip=True).replace("TL", "").strip()
                    price = parse_price(p_text)

                if name and full_url:
                    link_queue.append(Product(site=site_name, name=name, price=price, url=full_url))
        
        if link_queue:
            return link_queue

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
                results.append(Product(site=site_name, name=candidate_name, price=price, url=candidate_url))
                seen_urls.add(candidate_url)
            candidate_name = None
            candidate_url = None
            continue

        if candidate_name:
            gap_counter += 1
            if gap_counter > 15:
                candidate_name = None
                candidate_url = None

    return results

def get_driver():
    options = Options()
    options.add_argument('--headless=new') # Yeni ve daha gizli headless modu
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    # --- ANTI-BOT GİZLENME PARAMETRELERİ ---
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
    except:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
    # Tarayıcı içindeki 'Ben bir Selenium botuyum' bayrağını JavaScript ile kaldırıyoruz
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
    })
    
    return driver

def scrape_site(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
    
    driver = None
    try:
        driver = get_driver()
        driver.set_page_load_timeout(20) 
        driver.get(url)
        
        # Cloudflare veya dinamik JS kullanan siteler için bekleme süresini ayarlıyoruz
        if site in ["Direnc.net", "Samm Market", "Motorobit"]:
            time.sleep(5.0) # Cloudflare testinin arka planda bitmesi için daha uzun süre
        elif site in ["Hepsiburada", "Trendyol", "N11", "Amazon TR"]:
            time.sleep(3.0)
        else:
            time.sleep(1.5)
            
        html = driver.page_source
        
        products = extract_products(html, base_url, site, query)
        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status
    except Exception as e:
        return site, [], "Bağlantı Hatası / Engellendi"
    finally:
        if driver:
            driver.quit()

def search_all_selenium(query: str):
    results = []
    # Aynı anda en fazla 4 siteye gir
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(scrape_site, site, tmpl, query): site for site, tmpl in SEARCH_URL_TEMPLATES.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass
    return results

# --- Mobil Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Stealth Selenium)")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"Tarayıcılar gizli modda başlatıldı. {len(SEARCH_URL_TEMPLATES)} site aranıyor (Lütfen 15-20sn bekleyin)..."):
            site_results = search_all_selenium(query)
        
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
            
            data = []
            for r in all_products:
                fiyat = f"{r.price:,.2f} TL" if r.price is not None else "Fiyat Çekilemedi"
                data.append({
                    "Site": r.site,
                    "Fiyat": fiyat,
                    "Ürün Adı": r.name,
                    "Link": r.url
                })
            
            df = pd.DataFrame(data)
            st.dataframe(
                df,
                column_config={"Link": st.column_config.LinkColumn("Satın Al")},
                hide_index=True,
                use_container_width=True
            )
