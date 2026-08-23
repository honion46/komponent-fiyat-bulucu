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

# Her sitenin çalışan arama URL'si
SEARCH_URL_TEMPLATES = {
    "Motorobit": "https://www.motorobit.com/arama?kelime={query}",
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Direnc.net": "https://www.direnc.net/arama?q={query}",
    "Samm Market": "https://market.samm.com/search?s={query}",
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

def extract_products(html: str, base_url: str, max_gap: int = 8) -> list[tuple[str, float | None, str]]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    link_queue = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if not text or not href or text.lower() in IGNORE_LINK_TEXT or href.startswith("#") or "javascript:" in href:
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
            if gap_counter > max_gap:
                candidate_name = None
                candidate_url = None

    return results

async def search_site(client: httpx.AsyncClient, site: str, url_template: str, query: str) -> list[Product]:
    encoded_query = urllib.parse.quote_plus(query)
    url = url_template.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12, follow_redirects=True)
        if resp.status_code != 200:
            return []
    except Exception:
        return []
    raw_results = extract_products(resp.text, base_url)
    return [Product(site=site, name=n, price=p, url=u) for n, p, u in raw_results]

async def search_all(query: str) -> list[Product]:
    async with httpx.AsyncClient() as client:
        tasks = [search_site(client, site, tmpl, query) for site, tmpl in SEARCH_URL_TEMPLATES.items()]
        per_site = await asyncio.gather(*tasks)
    all_results = [p for site_results in per_site for p in site_results]
    all_results.sort(key=lambda p: (p.price is None, p.price or 0))
    return all_results

# --- Streamlit Arayüzü ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

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
