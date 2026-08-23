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

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)", re.IGNORECASE)
NUM_ONLY_RE = re.compile(r"^[\d.,]+$")
IGNORE_LINK_TEXT = {
    "add to cart", "sepete ekle", "favorilere ekle", "add to favorites",
    "i̇ncele", "incele", "javascript:void(0);", "see all", "tümü", "detay",
    "giriş yap", "üye ol", "sipariş takibi", "iletişim", "kategoriler",
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

# --- HTML Metin Akışı Ayrıştırıcı (Robotistan, Motorobit, Samm Market) ---
def extract_products(html: str, base_url: str, query: str, is_samm: bool = False) -> list[tuple[str, float | None, str]]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    link_queue = []
    keywords = [k.lower() for k in query.split() if len(k) > 1]

    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if not text or not href or text.lower() in IGNORE_LINK_TEXT or href.startswith("#") or "javascript:" in href:
            continue
        
        # Samm Market alakasız ürün döndüğünde başlık kontrolü yap
        if is_samm and keywords:
            if not any(k in text.lower() for k in keywords):
                continue

        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        link_queue.append((text, full_url))

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

    for line in lines:
        if link_idx < len(link_queue) and line == link_queue[link_idx][0]:
            candidate_name, candidate_url = link_queue[link_idx]
            link_idx += 1
            gap_counter = 0
            continue

        price_match = PRICE_RE.search(line)
        if price_match and candidate_name:
            price = parse_price(price_match.group(1))
            results.append((candidate_name, price, candidate_url))
            candidate_name = None
            candidate_url = None
            continue

        if candidate_name:
            gap_counter += 1
            if gap_counter > 8:
                candidate_name = None
                candidate_url = None

    return results

# --- Direnc.net Arama Metodu (T-Soft Servis & Yedek Proxy) ---
async def search_direnc(client: httpx.AsyncClient, query: str) -> list[Product]:
    encoded_query = urllib.parse.quote_plus(query)
    
    # 1. Yöntem: Direnc.net Arama Servisi
    api_url = f"https://www.direnc.net/arama?q={encoded_query}"
    proxy_url = f"https://api.allorigins.win/get?url={urllib.parse.quote(api_url)}"
    
    try:
        resp = await client.get(proxy_url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            html_content = data.get("contents", "")
            if html_content:
                raw_results = extract_products(html_content, "https://www.direnc.net", query)
                if raw_results:
                    return [Product(site="Direnc.net", name=n, price=p, url=u) for n, p, u in raw_results]
    except Exception:
        pass

    # 2. Yöntem: Standart Doğrudan İstek
    try:
        resp = await client.get(api_url, headers=HEADERS, timeout=8, follow_redirects=True)
        if resp.status_code == 200:
            raw_results = extract_products(resp.text, "https://www.direnc.net", query)
            return [Product(site="Direnc.net", name=n, price=p, url=u) for n, p, u in raw_results]
    except Exception:
        pass

    return []

# --- Genel Siteleri Tarama ---
async def search_standard_site(client: httpx.AsyncClient, site: str, url_tmpl: str, query: str) -> list[Product]:
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
    
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12, follow_redirects=True)
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    is_samm = (site == "Samm Market")
    raw_results = extract_products(resp.text, base_url, query, is_samm=is_samm)
    return [Product(site=site, name=n, price=p, url=u) for n, p, u in raw_results]

async def search_all(query: str) -> list[Product]:
    async with httpx.AsyncClient() as client:
        tasks = [
            search_standard_site(client, "Robotistan", "https://www.robotistan.com/arama?q={query}", query),
            search_standard_site(client, "Motorobit", "https://www.motorobit.com/arama?kelime={query}", query),
            search_standard_site(client, "Samm Market", "https://market.samm.com/search?s={query}", query),
            search_direnc(client, query)
        ]
        results = await asyncio.gather(*tasks)
        
    all_results = [p for site_prods in results for p in site_prods]
    
    # Tekrarlanan URL'leri ayıkla ve fiyata göre sırala
    unique_items = {}
    for p in all_results:
        if p.url not in unique_items:
            unique_items[p.url] = p
            
    sorted_list = list(unique_items.values())
    sorted_list.sort(key=lambda p: (p.price is None, p.price or 0))
    return sorted_list

# --- Streamlit Arayüzü ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584, lm35")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner("Tüm siteler taranıyor..."):
            results = asyncio.run(search_all(query))
        
        if not results:
            st.error("Aradığınız kriterde ürün bulunamadı.")
        else:
            data = []
            for r in results:
                fiyat = f"{r.price:,.2f} TL" if r.price is not None else "Belirtilmemiş"
                data.append({
                    "Site": r.site,
                    "Fiyat": fiyat,
                    "Ürün Adı": r.name,
                    "Link": r.url
                })
            
            df = pd.DataFrame(data)
            st.dataframe(
                df,
                column_config={"Link": st.column_config.LinkColumn("Ürün Linki")},
                hide_index=True,
                use_container_width=True
            )
