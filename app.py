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
# MODEL
# ============================================================

@dataclass
class Product:
    site: str
    name: str
    price: float | None
    url: str


# ============================================================
# FİYAT
# ============================================================

def parse_price(raw: str) -> float | None:
    raw = raw.strip().replace(".", "").replace(",", ".")

    try:
        return float(raw)
    except ValueError:
        return None


# ============================================================
# JSON-LD ÜRÜN ÇEKME
# ============================================================

def extract_products_jsonld(
    soup: BeautifulSoup,
    site_name: str,
    keywords: list[str],
) -> list[Product]:

    found = []

    def collect(obj):
        if isinstance(obj, dict):

            obj_type = obj.get("@type")

            if obj_type == "Product":
                found.append(obj)

            elif obj_type == "ItemList":

                for element in obj.get(
                    "itemListElement",
                    [],
                ):
                    if isinstance(element, dict):
                        collect(
                            element.get(
                                "item",
                                element,
                            )
                        )

            else:

                for value in obj.values():
                    collect(value)

        elif isinstance(obj, list):

            for element in obj:
                collect(element)

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

        name = (
            product.get("name")
            or ""
        ).strip()

        if not name:
            continue

        if keywords and not any(
            keyword in name.lower()
            for keyword in keywords
        ):
            continue

        offers = product.get(
            "offers",
            {},
        )

        if isinstance(offers, list):
            offers = (
                offers[0]
                if offers
                else {}
            )

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

        raw_price = offers.get(
            "price"
        )

        if raw_price is not None:

            try:
                price = float(
                    str(raw_price)
                    .replace(",", ".")
                )
            except (
                TypeError,
                ValueError,
            ):
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
# ÜRÜN AYRIŞTIRICI
# ============================================================

