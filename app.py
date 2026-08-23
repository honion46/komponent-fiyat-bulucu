import asyncio
import re
import urllib.parse
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Gerçek ve test edilmiş arama kalıpları
SEARCH_URL_TEMPLATES = {
    "Motorobit": "https://www.motorobit.com/arama?kelime={query}",
    "Direnc.net": "https://www.direnc.net/arama?kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?kelime={query}",
    "Samm Market": "https://market.samm.com/tr/search?q={query}",
}

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)", re.IGNORECASE)

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

def extract_products(html: str, base_url: str, site_name: str) -> list[tuple[str, float | None, str]]:
    soup = BeautifulSoup(html, "lxml")
    results = []

    # 1. YÖNTEM: T-Soft ve standart e-ticaret ürün kartı seçicileri
    cards = soup.select(
        ".productItem, .showcase, .product-card, .product-item, .item-grid, .productBox, [data-product-id]"
    )
    
    for card in cards:
        name_tag = card.select_one("a.product-name, .productName, .product-title, .name a, a[title]")
        price_tag = card.select_one(".product-price, .productPrice, .price, .current-price, .discountPrice")
        
        name = name_tag.get_text(strip=True) if name_tag else ""
        href = name_tag.get("href", "") if name_tag else ""
        
        if not name or len(name) < 3:
            continue
            
        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        
        price = None
        if price_tag:
            m = PRICE_RE.search(price_tag.get_text(separator=" "))
            if m:
                price = parse_price(m.group(1))
                
        results.append((name, price, full_url))

    # 2. YÖNTEM: Eğer kart yapısı yakalanamadıysa genel metin taraması
    if not results:
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            if len(text) < 4 or href.startswith("#") or "javascript:" in href:
                continue
            
            parent = a.find_parent(["div", "li", "td"])
            if parent:
                parent_text = parent.get_text(separator=" ")
                m = PRICE_RE.search(parent_text)
                if m:
                    full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                    results.append((text, parse_price(m.group(1)), full_url))

    return results

async def search_site(client: httpx.AsyncClient, site: str, url_template: str, query: str) -> list[Product]:
    encoded_query = urllib.parse.quote_plus(query)
    url = url_template.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
    
    headers = HEADERS.copy()
    headers["Referer"] = base_url
    
    try:
        resp = await client.get(url, headers=headers, timeout=12, follow_redirects=True)
        if resp.status_code != 200:
            return []
    except Exception:
        return []
        
    raw_results = extract_products(resp.text, base_url, site)
    # Tekrarlayan ürün linklerini temizle
    unique_results = {}
    for n, p, u in raw_results:
        if u not in unique_results and n:
            unique_results[u] = Product(site=site, name=n, price=p, url=u)
            
    return list(unique_results.values())

async def search_all(query: str) -> list[Product]:
    async with httpx.AsyncClient() as client:
        tasks = [search_site(client, site, tmpl, query) for site, tmpl in SEARCH_URL_TEMPLATES.items()]
        per_site = await asyncio.gather(*tasks)
        
    all_results = [p for site_results in per_site for p in site_results]
    all_results.sort(key=lambda p: (p.price is None, p.price or 0))
    return all_results

# --- Mobil Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, 10k direnç, 220uf")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner("Siteler taranıyor..."):
            results = asyncio.run(search_all(query))
        
        if not results:
            st.error("Sonuç bulunamadı.")
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
