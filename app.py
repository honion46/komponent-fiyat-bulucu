import asyncio
import re
import urllib.parse
from dataclasses import dataclass
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import os

# Streamlit Cloud üzerinde ilk çalışmada Playwright tarayıcısını otomatik kurmak için
@st.cache_resource
def install_playwright():
    os.system("playwright install chromium")

install_playwright()

from playwright.async_api import async_playwright

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

PRICE_RE = re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)", re.IGNORECASE)
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

async def search_all_playwright(query: str):
    async with async_playwright() as p:
        # Gerçek bir Chrome tarayıcı başlatıyoruz (arkaplanda gizli)
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        
        # Tarayıcı kimliğini (User-Agent) gerçek bir Windows bilgisayar gibi ayarlıyoruz
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        # Aynı anda en fazla 4 siteye girmesi için sınırlandırıcı (RAM çökmesini engeller)
        semaphore = asyncio.Semaphore(4)
        
        async def scrape_site(site, url_tmpl):
            async with semaphore:
                encoded_query = urllib.parse.quote_plus(query)
                url = url_tmpl.format(query=encoded_query)
                base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]
                
                page = await context.new_page()
                try:
                    # Sayfanın ana yapısı yüklenene kadar bekle (maks 15 saniye)
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    
                    # Sayfa içindeki dinamik JS'lerin ve fiyatların yüklenmesi için 1.5 saniye ekstra bekle
                    await page.wait_for_timeout(1500)
                    
                    # Render edilmiş sayfanın son halini al
                    html = await page.content()
                    
                    products = extract_products(html, base_url, site, query)
                    status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
                    return site, products, status
                except Exception as e:
                    return site, [], "Hata/Zaman Aşımı"
                finally:
                    await page.close()

        tasks = [scrape_site(site, tmpl) for site, tmpl in SEARCH_URL_TEMPLATES.items()]
        results = await asyncio.gather(*tasks)
        await browser.close()
        return results

# --- Mobil Web UI (Streamlit) ---
st.set_page_config(page_title="Komponent Fiyat Arama", page_icon="⚡", layout="wide")
st.title("⚡ Komponent Fiyat Karşılaştırma (Playwright Motoru)")

query = st.text_input("Aranacak Komponent:", placeholder="örn: esp32, direnç 10k, mp1584")

if st.button("Fiyatları Getir", type="primary", use_container_width=True):
    if not query.strip():
        st.warning("Lütfen bir ürün adı girin.")
    else:
        with st.spinner(f"Tarayıcı simülasyonu başlatıldı. {len(SEARCH_URL_TEMPLATES)} site taranıyor (Yaklaşık 10-15sn)..."):
            site_results = asyncio.run(search_all_playwright(query))
        
        with st.expander("🔍 Site Tarama Durumları", expanded=False):
            for site, prods, status in site_results:
                if "Hata" in status or "Aşımı" in status:
                    st.error(f"**{site}:** {status}")
                elif "bulunamadı" in status:
                    st.warning(f"**{site}:** {status}")
                else:
                    st.success(f"**{site}:** {status}")

        all_products = [p for _, prods, _ in site_results for p in prods]
        
        if not all_products:
            st.error("Hiçbir sitede sonuç bulunamadı.")
        else:
            # Fiyata göre sıralama
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
