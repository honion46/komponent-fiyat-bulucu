# ============================================================
# ⚡ KOMPONENT FİYAT KARŞILAŞTIRMA
# v1.5 - Görsel arayüz
# v1.4 arama motoru korunmuştur.
# ============================================================

import concurrent.futures
import json
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


# ============================================================
# MAĞAZALAR
# ============================================================

SEARCH_URL_TEMPLATES = {
    "Robotistan": "https://www.robotistan.com/arama?q={query}",
    "Motorobit": "https://www.motorobit.com/arama?q={query}",
    "Robolink": "https://www.robolinkmarket.com/arama?q={query}",
    "Robocombo": "https://www.robocombo.com/Arama?1&kelime={query}",
    "Kartal Otomasyon": "https://www.kartalotomasyon.com.tr/arama/{query}",
    "F1 Depo": "https://www.f1depo.com/arama/{query}",
    "Robotzade": "https://www.robotzade.com/arama/{query}",
    "Elektrodepo": "https://www.elektrodepo.com/arama/{query}",
    "Komponentci": "https://www.komponentci.net/arama?tip=1&kat=0&word={query}&search=",
    "RoboShop": "https://www.roboshop.com.tr/arama?k={query}",
    "Görsu Elektronik": "https://gorsuelektronik.com/arama?q={query}",
    "Robot Sepeti": "https://www.robotsepeti.com/arama?q={query}",
    "Robo90": "https://www.robo90.com/arama?q={query}",
}

SLOW_AJAX_SITES = {
    "Robolink",
    "Motorobit",
}

CLOUDFLARE_SITES = set()
SITE_WAIT_SELECTORS = {}

PRICE_RE = re.compile(
    r"([\d]{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|TRY)",
    re.IGNORECASE,
)

NUM_ONLY_RE = re.compile(r"^[\d.,]+$")

IGNORE_LINK_TEXT = {
    "add to cart",
    "sepete ekle",
    "favorilere ekle",
    "add to favorites",
    "i̇ncele",
    "incele",
    "javascript:void(0);",
    "see all",
    "tümü",
    "detay",
    "giriş yap",
    "üye ol",
    "sipariş takibi",
    "iletişim",
    "kategoriler",
    "yardım",
    "hesabım",
    "sepetim",
    "günün fırsatları",
    "müşteri hizmetleri",
    "satış yap",
}


# ============================================================
# ÜRÜN
# ============================================================

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


# ============================================================
# JSON-LD
# ============================================================

def extract_products_jsonld(
    soup: BeautifulSoup,
    site_name: str,
    keywords: list[str],
) -> list[Product]:

    found = []

    def collect(obj):

        if isinstance(obj, dict):

            t = obj.get("@type")

            if t == "Product":
                found.append(obj)

            elif t == "ItemList":

                for el in obj.get("itemListElement", []):
                    if isinstance(el, dict):
                        collect(el.get("item", el))

            else:

                for value in obj.values():
                    collect(value)

        elif isinstance(obj, list):

            for el in obj:
                collect(el)

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        collect(data)

    results = []
    seen_urls = set()

    for product in found:

        name = (product.get("name") or "").strip()

        if not name:
            continue

        if keywords and not any(
            k in name.lower() for k in keywords
        ):
            continue

        offers = product.get("offers", {})

        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        if not isinstance(offers, dict):
            offers = {}

        url = (
            product.get("url")
            or offers.get("url")
            or ""
        )

        if not url:
            continue

        price = None
        raw_price = offers.get("price")

        if raw_price is not None:

            try:
                price = float(
                    str(raw_price).replace(",", ".")
                )
            except Exception:
                price = None

        if url in seen_urls:
            continue

        seen_urls.add(url)

        results.append(
            Product(
                site_name,
                name,
                price,
                url,
            )
        )

    return results


# ============================================================
# ÜRÜN AYRIŞTIRMA
# ============================================================

