import asyncio
import re
import urllib.parse
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

# Robocombo URL'si güncellendi
SEARCH_URL_TEMPLATES = {
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Direnc.net": "https://www.direnc.net/arama?q={query}",
    "Motorobit": "https://www.motorobit.com/arama?kelime={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
    "Robolink": "https://www.robolinkmarket.com/arama?q={query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama?q={query}",
    "F1 Depo": "https://www.f1depo.com/arama?q={query}",
    "Özdisan": "https://www.ozdisan.com/Product/Search?searchtext={query}",
    "Robotzade": "https://www.robotzade.com/arama?q={query}",
    "Trendyol": "https://www.trendyol.com/sr?q={query}",
    "Hepsiburada": "https://www.hepsiburada.com/ara?q={query}",
    "N11": "https://www.n11.com/arama?q={query}",
    "Amazon TR": "https://www.amazon.com.tr/s?k={query}",
}

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,]+$")

# Link tarayıcısının yoksayacağı gereksiz kelimeler
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
    
    # Gereksiz kısımları temizle ki fiyat akışı bozulmasın
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    link_queue = []
    keywords = [k.lower() for k in query.split() if len(k) > 1]

    # Linkleri topla
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if not text or not href or text.lower() in IGNORE_LINK_TEXT or href.startswith("#") or "javascript:" in href:
            continue
        
        # Pazar yerlerinde alakasız önerileri filtrelemek için kelime kontrolü
        if keywords and not any(k in text.lower() for k in keywords):
            continue

        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        link_queue.append((text, full_url))

    # Metin akışından Link -> Fiyat eşleştirmesi
    raw_lines = [ln.strip() for ln in soup.get_text(separator="\n").split("\n") if ln.strip()]
    lines = []
    i = 0
    while i < len(raw_lines):
        cur = raw_lines[i]
        if i + 1 < len(raw_lines) and NUM_ONLY_RE.match(cur) and raw_lines[i + 1].strip().upper() in ("TL", "₺"):
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
            if gap_counter > 10:
                candidate_name = None
                candidate_url = None

    return results

async def search_site(client: httpx.AsyncClient, site: str, url_template: str, query: str) -> tuple[str, list[Product], str]:
    encoded_query = urllib.parse.quote_plus(query)
    url = url_template.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
    
    # Direnc.net için ücretsiz köprü kullanımı
    if site == "Direnc.net":
        fetch_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(url)}"
    else:
        fetch_url = url

    try:
        resp = await client.get(fetch_url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return site, [], f"Sunucu Engeli (HTTP {resp.status_code})"
    except Exception as e:
        return site, [], "Bağlantı Zaman Aşımı"
        
    products = extract_products(resp.text, base_url, site, query)
    status_msg = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı (Stokta yok)"
    return site, products, status_msg

async def search_all(query: str):
    async with httpx.AsyncClient() as client:
        tasks = [search_site(client, site, tmpl, query) for site, tmpl in SEARCH_URL_TEMPLATES.items()]
        return await asyncio.gather(*tasks)

# --- Mobil Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584, lehim teli")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"Türkiye'deki {len(SEARCH_URL_TEMPLATES)} site taranıyor. Lütfen bekleyin..."):
            site_results = asyncio.run(search_all(query))
        
        # Site durumlarını açılır kapanır bir panelde göster
        with st.expander("🔍 Site Tarama Durumları", expanded=False):
            for site, prods, status in site_results:
                if "Engeli" in status or "Zaman Aşımı" in status:
                    st.error(f"**{site}:** {status}")
                elif "bulunamadı" in status:
                    st.warning(f"**{site}:** {status}")
                else:
                    st.success(f"**{site}:** {status}")

        all_products = [p for _, prods, _ in site_results for p in prods]
        
        if not all_products:
            st.error("Hiçbir sitede sonuç bulunamadı veya sunucular engelledi. Lütfen kelimeyi değiştirip tekrar deneyin.")
        else:
            # Fiyatı olanları önce, olmayanları sona at. Ucuzdan pahalıya sırala.
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
                column_config={"Link": st.column_config.LinkColumn("Satın Al / İncele")},
                hide_index=True,
                use_container_width=True
            )
