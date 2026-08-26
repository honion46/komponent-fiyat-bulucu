import concurrent.futures, json, os, re, time, urllib.parse
from dataclasses import dataclass
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

SEARCH_URL_TEMPLATES={
"Robotistan":"https://www.robotistan.com/arama?q={query}",
"Motorobit":"https://www.motorobit.com/arama?q={query}",
"Robolink":"https://www.robolinkmarket.com/arama?q={query}",
"Robocombo":"https://www.robocombo.com/Arama?1&kelime={query}",
"Kartal Otomasyon":"https://www.kartalotomasyon.com.tr/arama/{query}",
"F1 Depo":"https://www.f1depo.com/arama/{query}",
"Robotzade":"https://www.robotzade.com/arama/{query}",
"Elektrodepo":"https://www.elektrodepo.com/arama/{query}",
"Komponentci":"https://www.komponentci.net/arama?tip=1&kat=0&word={query}&search=",
"RoboShop":"https://www.roboshop.com.tr/arama?k={query}",
"Görsu Elektronik":"https://gorsuelektronik.com/arama?q={query}",
"Robot Sepeti":"https://www.robotsepeti.com/arama?q={query}",
"Robo90":"https://www.robo90.com/arama?q={query}"}
SLOW_AJAX_SITES={"Robolink","Motorobit"}; CLOUDFLARE_SITES=set(); SITE_WAIT_SELECTORS={}
PRICE_RE=re.compile(r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|TRY)",re.I)
NUM_ONLY_RE=re.compile(r"^[\d.,]+$")
OUT_TERMS=(
    "gelince haber ver","gelince haber veriniz","tükendi",
    "stokta yok","stokta yoktur","stok yok","stok : yok","stok: yok",
    "stokta bulunmuyor","stokta bulunmamaktadır",
    "stokta mevcut değil","stokta mevcut değildir",
    "stok dışı","stok disi","temin edilemiyor",
    "satışa kapalı","satişa kapalı","ürün tükendi",
    "stokta yok!"
)
IN_TERMS=(
    "sepete ekle","sepete ekle!","sepete ekleyin",
    "satın al","hemen al","şimdi al","stokta",
    "stokta var","stokta mevcut","stoklarımızda"
)
IGNORE={"add to cart","sepete ekle","favorilere ekle","incele","tümü","detay","giriş yap","üye ol","sipariş takibi","iletişim","kategoriler","yardım","hesabım","sepetim","müşteri hizmetleri","satış yap"}

@dataclass
class Product:
    site:str; name:str; price:float|None; url:str; stock:str="unknown"

def parse_price(s):
    """Türkçe/İngilizce fiyat biçimlerini güvenli biçimde sayıya çevirir."""
    try:
        x = re.sub(r"[^0-9.,]", "", str(s).strip())
        if not x:
            return None

        # Hem nokta hem virgül varsa son görülen ayırıcı ondalıktır.
        if "," in x and "." in x:
            if x.rfind(",") > x.rfind("."):
                x = x.replace(".", "").replace(",", ".")
            else:
                x = x.replace(",", "")

        # Sadece virgül varsa: 1-2 hane ondalık, 3 hane binlik.
        elif "," in x:
            left, right = x.rsplit(",", 1)
            if len(right) in (1, 2):
                x = left.replace(",", "") + "." + right
            else:
                x = x.replace(",", "")

        # Sadece nokta varsa: 1-2 hane ondalık, 3 hane binlik.
        elif "." in x:
            left, right = x.rsplit(".", 1)
            if len(right) in (1, 2):
                x = left.replace(".", "") + "." + right
            else:
                x = x.replace(".", "")

        return float(x)
    except (TypeError, ValueError):
        return None

def stock_of(text):
    t=re.sub(r"\s+"," ",text.lower()).strip()
    if any(x in t for x in OUT_TERMS): return "out"
    if any(x in t for x in IN_TERMS): return "in"
    return "unknown"