def extract_products(
    html: str,
    base_url: str,
    site_name: str,
    query: str,
) -> list[Product]:

    soup = BeautifulSoup(html, "lxml")

    keywords = [
        k.lower()
        for k in query.split()
        if len(k) > 1
    ]

    jsonld = extract_products_jsonld(
        soup,
        site_name,
        keywords,
    )

    if jsonld:
        return jsonld

    # Gereksiz alanları kaldır
    for tag in soup(
        [
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "noscript",
            "aside",
        ]
    ):
        tag.decompose()

    results = []
    seen_urls = set()

    badge_res = [
        re.compile(r"peşin fiyatına \d+ taksit", re.I),
        re.compile(r"\btaksit\b", re.I),
        re.compile(r"ücretsiz kargo", re.I),
        re.compile(r"stoktan teslim", re.I),
        re.compile(r"\byeni\b", re.I),
        re.compile(r"sepete ekle", re.I),
        re.compile(r"favorilere ekle", re.I),
        re.compile(r"i̇ncele|incele", re.I),
        re.compile(r"\(\s*\d+\s*\)"),
        re.compile(r"%\s*\d+"),
        re.compile(r"\d+\s*yorum", re.I),
        re.compile(r"stokta\s*yok", re.I),
    ]

    def clean_name(text):

        for pattern in badge_res:
            text = pattern.sub(" ", text)

        text = PRICE_RE.sub(" ", text)

        text = re.sub(r"\s+", " ", text)

        return text.strip(" -–|")

    # --------------------------------------------------------
    # Ürün kartı tek link içindeyse
    # --------------------------------------------------------

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if (
            not href
            or href.startswith("#")
            or "javascript:" in href
        ):
            continue

        text = a.get_text(
            separator=" ",
            strip=True,
        )

        if not text:
            continue

        prices = PRICE_RE.findall(text)

        if not prices:
            continue

        name = clean_name(text)

        if len(name) < 3:
            continue

        if keywords and not any(
            k in name.lower()
            for k in keywords
        ):
            continue

        full_url = (
            href
            if href.startswith("http")
            else base_url.rstrip("/")
            + "/"
            + href.lstrip("/")
        )

        if full_url in seen_urls:
            continue

        price = parse_price(prices[-1])

        results.append(
            Product(
                site_name,
                name,
                price,
                full_url,
            )
        )

        seen_urls.add(full_url)

    if results:
        return results

    # --------------------------------------------------------
    # Yedek metin ayrıştırıcı
    # --------------------------------------------------------

    link_queue = []

    for a in soup.find_all("a"):

        text = a.get_text(strip=True)
        href = a.get("href", "")

        if (
            not text
            or not href
            or text.lower() in IGNORE_LINK_TEXT
            or href.startswith("#")
            or "javascript:" in href
        ):
            continue

        if keywords and not any(
            k in text.lower()
            for k in keywords
        ):
            continue

        full_url = (
            href
            if href.startswith("http")
            else base_url.rstrip("/")
            + "/"
            + href.lstrip("/")
        )

        link_queue.append(
            (text, full_url)
        )

    raw_lines = [
        line.strip()
        for line in soup.get_text(
            separator="\n"
        ).split("\n")
        if line.strip()
    ]

    lines = []

    i = 0

    while i < len(raw_lines):

        cur = raw_lines[i]

        if (
            i + 1 < len(raw_lines)
            and NUM_ONLY_RE.match(cur)
            and raw_lines[i + 1].strip().upper()
            in ("TL", "₺", "TRY")
        ):

            lines.append(f"{cur} TL")
            i += 2

        else:

            lines.append(cur)
            i += 1

    link_idx = 0
    candidate_name = None
    candidate_url = None
    gap_counter = 0

    for line in lines:

        if (
            link_idx < len(link_queue)
            and line == link_queue[link_idx][0]
        ):

            candidate_name, candidate_url = (
                link_queue[link_idx]
            )

            link_idx += 1
            gap_counter = 0
            continue

        price_match = PRICE_RE.search(line)

        if price_match and candidate_name:

            if candidate_url not in seen_urls:

                price = parse_price(
                    price_match.group(1)
                )

                results.append(
                    Product(
                        site_name,
                        candidate_name,
                        price,
                        candidate_url,
                    )
                )

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


# ============================================================
# SELENIUM
# ============================================================

_virtual_display = None


