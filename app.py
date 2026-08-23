import asyncio
import re
import urllib.parse
from dataclasses import dataclass
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

# Arama Şablonları (Direnc.net için Cloudflare Proxy eklendi)
SEARCH_CONFIGS = {
    "Robotistan": {
        "url": "https://www.robotistan.com/arama?q={query}",
        "use_proxy": False,
    },
    "Motorobit": {
        "url": "https://www.motorobit.com/arama?q={query}",
        "use_proxy": False,
    },
    "Direnc.net": {
        "url": "https://www.direnc.net/arama?q={query}",
        "use_proxy": True,  # Datacenter 403 engelini aşmak için proxy üzerinden çeker
    },
    "Samm Market": {
        "url": "https://market.samm.com/search?s={query}",
        "use_proxy": False,
    },
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

def extract_products(html: str, base_url: str, site_name: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    results = []
    seen_urls = set()

    # Ürün kartı seçicileri
    cards = soup.select(
        ".productItem, .showcase, .product-card, .product-item, .item-grid, "
        ".productBox, .product-detail-card, .catalog-item, div[data-product-id], .product-layout"
    )

    for card in cards:
        name_tag = card.select_one("a.product-name, a.productName, a.product-title, .name a, a[title], h3 a, h2 a, .title a")
        if not name_tag:
            name_tag = card.find("a", href=True)
            
        name = name_tag.get_text(strip=True) if name_tag else ""
        href = name_tag.get("href", "") if name_tag else ""
        
        if not name or len(name) < 3 or not href:
            continue

        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        if full_url in seen_urls or full_url.startswith("#") or "javascript:" in full_url:
            continue

        card_text = card.get_text(separator=" ")
        price_match = PRICE_RE.search(card_text)
        price = parse_price(price_match.group(1)) if price_match else None

        results.append(Product(site=site_name, name=name, price=price, url=full_url))
        seen_urls.add(full_url)

    # Yedek metin taraması (Eğer özel kart yakalanamadıysa)
    if not results:
        for a in soup.find_all("a", href=True):
            name = a.get_text(strip=True)
            href = a["href"]
            
            if len(name) < 4 or href.startswith("#") or "javascript:" in href:
                continue

            full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
            if full_url in seen_urls:
                continue

            parent = a.find_parent(["div", "li", "td", "article"])
            if parent:
                parent_text = parent.get_text(separator=" ")
                m = PRICE_RE.search(parent_text)
                if m:
                    price = parse_price(m.group(1))
                    results.append(Product(site=site_name, name=name, price=price, url=full_url))
                    seen_urls.add(full_url)

    return results

async def search_site(session: AsyncSession, site: str, config: dict, query: str) -> tuple[str, list[Product], str]:
    encoded_query = urllib.parse.quote_plus(query)
    target_url = config["url"].format(query=encoded_query)
    base_url = "https://" + target_url.split("://", 1)[1].split("/", 1)[0]
    
    # Cloudflare 403 veren siteleri proxy üzerinden geçir
    if config.get("use_proxy"):
        fetch_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(target_url)}"
    else:
        fetch_url = target_url

    try:
        resp = await session.get(fetch_url, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return site, [], f"Hata: HTTP {resp.status_code}"
    except Exception as e:
        return site, [], f"Bağlantı hatası: {str(e)[:30]}"
        
    products = extract_products(resp.text, base_url, site)
    status_msg = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
    return site, products, status_msg

async def search_all(query: str):
    async with AsyncSession(impersonate="chrome124") as session:
        tasks = [search_site(session, site, cfg, query) for site, cfg in SEARCH_CONFIGS.items()]
        return await asyncio.gather(*tasks)

# --- Mobil Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma")

query = st.text_input("Aranacak Komponent:", placeholder="örn: Mp1584, esp32, 10k direnç")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner("Siteler taranıyor..."):
            site_results = asyncio.run(search_all(query))
        
        with st.expander("🔍 Site Tarama Durumları", expanded=True):
            for site, prods, status in site_results:
                st.write(f"**{site}:** {status}")

        all_products = [p for _, prods, _ in site_results for p in prods]
        all_products.sort(key=lambda p: (p.price is None, p.price or 0))

        if not all_products:
            st.error("Hiçbir sitede sonuç bulunamadı.")
        else:
            data = []
            for r in all_products:
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