def jsonld(soup,site,keys):
    found=[]
    def walk(o):
        if isinstance(o,dict):
            typ=o.get("@type")
            if typ=="Product" or (isinstance(typ,list) and "Product" in typ): found.append(o)
            elif typ=="ItemList":
                for x in o.get("itemListElement",[]): walk(x.get("item",x) if isinstance(x,dict) else x)
            else:
                for v in o.values(): walk(v)
        elif isinstance(o,list):
            for x in o: walk(x)
    for s in soup.find_all("script",attrs={"type":"application/ld+json"}):
        try: walk(json.loads(s.string or ""))
        except: pass
    out=[]; seen=set()
    for p in found:
        name=str(p.get("name") or "").strip()
        if not name or (keys and not any(k in name.lower() for k in keys)): continue
        off=p.get("offers",{}); off=off[0] if isinstance(off,list) and off else off
        if not isinstance(off,dict): off={}
        url=p.get("url") or off.get("url") or ""
        if not url or url in seen: continue
        av=str(off.get("availability","")).lower()
        stock="out" if "outofstock" in av or "soldout" in av else "in" if "instock" in av or "limitedavailability" in av else "unknown"
        price=parse_price(str(off.get("price"))) if off.get("price") is not None else None
        seen.add(url); out.append(Product(site,name,price,url,stock))
    return out

def enrich_jsonld_stock_from_cards(soup, products):
    """JSON-LD ürünlerinde stok unknown ise aynı ürün kartının görünür metninden stok durumunu tamamlar. Birçok mağaza ürün/fiyat bilgisini JSON-LD'de verirken 'Sepete Ekle' veya 'Stokta Yok' bilgisini HTML ürün kartında tutuyor. """
    for p in products:
        if p.stock != "unknown":
            continue

        try:
            target = p.url.rstrip("/")

            anchors = soup.find_all("a", href=True)
            for a in anchors:
                href = a.get("href", "")
                full = href if href.startswith("http") else (
                    "https://" + href.lstrip("/")
                    if href.startswith("//") else href
                )

                # URL'nin son kısmını karşılaştır; izleme parametrelerini yok say.
                if not full.rstrip("/").split("?")[0].endswith(
                    target.split("?")[0].rstrip("/").split("/")[-1]
                ):
                    continue

                # Ürün kartı olabilecek en yakın üst elemanları kontrol et.
                node = a
                for _ in range(6):
                    node = getattr(node, "parent", None)
                    if node is None:
                        break

                    text = node.get_text(" ", strip=True)
                    if not text:
                        continue

                    low = re.sub(r"\s+", " ", text.lower()).strip()

                    # Negatif ifade her zaman pozitif ifadeden önce.
                    if any(x in low for x in OUT_TERMS):
                        p.stock = "out"
                        break

                    if any(x in low for x in IN_TERMS):
                        p.stock = "in"
                        break

                if p.stock != "unknown":
                    break

        except Exception:
            pass

    return products


def extract(html,base,site,query):
    soup=BeautifulSoup(html,"lxml"); keys=[x.lower() for x in query.split() if len(x)>1]
    j=jsonld(soup,site,keys)
    if j:
        return enrich_jsonld_stock_from_cards(soup, j)
    for tag in soup(["script","style","nav","footer","header","noscript","aside"]): tag.decompose()
    out=[]; seen=set()
    def clean(t):
        t=re.sub(r"peşin fiyatına \d+ taksit|\btaksit\b|ücretsiz kargo|stoktan teslim|\byeni\b|sepete ekle|favorilere ekle|incele|\(\s*\d+\s*\)|%\s*\d+|\d+\s*yorum"," ",t,flags=re.I)
        return re.sub(r"\s+"," ",PRICE_RE.sub(" ",t)).strip(" -–|")
    for a in soup.find_all("a",href=True):
        href=a["href"]; text=a.get_text(" ",strip=True)
        if not href or href.startswith("#") or "javascript:" in href or not text: continue
        prices=PRICE_RE.findall(text)
        if not prices: continue
        name=clean(text)
        if len(name)<3 or (keys and not any(k in name.lower() for k in keys)): continue
        url=href if href.startswith("http") else base.rstrip("/")+"/"+href.lstrip("/")
        if url in seen: continue
        seen.add(url); out.append(Product(site,name,parse_price(prices[-1]),url,stock_of(text)))
    if out:return out
    links=[]
    for a in soup.find_all("a"):
        text=a.get_text(strip=True); href=a.get("href","")
        if not text or not href or text.lower() in IGNORE or href.startswith("#") or "javascript:" in href: continue
        if keys and not any(k in text.lower() for k in keys): continue
        url=href if href.startswith("http") else base.rstrip("/")+"/"+href.lstrip("/"); links.append((text,url))
    lines=[x.strip() for x in soup.get_text("\n").split("\n") if x.strip()]; lines2=[]; i=0
    while i<len(lines):
        if i+1<len(lines) and NUM_ONLY_RE.match(lines[i]) and lines[i+1].upper() in ("TL","₺","TRY"): lines2.append(lines[i]+" TL"); i+=2
        else: lines2.append(lines[i]); i+=1
    idx=0; name=url=buf=None; gap=0
    for line in lines2:
        if idx<len(links) and line==links[idx][0]: name,url=links[idx]; buf=name; idx+=1; gap=0; continue
        if name: buf+=" "+line
        m=PRICE_RE.search(line)
        if m and name:
            if url not in seen: out.append(Product(site,name,parse_price(m.group(1)),url,stock_of(buf))); seen.add(url)
            name=url=buf=None; continue
        if name:
            gap+=1
            if gap>30:name=url=buf=None
    return out