def get_driver(stealth=False):

    global _virtual_display

    options = Options()

    display_ready = False

    if stealth:

        if _virtual_display is None:

            try:

                from pyvirtualdisplay import Display

                _virtual_display = Display(
                    visible=0,
                    size=(1920, 1080),
                )

                _virtual_display.start()
                display_ready = True

            except Exception:

                _virtual_display = False

        elif _virtual_display is not False:

            display_ready = True

    if not display_ready:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )
    options.add_argument("--lang=tr-TR")

    options.add_experimental_option(
        "excludeSwitches",
        ["enable-automation"],
    )

    options.add_experimental_option(
        "useAutomationExtension",
        False,
    )

    options.add_argument(
        "--user-agent=Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    for path in [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]:

        if os.path.exists(path):

            options.binary_location = path
            break

    try:

        service = Service(
            "/usr/bin/chromedriver"
        )

        driver = webdriver.Chrome(
            service=service,
            options=options,
        )

    except Exception:

        service = Service(
            ChromeDriverManager().install()
        )

        driver = webdriver.Chrome(
            service=service,
            options=options,
        )

    stealth_js = """
    Object.defineProperty(
        navigator,
        'webdriver',
        {get: () => undefined}
    );

    Object.defineProperty(
        navigator,
        'plugins',
        {get: () => [1,2,3,4,5]}
    );

    Object.defineProperty(
        navigator,
        'languages',
        {get: () => [
            'tr-TR','tr','en-US','en'
        ]}
    );

    window.chrome = {runtime:{}};
    """

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": stealth_js},
    )

    return driver


def wait_for_real_content(
    driver,
    timeout=15,
    min_matches=2,
):

    js = """
    const t =
        document.body.innerText || '';

    const matches =
        t.match(
            /\\d[\\d.,]*\\s*(TL|₺)/g
        ) || [];

    return matches.length;
    """

    end = time.time() + timeout

    while time.time() < end:

        try:

            count = driver.execute_script(js)

            if count >= min_matches:
                return True

        except Exception:
            pass

        time.sleep(0.5)

    return False


def dismiss_cookie_banner(driver):

    texts = [
        "kabul et",
        "kabul ediyorum",
        "onayla",
        "onaylıyorum",
        "tümünü kabul et",
        "accept",
        "accept all",
        "i agree",
        "anladım",
        "tamam",
        "izin ver",
        "daha sonra",
        "şimdi değil",
        "hayır",
        "vazgeç",
        "kapat",
        "reddet",
        "not now",
        "no thanks",
        "dismiss",
        "close",
    ]

    js = """
    const texts = arguments[0];

    const nodes =
        document.querySelectorAll(
            'button,a,div[role="button"],'
            + 'span[role="button"]'
        );

    for (const el of nodes) {

        const t =
            (el.innerText || '')
            .trim()
            .toLowerCase();

        if (
            t &&
            texts.some(x => t.includes(x)) &&
            t.length < 40
        ) {
            el.click();
            return true;
        }
    }

    const closeEls =
        document.querySelectorAll(
            '[aria-label*="close" i],'
            + '[aria-label*="kapat" i],'
            + '.close,.modal-close,.popup-close'
        );

    for (const el of closeEls) {
        el.click();
        return true;
    }

    return false;
    """

    try:

        driver.execute_script(
            js,
            texts,
        )

        from selenium.webdriver.common.keys import Keys

        driver.find_element(
            "tag name",
            "body",
        ).send_keys(Keys.ESCAPE)

    except Exception:
        pass


def scrape_site(
    site,
    url_tmpl,
    query,
):

    for attempt in range(2):

        result = _scrape_site_once(
            site,
            url_tmpl,
            query,
        )

        if (
            "TimeoutException"
            not in result[2]
            or attempt == 1
        ):

            return result

    return result


