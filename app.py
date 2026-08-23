import concurrent.futures
import re
import urllib.parse
from dataclasses import dataclass
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from curl_cffi import requests

# Odaklandığımız Elektronik Komponent Siteleri (Pazaryerleri de dahil)
SEARCH_URL_TEMPLATES = {
    "Direnc.net": "https://www.direnc.net/arama?q={query}",
    "Özdisan": "https://www.ozdisan.com/Product/Search?searchtext={query}",
    "Motorobit": "https://www.motorobit.com/arama?kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
    "Robolink": "https://www.robolinkmarket.com/arama?q={query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama/{query}",
    "F1 Depo": "https://www.f1depo.com/arama/{query}",
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
    
    # Cloudflare veya Hata Sayfası Yakalandıysa Boş Döndür
    if "Attention Required! | Cloudflare" in html or "cf-browser-verification" in html:
        return []

    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "template", "meta", "svg", "path"]):
        tag.decompose()

    # Pazaryerleri için Özel CSS Seçicileri
    if site_name == "Hepsiburada":
        products = []
        for card in soup.select('li[id^="i"]'):
            name_tag = card.select_one('h3[data-test-id="product-card-name"]')
            price_tag = card.select_one('div[data-test-id="price-current-price"]')
            link_tag = card.find("a", href=True)
            if name_tag and price_tag and link_tag:
                name = clean_text(name_tag.get("title", "") or name_tag.get_text(strip=True))
                if keywords and not any(k in name.lower() for k in keywords): continue
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
                if keywords and not any(k in name.lower() for k in keywords): continue
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
                if keywords and not any(k in name.lower() for k in keywords): continue
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
                if keywords and not any(k in name.lower() for k in keywords): continue
                p_text = price_tag.get_text(strip=True) + ("," + price_fraction.get_text(strip=True) if price_fraction else "")
                price = parse_price(p_text)
                href = link_tag["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                products.append(Product(site_name, name, price, full_url))
        return products

    # ---------------------------------------------------------
    # STANDART KOMPONENT SİTELERİ İÇİN TEMİZ METİN AKIŞI
    # Direnc.net, Özdisan, Motorobit vb.
    # ---------------------------------------------------------
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

def scrape_site_cffi(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]

    try:
        # TLS Spoofing başlıyor: Sistem kendini Windows üzerindeki Chrome 110 gibi tanıtıyor.
        response = requests.get(
            url, 
            impersonate="chrome110", 
            timeout=15,
            headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.google.com/"
            }
        )
        
        # Eğer site bizi engellerse veya Cloudflare'de takılırsak yakalayalım
        if response.status_code in [403, 503]:
            return site, [], f"CF / Bot Koruması (HTTP {response.status_code})"
            
        html = response.text
        products = extract_products(html, base_url, site, query)

        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status
    except Exception as e:
        return site, [], "Zaman Aşımı / Bağlantı Hatası"

def search_all_cffi(query: str):
    results = []
    # Aynı anda 4 siteyi hızlıca tara
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(scrape_site_cffi, site, tmpl, query): site for site, tmpl in SEARCH_URL_TEMPLATES.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass
    return results

# --- Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Anti-Bot Bypass)")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, lm35")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"Ağ kimliği gizlenerek {len(SEARCH_URL_TEMPLATES)} site aranıyor (Çok daha hızlı)..."):
            site_results = search_all_cffi(query)

        with st.expander("🔍 Site Tarama Durumları", expanded=False):
            for site, prods, status in site_results:
                if "Koruması" in status or "Hatası" in status:
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