_virtual_display=None
def get_driver(stealth=False):
    global _virtual_display
    o=Options(); ready=False
    if stealth:
        if _virtual_display is None:
            try:
                from pyvirtualdisplay import Display
                _virtual_display=Display(visible=0,size=(1920,1080)); _virtual_display.start(); ready=True
            except: _virtual_display=False
        elif _virtual_display is not False: ready=True
    if not ready:o.add_argument("--headless=new")
    for x in ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--window-size=1920,1080","--disable-blink-features=AutomationControlled","--lang=tr-TR"]:o.add_argument(x)
    o.add_experimental_option("excludeSwitches",["enable-automation"]); o.add_experimental_option("useAutomationExtension",False)
    o.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
    for p in ["/usr/bin/chromium","/usr/bin/chromium-browser","/usr/bin/google-chrome"]:
        if os.path.exists(p):o.binary_location=p;break
    try:d=webdriver.Chrome(service=Service("/usr/bin/chromedriver"),options=o)
    except:d=webdriver.Chrome(service=Service(ChromeDriverManager().install()),options=o)
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":"Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"})
    return d

def wait_content(d,timeout=15):
    end=time.time()+timeout
    while time.time()<end:
        try:
            if len(re.findall(r"\d[\d.,]*\s*(?:TL|₺)",d.execute_script("return document.body.innerText||''"),re.I))>=2:return True
        except:pass
        time.sleep(.5)
    return False

def cookies(d):
    js="""const a=['kabul et','kabul ediyorum','tümünü kabul et','accept','accept all','anladım','tamam','kapat','reddet','close'];for(const e of document.querySelectorAll('button,a,div[role=button]')){const t=(e.innerText||'').trim().toLowerCase();if(t&&a.some(x=>t.includes(x))&&t.length<40){e.click();return true}}return false;"""
    try:d.execute_script(js)
    except:pass