def _scrape_site_once(
    site,
    url_tmpl,
    query,
):

    encoded_query = urllib.parse.quote_plus(query)

    url = url_tmpl.format(
        query=encoded_query
    )

    base_url = (
        "https://"
        + url.split("://", 1)[1]
        .split("/", 1)[0]
    )

    driver = None

    try:

        driver = get_driver(
            stealth=(
                site in CLOUDFLARE_SITES
            )
        )

        driver.set_page_load_timeout(35)
        driver.get(url)

        time.sleep(1)

        dismiss_cookie_banner(driver)

        selector = SITE_WAIT_SELECTORS.get(site)

        if selector:

            try:

                WebDriverWait(
                    driver,
                    10,
                ).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            selector,
                        )
                    )
                )

            except Exception:
                pass

        elif (
            site in SLOW_AJAX_SITES
            or site in CLOUDFLARE_SITES
        ):

            wait_for_real_content(
                driver,
                timeout=20,
            )

        else:

            time.sleep(3)

        dismiss_cookie_banner(driver)

        driver.execute_script(
            "window.scrollTo("
            "0,document.body.scrollHeight/2"
            ");"
        )

        time.sleep(1)

        html = driver.page_source

        products = extract_products(
            html,
            base_url,
            site,
            query,
        )

        # Kullanıcıya debug görüntüsü gönderilmiyor.
        return (
            site,
            products,
            (
                f"{len(products)} ürün bulundu"
                if products
                else "Ürün bulunamadı"
            ),
            None,
            None,
        )

    except Exception as e:

        return (
            site,
            [],
            f"Bağlantı Hatası ({e.__class__.__name__})",
            None,
            None,
        )

    finally:

        if driver:

            try:
                driver.quit()
            except Exception:
                pass