def extract_products(
    html: str,
    base_url: str,
    site_name: str,
    query: str,
) -> list[Product]:

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    keywords = [
        k.lower()
        for k in query.split()
        if len(k) > 1
    ]

    # Önce JSON-LD
    jsonld_results = extract_products_jsonld(
        soup,
        site_name,
        keywords,
    )

    if jsonld_results:
        return jsonld_results

    # --------------------------------------------------------
    # Hepsiburada
    # --------------------------------------------------------

    if site_name == "Hepsiburada":

        products = []

        for card in soup.select(
            'li[id^="i"]'
        ):

            name_tag = card.select_one(
                'h3[data-test-id="product-card-name"]'
            )

            price_tag = card.select_one(
                'div[data-test-id="price-current-price"]'
            )

            link_tag = card.find(
                "a",
                href=True,
            )

            if (
                name_tag
                and price_tag
                and link_tag
            ):

                name = name_tag.get_text(
                    strip=True
                )

                if keywords and not any(
                    k in name.lower()
                    for k in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(
                        strip=True
                    )
                    .replace("TL", "")
                    .strip()
                )

                href = link_tag["href"]

                full_url = (
                    href
                    if href.startswith("http")
                    else base_url.rstrip("/")
                    + "/"
                    + href.lstrip("/")
                )

                products.append(
                    Product(
                        site_name,
                        name,
                        price,
                        full_url,
                    )
                )

        return products

    # --------------------------------------------------------
    # Trendyol
    # --------------------------------------------------------

    if site_name == "Trendyol":

        products = []

        for card in soup.select(
            ".p-card-wrppr"
        ):

            name_tag = card.select_one(
                ".prdct-desc-cntnr-name"
            )

            price_tag = (
                card.select_one(
                    ".prc-box-dscntd"
                )
                or card.select_one(
                    ".prc-box-sllng"
                )
            )

            link_tag = card.find(
                "a",
                href=True,
            )

            if (
                name_tag
                and price_tag
                and link_tag
            ):

                name = name_tag.get_text(
                    strip=True
                )

                if keywords and not any(
                    k in name.lower()
                    for k in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(
                        strip=True
                    )
                    .replace("TL", "")
                    .strip()
                )

                href = link_tag["href"]

                full_url = (
                    href
                    if href.startswith("http")
                    else base_url.rstrip("/")
                    + "/"
                    + href.lstrip("/")
                )

                products.append(
                    Product(
                        site_name,
                        name,
                        price,
                        full_url,
                    )
                )

        return products

    # --------------------------------------------------------
    # N11
    # --------------------------------------------------------

    if site_name == "N11":

        products = []

        for card in soup.select(
            ".column"
        ):

            name_tag = card.select_one(
                "h3.productName"
            )

            price_tag = (
                card.select_one("ins")
                or card.select_one(
                    ".newPrice"
                )
            )

            link_tag = card.find(
                "a",
                href=True,
            )

            if (
                name_tag
                and price_tag
                and link_tag
            ):

                name = name_tag.get_text(
                    strip=True
                )

                if keywords and not any(
                    k in name.lower()
                    for k in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(
                        strip=True
                    )
                    .replace("TL", "")
                    .strip()
                )

                href = link_tag["href"]

                full_url = (
                    href
                    if href.startswith("http")
                    else base_url.rstrip("/")
                    + "/"
                    + href.lstrip("/")
                )

                products.append(
                    Product(
                        site_name,
                        name,
                        price,
                        full_url,
                    )
                )

        return products

    # --------------------------------------------------------
    # Amazon
    # --------------------------------------------------------

    if site_name == "Amazon TR":

        products = []

        for card in soup.select(
            'div[data-component-type="s-search-result"]'
        ):

            name_tag = card.select_one(
                "h2 a span"
            )

            price_tag = card.select_one(
                "span.a-price-whole"
            )

            price_fraction = card.select_one(
                "span.a-price-fraction"
            )

            link_tag = card.select_one(
                "h2 a"
            )

            if (
                name_tag
                and price_tag
                and link_tag
            ):

                name = name_tag.get_text(
                    strip=True
                )

                if keywords and not any(
                    k in name.lower()
                    for k in keywords
                ):
                    continue

                price_text = (
                    price_tag.get_text(
                        strip=True
                    )
                )

                if price_fraction:
                    price_text += (
                        ","
                        + price_fraction.get_text(
                            strip=True
                        )
                    )

                price = parse_price(
                    price_text
                )

                href = link_tag["href"]

                full_url = (
                    href
                    if href.startswith("http")
                    else base_url.rstrip("/")
                    + "/"
                    + href.lstrip("/")
                )

                products.append(
                    Product(
                        site_name,
                        name,
                        price,
                        full_url,
                    )
                )

        return products

    # --------------------------------------------------------
    # Genel komponent siteleri
    # --------------------------------------------------------

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
        re.compile(
            r"peşin fiyatına \d+ taksit",
            re.IGNORECASE,
        ),
        re.compile(
            r"\btaksit\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"ücretsiz kargo",
            re.IGNORECASE,
        ),
        re.compile(
            r"stoktan teslim",
            re.IGNORECASE,
        ),
        re.compile(
            r"\byeni\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"sepete ekle",
            re.IGNORECASE,
        ),
        re.compile(
            r"favorilere ekle",
            re.IGNORECASE,
        ),
        re.compile(
            r"i̇ncele|incele",
            re.IGNORECASE,
        ),
        re.compile(
            r"\(\s*\d+\s*\)",
        ),
        re.compile(
            r"%\s*\d+",
        ),
        re.compile(
            r"\d+\s*yorum",
            re.IGNORECASE,
        ),
        re.compile(
            r"stokta\s*yok",
            re.IGNORECASE,
        ),
    ]

    def clean_name(
        raw_text: str,
    ) -> str:

        text = raw_text

        for pattern in badge_res:
            text = pattern.sub(
                " ",
                text,
            )

        text = PRICE_RE.sub(
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip(" -–|")

        return text.strip()

    # --------------------------------------------------------
    # Yöntem 1
    # --------------------------------------------------------

    for anchor in soup.find_all(
        "a",
        href=True,
    ):

        href = anchor["href"]

        if (
            not href
            or href.startswith("#")
            or "javascript:" in href
        ):
            continue

        full_text = anchor.get_text(
            separator=" ",
            strip=True,
        )

        if not full_text:
            continue

        prices = PRICE_RE.findall(
            full_text
        )

        if not prices:
            continue

        name = clean_name(
            full_text
        )

        if (
            not name
            or len(name) < 3
        ):
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

        price = parse_price(
            prices[-1]
        )

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
    # Yöntem 2
    # --------------------------------------------------------

    link_queue = []

    for anchor in soup.find_all("a"):

        text = anchor.get_text(
            strip=True
        )

        href = anchor.get(
            "href",
            "",
        )

        if (
            not text
            or not href
            or text.lower()
            in IGNORE_LINK_TEXT
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
            (
                text,
                full_url,
            )
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

        current = raw_lines[i]

        if (
            i + 1 < len(raw_lines)
            and NUM_ONLY_RE.match(
                current
            )
            and raw_lines[i + 1]
            .strip()
            .upper()
            in (
                "TL",
                "₺",
                "TRY",
            )
        ):

            lines.append(
                f"{current} TL"
            )

            i += 2

        else:

            lines.append(
                current
            )

            i += 1

    link_idx = 0
    candidate_name = None
    candidate_url = None
    gap_counter = 0

    for line in lines:

        if (
            link_idx < len(link_queue)
            and line
            == link_queue[
                link_idx
            ][0]
        ):

            (
                candidate_name,
                candidate_url,
            ) = link_queue[
                link_idx
            ]

            link_idx += 1
            gap_counter = 0

            continue

        price_match = PRICE_RE.search(
            line
        )

        if (
            price_match
            and candidate_name
        ):

            if (
                candidate_url
                not in seen_urls
            ):

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

                seen_urls.add(
                    candidate_url
                )

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


def get_driver(
    stealth: bool = False,
):

    global _virtual_display

    options = Options()

    display_ready = False

    if stealth:

        if _virtual_display is None:

            try:

                from pyvirtualdisplay import Display

                _virtual_display = Display(
                    visible=0,
                    size=(
                        1920,
                        1080,
                    ),
                )

                _virtual_display.start()

                display_ready = True

            except Exception:

                _virtual_display = False

        elif _virtual_display is not False:

            display_ready = True

    if not display_ready:

        options.add_argument(
            "--headless=new"
        )

    options.add_argument(
        "--no-sandbox"
    )

    options.add_argument(
        "--disable-dev-shm-usage"
    )

    options.add_argument(
        "--disable-gpu"
    )

    options.add_argument(
        "--window-size=1920,1080"
    )

    options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )

    options.add_argument(
        "--lang=tr-TR"
    )

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

    binary_candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]

    for path in binary_candidates:

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
            'tr-TR',
            'tr',
            'en-US',
            'en'
        ]}
    );

    window.chrome = {
        runtime: {}
    };
    """

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": stealth_js
        },
    )

    return driver


def wait_for_real_content(
    driver,
    timeout=15,
    min_matches=2,
):

    js_check = """
    const t =
        document.body.innerText || '';

    const matches =
        t.match(
            /\\d[\\d.,]*\\s*(TL|₺)/g
        ) || [];

    return matches.length;
    """

    end_time = (
        time.time()
        + timeout
    )

    while time.time() < end_time:

        try:

            count = driver.execute_script(
                js_check
            )

            if (
                count
                and count >= min_matches
            ):
                return True

        except Exception:
            pass

        time.sleep(0.5)

    return False


def dismiss_cookie_banner(
    driver,
):

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
            'button, a, '
            + 'div[role="button"], '
            + 'span[role="button"]'
        );

    for (const el of nodes) {

        const t =
            (el.innerText || '')
            .trim()
            .toLowerCase();

        if (
            t &&
            texts.some(
                x => t.includes(x)
            ) &&
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
            + '.close,'
            + '.modal-close,'
            + '.popup-close'
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
        ).send_keys(
            Keys.ESCAPE
        )

    except Exception:
        pass


def scrape_site(
    site: str,
    url_tmpl: str,
    query: str,
):

    for attempt in range(2):

        result = _scrape_site_once(
            site,
            url_tmpl,
            query,
        )

        status = result[2]

        if (
            "TimeoutException"
            not in status
            or attempt == 1
        ):

            return result

    return result


def _scrape_site_once(
    site: str,
    url_tmpl: str,
    query: str,
):

    encoded_query = urllib.parse.quote_plus(
        query
    )

    url = url_tmpl.format(
        query=encoded_query
    )

    base_url = (
        "https://"
        + url.split(
            "://",
            1,
        )[1].split(
            "/",
            1,
        )[0]
    )

    driver = None

    try:

        driver = get_driver(
            stealth=(
                site
                in CLOUDFLARE_SITES
            )
        )

        driver.set_page_load_timeout(
            35
        )

        driver.get(url)

        time.sleep(1.0)

        dismiss_cookie_banner(
            driver
        )

        time.sleep(0.5)

        selector = SITE_WAIT_SELECTORS.get(
            site
        )

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

            time.sleep(3.0)

        dismiss_cookie_banner(
            driver
        )

        driver.execute_script(
            "window.scrollTo("
            "0, document.body.scrollHeight/2"
            ");"
        )

        time.sleep(1.0)

        html = driver.page_source

        products = extract_products(
            html,
            base_url,
            site,
            query,
        )

        # Debug verisi artık kullanıcıya gösterilmiyor.
        debug_png = None
        debug_html_snippet = None

        status = (
            f"{len(products)} ürün bulundu"
            if products
            else "Ürün bulunamadı"
        )

        return (
            site,
            products,
            status,
            debug_png,
            debug_html_snippet,
        )

    except Exception as e:

        return (
            site,
            [],
            "Bağlantı Hatası / "
            f"Engellendi ({e.__class__.__name__})",
            None,
            None,
        )

    finally:

        if driver:

            try:
                driver.quit()
            except Exception:
                pass


# ============================================================
# ARAMA
# ============================================================

def search_all_selenium(
    query: str,
):

    results = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3
    ) as executor:

        futures = {
            executor.submit(
                scrape_site,
                site,
                template,
                query,
            ): site
            for site, template
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


def search_basket(
    items: list[str],
    progress_callback=None,
):

    all_results = {}

    for index, item in enumerate(items):

        if progress_callback:

            progress_callback(
                index,
                item,
            )

        all_results[item] = (
            search_all_selenium(item)
        )

    return all_results


def build_basket_comparison(
    items: list[str],
    all_results: dict,
):

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

        site_results = (
            all_results.get(
                item,
                [],
            )
        )

        found_sites = set()

        for (
            site,
            products,
            status,
            _debug_png,
            _debug_html,
        ) in site_results:

            priced = [
                p
                for p in products
                if p.price is not None
            ]

            if priced:

                cheapest = min(
                    priced,
                    key=lambda p: p.price,
                )

                comparison[site][
                    "total"
                ] += cheapest.price

                comparison[site][
                    "found_count"
                ] += 1

                comparison[site][
                    "picks"
                ][item] = cheapest

                found_sites.add(site)

        for site in SEARCH_URL_TEMPLATES:

            if site not in found_sites:

                comparison[site][
                    "missing"
                ].append(item)

    return comparison


# ============================================================
# ARAYÜZ - v1.5
# ============================================================

st.set_page_config(
    page_title="Komponent Fiyat Karşılaştırma",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>

.block-container {
    padding-top: 1.2rem !important;
    padding-bottom: 1rem !important;
    max-width: 1400px !important;
}

/* Ana başlık */

.hero {
    padding: 1.2rem 1.3rem;
    border-radius: 18px;
    margin-bottom: 1rem;
    background:
        linear-gradient(
            135deg,
            rgba(30,35,50,.95),
            rgba(20,24,35,.95)
        );
    border: 1px solid rgba(255,255,255,.08);
    box-shadow:
        0 8px 30px rgba(0,0,0,.18);
}

.hero-title {
    font-size: 1.8rem;
    font-weight: 800;
    margin-bottom: .2rem;
}

.hero-subtitle {
    color: #9da5b4;
    font-size: .92rem;
}

/* İstatistik kartları */

.stat-card {
    padding: .85rem .9rem;
    border-radius: 14px;
    background: rgba(255,255,255,.045);
    border: 1px solid rgba(255,255,255,.07);
    text-align: center;
    min-height: 82px;
}

.stat-number {
    font-size: 1.35rem;
    font-weight: 800;
}

.stat-label {
    font-size: .75rem;
    color: #9da5b4;
    margin-top: .15rem;
}

/* Sonuç alanı */

.result-card {
    padding: .9rem 1rem;
    border-radius: 14px;
    margin: .35rem 0;
    background: rgba(255,255,255,.035);
    border: 1px solid rgba(255,255,255,.06);
}

.cheapest-card {
    border: 1px solid rgba(40,200,110,.35);
    background: rgba(40,200,110,.08);
}

/* Durumlar */

.site-found {
    display: inline-block;
    padding: .3rem .65rem;
    border-radius: 999px;
    background: rgba(40,200,110,.13);
    color: #51dc82;
    font-size: .8rem;
    margin: .15rem;
}

.site-missing {
    display: inline-block;
    padding: .3rem .65rem;
    border-radius: 999px;
    background: rgba(150,150,150,.10);
    color: #9ca3af;
    font-size: .8rem;
    margin: .15rem;
}

/* Fiyat */

.price {
    font-weight: 850;
    font-size: 1rem;
}

.best-price {
    color: #4ade80;
}

/* Mobil */

@media (max-width: 700px) {

    .hero-title {
        font-size: 1.45rem;
    }

    .hero {
        padding: 1rem;
    }

    .stat-card {
        min-height: 72px;
        padding: .65rem;
    }

    .stat-number {
        font-size: 1.1rem;
    }

}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# BAŞLIK
# ============================================================

st.markdown(
    """