def detect_product_page_stock(driver, site):
    """Ürün sayfasındaki gerçek stok/satın alma durumunu belirler. Öncelik: 1. Ürün satın alma alanındaki görünür buton/metin 2. Açık stok mesajı 3. JSON-LD availability 4. Sayfa metni """
    try:
        js = r""" const norm = s => (s || '') .replace(/\s+/g, ' ') .trim() .toLocaleLowerCase('tr-TR'); const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); return !!(r.width && r.height && st.display !== 'none' && st.visibility !== 'hidden'); }; const positive = [ 'sepete ekle', 'sepete ekle!', 'satın al', 'hemen al', 'şimdi al', 'add to cart', 'buy now', 'stokta', 'stokta var', 'stokta mevcut' ]; const negative = [ 'gelince haber ver', 'gelince haber veriniz', 'tükendi', 'stokta yok', 'stokta yoktur', 'stok yok', 'stok : yok', 'stok: yok', 'stokta bulunmuyor', 'stokta bulunmamaktadır', 'stokta mevcut değil', 'stokta mevcut değildir', 'stok dışı', 'stok disi', 'temin edilemiyor', 'satışa kapalı', 'ürün tükendi' ]; // Ürün satın alma alanlarını özellikle ara. const selectors = [ 'form[action*="cart" i]', 'form[action*="sepet" i]', '.product-detail', '.product-detail-container', '.product-info', '.product-content', '.product-actions', '.product-buttons', '.add-to-cart', '#product', '[class*="product-detail" i]', '[class*="product-info" i]', '[class*="product-action" i]', '[class*="add-to-cart" i]', '[id*="product" i]' ]; const areas = []; for (const sel of selectors) { try { document.querySelectorAll(sel).forEach(el => { if (visible(el)) areas.push(el); }); } catch (_) {} } // Önce en küçük alanlarda karar ver. for (const area of areas) { const t = norm(area.innerText || ''); if (!t) continue; if (negative.some(x => t.includes(x))) return 'out'; const buttons = [ ...area.querySelectorAll( 'button, input[type="button"], input[type="submit"], ' + 'a[role="button"], [role="button"]' ) ].filter(visible); const bt = buttons.map(el => norm(el.innerText || el.value || el.getAttribute('aria-label')) ).filter(Boolean); if (bt.some(x => negative.some(n => x.includes(n)))) return 'out'; if (bt.some(x => positive.some(n => x.includes(n)))) return 'in'; if (positive.some(x => t.includes(x))) return 'in'; } // Genel görünür butonlar: footer/header'daki sahte "sepete ekle" // ifadelerinden önce açık stok mesajlarını kontrol et. const body = norm(document.body ? document.body.innerText : ''); if (negative.some(x => body.includes(x))) return 'out'; const buttons = [ ...document.querySelectorAll( 'button, input[type="button"], input[type="submit"], ' + 'a[role="button"], [role="button"]' ) ].filter(visible); const bt = buttons.map(el => norm(el.innerText || el.value || el.getAttribute('aria-label')) ).filter(Boolean); if (bt.some(x => negative.some(n => x.includes(n)))) return 'out'; if (bt.some(x => positive.some(n => x.includes(n)))) return 'in'; // JSON-LD availability for (const script of document.querySelectorAll( 'script[type="application/ld+json"]' )) { try { const raw = JSON.parse(script.textContent || '{}'); const stack = Array.isArray(raw) ? [...raw] : [raw]; while (stack.length) { const obj = stack.pop(); if (!obj || typeof obj !== 'object') continue; const av = obj.availability; if (typeof av === 'string') { const a = av.toLowerCase(); if (a.includes('outofstock') || a.includes('soldout')) return 'out'; if (a.includes('instock') || a.includes('limitedavailability') || a.includes('preorder')) return 'in'; } for (const v of Object.values(obj)) { if (v && typeof v === 'object') stack.push(v); } } } catch (_) {} } if (positive.some(x => body.includes(x))) return 'in'; return 'unknown'; """

        return driver.execute_script(js) or "unknown"
    except Exception:
        return "unknown"

def refine_product_stocks(driver, products, site, limit=4):
    """Arama sonucunda stok bilinmiyorsa ürün sayfasından kesinleştirir. Artık sadece F1 Depo/Robotzade değil; Elektrodepo, Komponentci ve diğer mağazalar da desteklenir. Arama kartı/JSON-LD zaten kesin bilgi verdiyse ürün sayfasına gidilmez. """
    checked = 0

    for p in products:
        if checked >= limit:
            break

        if p.stock != "unknown":
            continue

        try:
            driver.set_page_load_timeout(12)
            driver.get(p.url)
            time.sleep(0.55)

            detected = detect_product_page_stock(driver, site)

            if detected in {"in", "out"}:
                p.stock = detected

            checked += 1
        except Exception:
            checked += 1

    return products

def scrape(site,tpl,query):
    for attempt in range(2):
        d=None
        try:
            enc=urllib.parse.quote_plus(query); url=tpl.format(query=enc); base="https://"+url.split("://",1)[1].split("/",1)[0]
            d=get_driver(site in CLOUDFLARE_SITES); d.set_page_load_timeout(35); d.get(url); time.sleep(1); cookies(d)
            sel=SITE_WAIT_SELECTORS.get(site)
            if sel:
                try:WebDriverWait(d,10).until(EC.presence_of_element_located((By.CSS_SELECTOR,sel)))
                except:pass
            elif site in SLOW_AJAX_SITES:wait_content(d,20)
            else:time.sleep(3)
            cookies(d); d.execute_script("window.scrollTo(0,document.body.scrollHeight/2)"); time.sleep(1)
            products=extract(d.page_source,base,site,query)

            # F1 Depo / Robotzade: ürün sayfasındaki gerçek stok
            # butonunu kontrol et. "Gelince Haber Ver" = Yok,
            # "Sepete Ekle" = Var.
            products=refine_product_stocks(
                d, products, site
            )

            return site,products,"bulundu" if products else "bulunamadı",None,None
        except Exception as e:
            result=(site,[],f"hata:{type(e).__name__}",None,None)
            if attempt==1:return result
        finally:
            if d:
                try:d.quit()
                except:pass