def search_all_selenium(query):

    results = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3
    ) as executor:

        futures = {
            executor.submit(
                scrape_site,
                site,
                tmpl,
                query,
            ): site
            for site, tmpl
            in SEARCH_URL_TEMPLATES.items()
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            try:
                results.append(
                    future.result()
                )
            except Exception:
                pass

    return results


# ============================================================
# SAYFA AYARLARI
# ============================================================

st.set_page_config(
    page_title="Komponent Fiyat Karşılaştırma",
    page_icon="⚡",
    layout="wide",
)


# ============================================================
# SADE CSS
# ============================================================
# Burada HTML kartları kullanmıyoruz.
# Böylece önceki sürümdeki ham HTML problemi olmaz.
# ============================================================

st.markdown(
    """
<style>

.block-container {
    padding-top: 1rem;
    padding-bottom: 1rem;
    max-width: 1200px;
}

.main-title {
    font-size: 1.7rem;
    font-weight: 800;
    margin-bottom: 0.1rem;
}

.sub-title {
    color: #999;
    font-size: 0.85rem;
    margin-bottom: 1rem;
}

.small-note {
    color: #888;
    font-size: 0.75rem;
}

.price-best {
    color: #35d477;
    font-weight: 800;
}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# BAŞLIK
# ============================================================

st.markdown(
    '<div class="main-title">⚡ Komponent Fiyat Karşılaştırma</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    'Türkiye\'deki elektronik komponent mağazalarını '
    'tek aramada karşılaştır.'
    '</div>',
    unsafe_allow_html=True,
)


# ============================================================
# TABS
# ============================================================

tab_single, tab_basket = st.tabs(
    [
        "🔍 Tek Ürün",
        "🛒 Sepet Karşılaştırma",
    ]
)


# ============================================================
# TEK ÜRÜN
# ============================================================

with tab_single:

    query = st.text_input(
        "Aranacak Komponent",
        placeholder="Örn: L293D, ESP32, MP1584...",
    )

    if st.button(
        "🔎 Fiyatları Getir",
        type="primary",
        use_container_width=True,
        key="single_search",
    ):

        if not query.strip():

            st.warning(
                "Lütfen bir ürün adı girin."
            )

        else:

            start = time.time()

            with st.spinner(
                "🔎 Mağazalar taranıyor..."
            ):

                site_results = (
                    search_all_selenium(
                        query
                    )
                )

            elapsed = time.time() - start

            all_products = [
                p
                for (
                    _site,
                    products,
                    _status,
                    _png,
                    _html,
                ) in site_results
                for p in products
            ]

            # Fiyatı olanları gerçekten sayısal
            # değere göre sırala.
            all_products.sort(
                key=lambda p: (
                    p.price is None,
                    p.price
                    if p.price is not None
                    else float("inf"),
                )
            )

            found_sites = sum(
                1
                for (
                    _site,
                    products,
                    _status,
                    _png,
                    _html,
                ) in site_results
                if products
            )

            priced = [
                p
                for p in all_products
                if p.price is not None
            ]

            cheapest = (
                priced[0]
                if priced
                else None
            )

            # ------------------------------------------------
            # ÖZET
            # ------------------------------------------------

            st.success(
                f"✅ Tarama tamamlandı — "
                f"{len(SEARCH_URL_TEMPLATES)} site · "
                f"{len(all_products)} ürün · "
                f"{elapsed:.0f} saniye"
            )

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "🏪 Mağaza",
                len(SEARCH_URL_TEMPLATES),
            )

            c2.metric(
                "📦 Ürün",
                len(all_products),
            )

            c3.metric(
                "💰 En Ucuz",
                (
                    f"{cheapest.price:,.2f} TL"
                    if cheapest
                    else "—"
                ),
            )

            # ------------------------------------------------
            # MAĞAZA DURUMLARI
            # ------------------------------------------------

            with st.expander(
                "📡 Mağaza Durumları",
                expanded=False,
            ):

                cols = st.columns(2)

                for index, (
                    site,
                    products,
                    _status,
                    _png,
                    _html,
                ) in enumerate(
                    sorted(
                        site_results,
                        key=lambda x: x[0],
                    )
                ):

                    if products:

                        text = (
                            f"🟢 **{site}** — bulundu"
                        )

                    else:

                        text = (
                            f"⚪ **{site}** — bulunamadı"
                        )

                    cols[index % 2].write(text)

            # ------------------------------------------------
            # SONUÇLAR
            # ------------------------------------------------

            if not all_products:

                st.error(
                    "😕 Hiçbir sitede sonuç bulunamadı."
                )

            else:

                st.subheader(
                    "📦 Arama Sonuçları"
                )

                for index, product in enumerate(
                    all_products,
                    start=1,
                ):

                    is_best = (
                        cheapest is not None
                        and product.url
                        == cheapest.url
                    )

                    col1, col2, col3 = st.columns(
                        [0.7, 4.7, 1.8]
                    )

                    with col1:

                        st.write(
                            f"**{index}**"
                        )

                    with col2:

                        st.caption(
                            product.site
                        )

                        st.write(
                            product.name
                        )

                        if is_best:

                            st.success(
                                "🏆 En ucuz"
                            )

                    with col3:

                        if product.price is not None:

                            if is_best:

                                st.markdown(
                                    f'<div class="price-best">'
                                    f'{product.price:,.2f} TL'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

                            else:

                                st.write(
                                    f"**{product.price:,.2f} TL**"
                                )

                        else:

                            st.caption(
                                "Fiyat yok"
                            )

                        st.link_button(
                            "🌐 Siteye Git",
                            product.url,
                            use_container_width=True,
                        )

                    st.divider()


# ============================================================
# SEPET
# ============================================================

with tab_basket:

    st.caption(
        "Her satıra bir ürün yaz. "
        "Sistem mağazaları karşılaştırır."
    )

    basket_text = st.text_area(
        "Sepet",
        placeholder=(
            "esp32\n"
            "hc05\n"
            "1k direnç\n"
            "breadboard"
        ),
        height=150,
    )

    if st.button(
        "🛒 Sepeti Karşılaştır",
        type="primary",
        use_container_width=True,
        key="basket_search",
    ):

        items = [
            line.strip()
            for line
            in basket_text.split("\n")
            if line.strip()
        ]

        if not items:

            st.warning(
                "Lütfen en az bir ürün girin."
            )

        else:

            progress = st.progress(
                0,
                text="Başlıyor...",
            )

            all_results = {}

            for index, item in enumerate(items):

                progress.progress(
                    index / len(items),
                    text=(
                        f"🔎 {index + 1}/"
                        f"{len(items)} — "
                        f"{item} aranıyor..."
                    ),
                )

                all_results[item] = (
                    search_all_selenium(
                        item
                    )
                )

            progress.progress(
                1.0,
                text="✅ Tamamlandı",
            )

            comparison = {
                site: {
                    "total": 0.0,
                    "found_count": 0,
                    "missing": [],
                    "picks": {},
                }
                for site
                in SEARCH_URL_TEMPLATES
            }

            for item in items:

                site_results = all_results.get(
                    item,
                    [],
                )

                found_sites = set()

                for (
                    site,
                    products,
                    _status,
                    _png,
                    _html,
                ) in site_results:

                    priced = [
                        p
                        for p in products
                        if p.price is not None
                    ]

                    if priced:

                        cheapest_item = min(
                            priced,
                            key=lambda p: p.price,
                        )

                        comparison[site]["total"] += (
                            cheapest_item.price
                        )

                        comparison[site][
                            "found_count"
                        ] += 1

                        comparison[site][
                            "picks"
                        ][item] = cheapest_item

                        found_sites.add(site)

                for site in SEARCH_URL_TEMPLATES:

                    if site not in found_sites:

                        comparison[site][
                            "missing"
                        ].append(item)

            ranked = sorted(
                comparison.items(),
                key=lambda x: (
                    len(x[1]["missing"]),
                    (
                        x[1]["total"]
                        if x[1]["found_count"]
                        else float("inf")
                    ),
                ),
            )

            full = [
                x
                for x in ranked
                if not x[1]["missing"]
                and x[1]["found_count"]
            ]

            if full:

                best_site, best_data = full[0]

                st.success(
                    f"🏆 En ucuz tam sepet: "
                    f"**{best_site}** — "
                    f"**{best_data['total']:,.2f} TL**"
                )

            else:

                st.warning(
                    "Sepetin tamamını tek mağazada "
                    "karşılayan mağaza bulunamadı."
                )

            rows = []

            for site, data in ranked:

                if not data["found_count"]:
                    continue

                rows.append(
                    {
                        "Mağaza": site,
                        "Bulunan": (
                            f"{data['found_count']}/"
                            f"{len(items)}"
                        ),
                        "Toplam": (
                            f"{data['total']:,.2f} TL"
                        ),
                        "Eksik": (
                            ", ".join(
                                data["missing"]
                            )
                            if data["missing"]
                            else "—"
                        ),
                    }
                )

            if rows:

                st.subheader(
                    "📊 Sepet Karşılaştırması"
                )

                st.dataframe(
                    pd.DataFrame(rows),
                    hide_index=True,
                    use_container_width=True,
                )

            with st.expander(
                "📋 Ürün Bazında Ayrıntılar",
                expanded=False,
            ):

                for item in items:

                    st.markdown(
                        f"**🔹 {item}**"
                    )

                    detail = []

                    for site, data in ranked:

                        product = data[
                            "picks"
                        ].get(item)

                        if product:

                            detail.append(
                                {
                                    "Mağaza": site,
                                    "Fiyat": (
                                        f"{product.price:,.2f} TL"
                                    ),
                                    "Ürün": product.name,
                                    "Site": product.url,
                                }
                            )

                    if detail:

                        detail.sort(
                            key=lambda x: float(
                                x["Fiyat"]
                                .replace(
                                    " TL",
                                    ""
                                )
                                .replace(
                                    ".",
                                    ""
                                )
                                .replace(
                                    ",",
                                    "."
                                )
                            )
                        )

                        st.dataframe(
                            pd.DataFrame(detail),
                            column_config={
                                "Site":
                                st.column_config.LinkColumn(
                                    "Siteye Git"
                                )
                            },
                            hide_index=True,
                            use_container_width=True,
                        )

                    else:

                        st.caption(
                            "Bu ürün bulunamadı."
                        )


# ============================================================
# ALT BİLGİ
# ============================================================

st.divider()

st.markdown(
    """
<div style="
    text-align:center;
    color:#777;
    font-size:.78rem;
    padding:.4rem;
">
    ⚡ Komponent Fiyat Karşılaştırma<br>
    <strong>Mehmet Özberk</strong><br>
    <span style="font-size:.7rem;">v1.5</span>
</div>
""",
    unsafe_allow_html=True,
)