<div class="hero">

    <div class="hero-title">
        ⚡ Komponent Fiyat Karşılaştırma
    </div>

    <div class="hero-subtitle">
        Türkiye'deki elektronik komponent
        mağazalarını tek aramada karşılaştır.
    </div>

</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# SEKME
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
        placeholder=(
            "Örn: L293D, ESP32, MP1584, HC-05..."
        ),
        label_visibility="visible",
    )

    search_button = st.button(
        "🔎 Fiyatları Getir",
        type="primary",
        use_container_width=True,
        key="single_search",
    )

    if search_button:

        if not query.strip():

            st.warning(
                "Lütfen bir ürün adı girin."
            )

        else:

            start_time = time.time()

            with st.status(
                "🔎 Mağazalar taranıyor...",
                expanded=True,
            ) as status_box:

                st.write(
                    "🌐 Mağazalara bağlanılıyor..."
                )

                site_results = (
                    search_all_selenium(
                        query
                    )
                )

                elapsed = (
                    time.time()
                    - start_time
                )

                status_box.update(
                    label=(
                        f"✅ Tarama tamamlandı — "
                        f"{len(SEARCH_URL_TEMPLATES)} site · "
                        f"{elapsed:.0f} saniye"
                    ),
                    state="complete",
                    expanded=False,
                )

            all_products = [
                product
                for (
                    _site,
                    products,
                    _status,
                    _debug_png,
                    _debug_html,
                ) in site_results
                for product in products
            ]

            # Fiyatı olanlar önce, ucuzdan pahalıya
            all_products.sort(
                key=lambda p: (
                    p.price is None,
                    (
                        p.price
                        if p.price is not None
                        else float("inf")
                    ),
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

            priced_products = [
                p
                for p in all_products
                if p.price is not None
            ]

            cheapest = (
                priced_products[0]
                if priced_products
                else None
            )

            # =================================================
            # İSTATİSTİKLER
            # =================================================

            c1, c2, c3, c4 = st.columns(4)

            with c1:

                st.markdown(
                    f"""
                    <div class="stat-card">
                        <div class="stat-number">
                            {len(SEARCH_URL_TEMPLATES)}
                        </div>
                        <div class="stat-label">
                            🏪 Mağaza
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with c2:

                st.markdown(
                    f"""
                    <div class="stat-card">
                        <div class="stat-number">
                            {found_sites}
                        </div>
                        <div class="stat-label">
                            🔎 Sonuç Bulunan
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with c3:

                st.markdown(
                    f"""
                    <div class="stat-card">
                        <div class="stat-number">
                            {len(all_products)}
                        </div>
                        <div class="stat-label">
                            📦 Ürün
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            with c4:

                cheapest_text = (
                    f"{cheapest.price:,.2f} TL"
                    if cheapest
                    and cheapest.price is not None
                    else "—"
                )

                st.markdown(
                    f"""
                    <div class="stat-card">
                        <div class="stat-number">
                            {cheapest_text}
                        </div>
                        <div class="stat-label">
                            💰 En Ucuz
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.write("")

            # =================================================
            # SİTE DURUMLARI
            # =================================================

            with st.expander(
                "📡 Mağaza Durumları",
                expanded=False,
            ):

                status_html = ""

                for (
                    site,
                    products,
                    _status,
                    _debug_png,
                    _debug_html,
                ) in sorted(
                    site_results,
                    key=lambda x: x[0],
                ):

                    if products:

                        status_html += (
                            '<span class="site-found">'
                            f"🟢 {site}"
                            "</span>"
                        )

                    else:

                        status_html += (
                            '<span class="site-missing">'
                            f"⚪ {site}"
                            "</span>"
                        )

                st.markdown(
                    status_html,
                    unsafe_allow_html=True,
                )

            # =================================================
            # SONUÇ YOK
            # =================================================

            if not all_products:

                st.error(
                    "😕 Hiçbir mağazada sonuç bulunamadı."
                )

            else:

                st.markdown(
                    f"""
                    <div style="
                        margin-top:1rem;
                        margin-bottom:.6rem;
                        font-weight:800;
                        font-size:1.1rem;
                    ">
                        📦 Arama Sonuçları
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # =================================================
                # ÜRÜNLER
                # =================================================

                for index, product in enumerate(
                    all_products,
                    start=1,
                ):

                    is_cheapest = (
                        cheapest is not None
                        and product.url
                        == cheapest.url
                    )

                    card_class = (
                        "result-card cheapest-card"
                        if is_cheapest
                        else "result-card"
                    )

                    if product.price is not None:

                        price_text = (
                            f"{product.price:,.2f} TL"
                        )

                    else:

                        price_text = (
                            "Fiyat alınamadı"
                        )

                    badge = (
                        " 🏆 EN UCUZ"
                        if is_cheapest
                        else ""
                    )

                    safe_name = (
                        str(product.name)
                        .replace(
                            "<",
                            "&lt;",
                        )
                        .replace(
                            ">",
                            "&gt;",
                        )
                    )

                    st.markdown(
                        f"""
                        <div class="{card_class}">

                            <div style="
                                display:flex;
                                justify-content:
                                    space-between;
                                align-items:center;
                                gap:.8rem;
                            ">

                                <div style="
                                    flex:1;
                                    min-width:0;
                                ">

                                    <div style="
                                        color:#9ca3af;
                                        font-size:.75rem;
                                        margin-bottom:.2rem;
                                    ">
                                        #{index}
                                        ·
                                        {product.site}
                                    </div>

                                    <div style="
                                        font-weight:700;
                                        line-height:1.35;
                                    ">
                                        {safe_name}
                                    </div>

                                </div>

                                <div style="
                                    text-align:right;
                                    white-space:nowrap;
                                ">

                                    <div class="
                                        price
                                        {'best-price'
                                        if is_cheapest
                                        else ''}
                                    ">
                                        {price_text}
                                    </div>

                                    <div style="
                                        font-size:.72rem;
                                        color:#4ade80;
                                    ">
                                        {badge}
                                    </div>

                                </div>

                            </div>

                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Siteye Git butonu
                    st.link_button(
                        f"🌐 {product.site} — Siteye Git",
                        product.url,
                        use_container_width=True,
                    )


# ============================================================
# SEPET
# ============================================================

with tab_basket:

    st.markdown(
        """
        <div style="
            margin-bottom:.6rem;
            color:#a0a7b4;
        ">
            Her satıra bir ürün yaz.
            Sistem tüm mağazaları karşılaştırıp
            en uygun sepeti bulsun.
        </div>
        """,
        unsafe_allow_html=True,
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
        label_visibility="collapsed",
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

            progress_bar = st.progress(
                0,
                text="Hazırlanıyor...",
            )

            def update_progress(
                idx,
                item,
            ):

                progress_bar.progress(
                    idx / len(items),
                    text=(
                        f"🔎 {idx + 1}/"
                        f"{len(items)} — "
                        f"{item} aranıyor..."
                    ),
                )

            all_results = search_basket(
                items,
                progress_callback=(
                    update_progress
                ),
            )

            progress_bar.progress(
                1.0,
                text="✅ Tamamlandı",
            )

            comparison = (
                build_basket_comparison(
                    items,
                    all_results,
                )
            )

            ranked_sites = sorted(
                comparison.items(),
                key=lambda kv: (
                    len(
                        kv[1]["missing"]
                    ),
                    (
                        kv[1]["total"]
                        if kv[1]["found_count"]
                        > 0
                        else float("inf")
                    ),
                ),
            )

            full_coverage = [
                (site, data)
                for site, data
                in ranked_sites
                if not data["missing"]
                and data["found_count"] > 0
            ]

            if full_coverage:

                best_site, best_data = (
                    full_coverage[0]
                )

                st.success(
                    f"🏆 En ucuz tam sepet: "
                    f"**{best_site}** — "
                    f"**{best_data['total']:,.2f} TL**"
                )

            else:

                st.warning(
                    "Sepetin tamamını tek mağazada "
                    "karşılayan bir mağaza bulunamadı."
                )

            summary_rows = []

            for site, data in ranked_sites:

                if data["found_count"] == 0:
                    continue

                summary_rows.append(
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

            if summary_rows:

                st.subheader(
                    "📊 Sepet Karşılaştırması"
                )

                st.dataframe(
                    pd.DataFrame(
                        summary_rows
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

            with st.expander(
                "📋 Ürün Bazında Ayrıntılar",
                expanded=False,
            ):

                for item in items:

                    st.markdown(
                        f"### 🔹 {item}"
                    )

                    item_rows = []

                    for (
                        site,
                        data,
                    ) in ranked_sites:

                        pick = data[
                            "picks"
                        ].get(item)

                        if pick:

                            item_rows.append(
                                {
                                    "Mağaza": site,
                                    "Fiyat": (
                                        f"{pick.price:,.2f} TL"
                                    ),
                                    "Ürün": pick.name,
                                    "Site": pick.url,
                                }
                            )

                    if item_rows:

                        item_rows.sort(
                            key=lambda row: (
                                float(
                                    row[
                                        "Fiyat"
                                    ]
                                    .replace(
                                        " TL",
                                        "",
                                    )
                                    .replace(
                                        ".",
                                        "",
                                    )
                                    .replace(
                                        ",",
                                        ".",
                                    )
                                )
                            )
                        )

                        st.dataframe(
                            pd.DataFrame(
                                item_rows
                            ),
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
                            "Bu ürün hiçbir mağazada bulunamadı."
                        )


# ============================================================
# ALT BİLGİ
# ============================================================

st.markdown(
    """
    <div style="
        text-align:center;
        margin-top:2rem;
        padding:1rem 0 .5rem;
        color:#777;
        font-size:.78rem;
        border-top:1px solid rgba(255,255,255,.06);
    ">

        ⚡ Komponent Fiyat Karşılaştırma

        <br>

        <strong style="color:#aaa;">
            Mehmet Özberk
        </strong>

        <br>

        <span style="font-size:.7rem;">
            v1.5
        </span>

    </div>
    """,
    unsafe_allow_html=True,
)