def search_all(query, progress_callback=None):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fs = {
            ex.submit(scrape, s, t, query): s
            for s, t in SEARCH_URL_TEMPLATES.items()
        }
        done = 0
        for f in concurrent.futures.as_completed(fs):
            site = fs[f]
            done += 1
            try:
                result = f.result()
                results.append(result)
                found = bool(result[1])
            except Exception:
                result = (site, [], "bulunamadı", None, None)
                results.append(result)
                found = False
            if progress_callback:
                progress_callback(done, len(fs), site, found)
    return results


def stock_badge(s):return "🟢 Var" if s=="in" else "🔴 Yok" if s=="out" else "⚪ Bilinmiyor"

st.set_page_config(page_title="Komponent Fiyat Karşılaştırma",page_icon="⚡",layout="wide")
st.markdown("""<style> .block-container{padding-top:.55rem;padding-bottom:.55rem;max-width:900px} .small{font-size:.74rem;color:#888} .price{font-weight:800;white-space:nowrap} .result-table{width:100%;border-collapse:collapse;font-size:.78rem} .result-table th{color:#999;text-align:left;padding:7px 5px;border-bottom:1px solid #444;white-space:nowrap} .result-table td{padding:7px 5px;border-bottom:1px solid #292929;vertical-align:middle} .result-table .name{min-width:150px} .result-table .store{font-weight:700;white-space:nowrap} .result-table .go{color:#42b8ff;text-decoration:none;font-weight:700;white-space:nowrap} .result-table .stock{white-space:nowrap} </style>""",unsafe_allow_html=True)
st.title("⚡ Komponent Fiyat Karşılaştırma")
t1,t2=st.tabs(["🔍 Tek Ürün","🛒 Sepet Karşılaştırma"])

with t1:
    q=st.text_input("Aranacak Komponent:",placeholder="örn: L293D, ESP32, MP1584")
    if st.button("Fiyatları Getir",type="primary",use_container_width=True,key="search"):
        if not q.strip():st.warning("Lütfen bir ürün adı girin.")
        else:
            start=time.time()

            scan_box = st.empty()
            scan_state = {site: "⏳" for site in SEARCH_URL_TEMPLATES}

            def update_scan(done, total, site, found):
                scan_state[site] = "🟢" if found else "⚪"
                chips = " ".join(
                    f"{icon} {name}"
                    for name, icon in scan_state.items()
                )
                scan_box.info(
                    f"📡 **Canlı tarama — {done}/{total} site tamamlandı**\n\n{chips}"
                )

            scan_box.info(
                "📡 **Canlı tarama başlıyor...**\n\n"
                + " ".join(
                    f"⏳ {site}"
                    for site in SEARCH_URL_TEMPLATES
                )
            )

            results = search_all(q, progress_callback=update_scan)
            scan_box.empty()

            products=[p for _,ps,_,_,_ in results for p in ps]
            # Gerçek sayısal sıralama: 14,21 < 23,60 < 115,44
            products.sort(key=lambda p:(p.price is None,p.price if p.price is not None else float("inf")))
            found=sum(bool(ps) for _,ps,_,_,_ in results); elapsed=time.time()-start
            st.success(f"✅ Tarama tamamlandı — {len(SEARCH_URL_TEMPLATES)} site · {len(products)} ürün · {elapsed:.0f} saniye")
            st.info(f"📡 **Tarama tamamlandı** — {found}/{len(SEARCH_URL_TEMPLATES)} site ürün buldu · ⏱️ {elapsed:.0f} sn · 📦 {len(products)} ürün")
            statuses=[]
            for site,ps,_,_,_ in sorted(results,key=lambda x:x[0]):statuses.append(f"🟢 **{site}** — bulundu" if ps else f"⚪ **{site}** — bulunamadı")
            st.markdown(" • ".join(statuses))
            ins=sum(p.stock=="in" for p in products); outs=sum(p.stock=="out" for p in products); unk=sum(p.stock=="unknown" for p in products)
            st.caption(f"🟢 {ins} stokta • 🔴 {outs} stokta yok • ⚪ {unk} bilinmiyor • 💰 {sum(p.price is not None for p in products)} fiyat doğrulandı")
            if not products:st.error("Hiçbir sitede sonuç bulunamadı.")
            else:
                st.subheader(f"🔎 Arama Sonuçları ({len(products)})")
                cheapest=next((p for p in products if p.price is not None),None)

                rows=[]
                for p in products:
                    stock=stock_badge(p.stock)
                    price="—" if p.price is None else f"{p.price:,.2f} TL"
                    name=(p.name[:85]+"...") if len(p.name)>85 else p.name
                    rows.append(
                        f'<tr>'
                        f'<td class="store">{p.site}</td>'
                        f'<td class="name">{name}</td>'
                        f'<td class="stock">{stock}</td>'
                        f'<td class="price">{price}</td>'
                        f'<td><a class="go" href="{p.url}" target="_blank">🌐 Siteye Git</a></td>'
                        f'</tr>'
                    )

                st.markdown(
                    '<div style="overflow-x:auto;border:1px solid #333;border-radius:9px">'
                    '<table class="result-table">'
                    '<thead><tr><th>Mağaza</th><th>Ürün</th><th>Stok</th><th>Fiyat</th><th>Bağlantı</th></tr></thead>'
                    '<tbody>'+''.join(rows)+'</tbody></table></div>',
                    unsafe_allow_html=True
                )

with t2:
    st.caption("Her satıra bir ürün yaz.")
    text=st.text_area("Sepet:",placeholder="esp32\nhc05\n1k direnç\nbreadboard",height=130)
    if st.button("🛒 Sepeti Karşılaştır",type="primary",use_container_width=True,key="basket"):
        items=[x.strip() for x in text.splitlines() if x.strip()]
        if not items:st.warning("Lütfen en az bir ürün girin.")
        else:
            prog=st.progress(0,text="Başlıyor..."); allr={}
            for i,item in enumerate(items):
                prog.progress(i/len(items),text=f"🔎 {i+1}/{len(items)} — {item}"); allr[item]=search_all(item)
            prog.progress(1,text="✅ Tamamlandı")
            comp={s:{"total":0.0,"found":0,"missing":[],"picks":{}} for s in SEARCH_URL_TEMPLATES}
            for item in items:
                present=set()
                for s,ps,_,_,_ in allr[item]:
                    avail=[p for p in ps if p.price is not None and p.stock!="out"]
                    if avail:
                        p=min(avail,key=lambda x:x.price);comp[s]["total"]+=p.price;comp[s]["found"]+=1;comp[s]["picks"][item]=p;present.add(s)
                for s in SEARCH_URL_TEMPLATES:
                    if s not in present:comp[s]["missing"].append(item)
            ranked=sorted(comp.items(),key=lambda x:(len(x[1]["missing"]),x[1]["total"] if x[1]["found"] else float("inf")))
            full=[x for x in ranked if not x[1]["missing"] and x[1]["found"]]
            if full:st.success(f"🏆 En ucuz tam sepet: **{full[0][0]}** — **{full[0][1]['total']:,.2f} TL**")
            rows=[{"Mağaza":s,"Bulunan":f"{d['found']}/{len(items)}","Toplam":f"{d['total']:,.2f} TL","Eksik":", ".join(d["missing"]) or "—"} for s,d in ranked if d["found"]]
            if rows:st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
            with st.expander("📋 Ürün Bazında Ayrıntılar"):
                for item in items:
                    rows=[]
                    for s,d in ranked:
                        p=d["picks"].get(item)
                        if p:rows.append({"Mağaza":s,"Fiyat":f"{p.price:,.2f} TL","Ürün":p.name,"Site":p.url})
                    if rows:
                        rows.sort(key=lambda x:parse_price(x["Fiyat"]))
                        st.dataframe(pd.DataFrame(rows),column_config={"Site":st.column_config.LinkColumn("Siteye Git")},hide_index=True,use_container_width=True)
                    else:st.caption(f"{item}: bulunamadı.")

st.divider(); st.caption("⚡ Komponent Fiyat Karşılaştırma · Mehmet Özberk · v1.6")
