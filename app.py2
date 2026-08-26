import os
import subprocess
import streamlit as st

st.title("🔧 Chrome / ChromeDriver Test")

for command in [
    ["which", "chromium"],
    ["which", "chromedriver"],
    ["chromium", "--version"],
    ["chromedriver", "--version"],
]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )

        st.code(
            "$ " + " ".join(command)
            + "\n"
            + result.stdout
            + result.stderr
        )

    except Exception as e:
        st.error(f"{command}: {e}")

st.write("Chromium:")
st.write(os.path.exists("/usr/bin/chromium"))

st.write("ChromeDriver:")
st.write(os.path.exists("/usr/bin/chromedriver"))
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

SLOW_AJAX_SITES = {"Robolink", "Motorobit"}
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

DEBUG_DIR = "debug_snapshots"


@dataclass
class Product:
    site: str
    name: str
    price: float | None
    url: str
    stock: str = "Bilinmiyor"
    quantity: int = 1
    match_score: int = 0


def parse_price(raw: str) -> float | None:
    raw = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def parse_query_quantity(query: str):
    quantity = 1

    patterns = [
        r"\bx\s*(\d+)\s*$",
        r"\*\s*(\d+)\s*$",
        r"\b(\d+)\s*(?:adet|ad)\s*$",
    ]

    clean_query = query.strip()

    for pattern in patterns:
        match = re.search(pattern, clean_query, re.IGNORECASE)
        if match:
            try:
                quantity = max(1, int(match.group(1)))
                clean_query = clean_query[: match.start()].strip()
            except Exception:
                pass
            break

    return clean_query, quantity


def enrich_product(product: Product, query: str, product_name: str):
    query_words = [
        x.lower()
        for x in re.findall(r"[a-zA-Z0-9çğıöşüÇĞİÖŞÜ]+", query)
        if len(x) > 1
    ]

    name_lower = product_name.lower()

    if not query_words:
        product.match_score = 0
        return

    found = sum(1 for word in query_words if word in name_lower)
    product.match_score = int((found / len(query_words)) * 100)


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
                for element in obj.get("itemListElement", []):
                    if isinstance(element, dict):
                        collect(element.get("item", element))

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
        raw = script.string

        if not raw:
            continue

        try:
            data = json.loads(raw)
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
            keyword in name.lower() for keyword in keywords
        ):
            continue

        offers = product.get("offers", {})

        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        if not isinstance(offers, dict):
            offers = {}

        url = product.get("url") or offers.get("url") or ""

        if not url:
            continue

        price = None

        raw_price = offers.get("price")

        if raw_price is not None:
            try:
                price = float(
                    str(raw_price)
                    .replace(".", "")
                    .replace(",", ".")
                )
            except (TypeError, ValueError):
                try:
                    price = float(str(raw_price))
                except Exception:
                    price = None

        if url in seen_urls:
            continue

        seen_urls.add(url)

        results.append(
            Product(
                site=site_name,
                name=name,
                price=price,
                url=url,
            )
        )

    return results


def extract_products(
    html: str,
    base_url: str,
    site_name: str,
    query: str,
) -> list[Product]:

    soup = BeautifulSoup(html, "lxml")

    keywords = [
        keyword.lower()
        for keyword in query.split()
        if len(keyword) > 1
    ]

    jsonld_results = extract_products_jsonld(
        soup,
        site_name,
        keywords,
    )

    if jsonld_results:
        return jsonld_results

    if site_name == "Hepsiburada":
        products = []

        for card in soup.select('li[id^="i"]'):
            name_tag = card.select_one(
                'h3[data-test-id="product-card-name"]'
            )

            price_tag = card.select_one(
                'div[data-test-id="price-current-price"]'
            )

            link_tag = card.find("a", href=True)

            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)

                if keywords and not any(
                    keyword in name.lower()
                    for keyword in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(strip=True)
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

    if site_name == "Trendyol":
        products = []

        for card in soup.select(".p-card-wrppr"):
            name_tag = card.select_one(
                ".prdct-desc-cntnr-name"
            )

            price_tag = (
                card.select_one(".prc-box-dscntd")
                or card.select_one(".prc-box-sllng")
            )

            link_tag = card.find("a", href=True)

            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)

                if keywords and not any(
                    keyword in name.lower()
                    for keyword in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(strip=True)
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

    if site_name == "N11":
        products = []

        for card in soup.select(".column"):
            name_tag = card.select_one(
                "h3.productName"
            )

            price_tag = (
                card.select_one("ins")
                or card.select_one(".newPrice")
            )

            link_tag = card.find("a", href=True)

            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)

                if keywords and not any(
                    keyword in name.lower()
                    for keyword in keywords
                ):
                    continue

                price = parse_price(
                    price_tag.get_text(strip=True)
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

            if name_tag and price_tag and link_tag:
                name = name_tag.get_text(strip=True)

                if keywords and not any(
                    keyword in name.lower()
                    for keyword in keywords
                ):
                    continue

                price_text = price_tag.get_text(
                    strip=True
                )

                if price_fraction:
                    price_text += (
                        ","
                        + price_fraction.get_text(
                            strip=True
                        )
                    )

                price = parse_price(price_text)

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

    def clean_name(raw_text: str) -> str:
        text = raw_text

        for pattern in badge_res:
            text = pattern.sub(" ", text)

        text = PRICE_RE.sub(" ", text)

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip(" -–|")

        return text.strip()

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

        prices = PRICE_RE.findall(full_text)

        if not prices:
            continue

        name = clean_name(full_text)

        if not name or len(name) < 3:
            continue

        if keywords and not any(
            keyword in name.lower()
            for keyword in keywords
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

    link_queue = []

    for anchor in soup.find_all("a"):
        text = anchor.get_text(strip=True)
        href = anchor.get("href", "")

        if (
            not text
            or not href
            or text.lower() in IGNORE_LINK_TEXT
            or href.startswith("#")
            or "javascript:" in href
        ):
            continue

        if keywords and not any(
            keyword in text.lower()
            for keyword in keywords
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

    index = 0

    while index < len(raw_lines):
        current = raw_lines[index]

        if (
            index + 1 < len(raw_lines)
            and NUM_ONLY_RE.match(current)
            and raw_lines[index + 1]
            .strip()
            .upper()
            in ("TL", "₺", "TRY")
        ):
            lines.append(
                f"{current} TL"
            )

            index += 2

        else:
            lines.append(current)
            index += 1

    link_index = 0
    candidate_name = None
    candidate_url = None
    gap_counter = 0

    for line in lines:
        if (
            link_index < len(link_queue)
            and line == link_queue[link_index][0]
        ):
            candidate_name, candidate_url = (
                link_queue[link_index]
            )

            link_index += 1
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


def get_driver(stealth: bool = False):
    options = Options()

    chromium_candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]

    for path in chromium_candidates:
        if os.path.exists(path):
            options.binary_location = path
            break

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-default-apps")
    options.add_argument("--no-first-run")
    options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )
    options.add_argument("--lang=tr-TR")
    options.add_argument("--window-size=1920,1080")

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
        "Chrome/131.0.0.0 Safari/537.36"
    )

    chromedriver = "/usr/bin/chromedriver"

    if not os.path.exists(chromedriver):
        raise RuntimeError(
            "Sistem chromedriver bulunamadı: "
            "/usr/bin/chromedriver. "
            "packages.txt içinde "
            "chromium-driver olduğundan emin olun."
        )

    service = Service(chromedriver)

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
        {get: () => [1, 2, 3, 4, 5]}
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

    window.chrome = { runtime: {} };
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
    js_check = """
    const t = document.body.innerText || '';
    const matches =
        t.match(/\\d[\\d.,]*\\s*(TL|₺)/g) || [];
    return matches.length;
    """

    end_time = time.time() + timeout

    while time.time() < end_time:
        try:
            count = driver.execute_script(
                js_check
            )

            if count and count >= min_matches:
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

    const nodes = document.querySelectorAll(
        'button, a, div[role="button"], span[role="button"]'
    );

    for (const el of nodes) {
        const t = (
            el.innerText || ''
        ).trim().toLowerCase();

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

    const closeEls = document.querySelectorAll(
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
            "TimeoutException" not in status
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
                site in CLOUDFLARE_SITES
            )
        )

        driver.set_page_load_timeout(35)

        driver.get(url)

        time.sleep(1.0)

        dismiss_cookie_banner(driver)

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

        dismiss_cookie_banner(driver)

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

        debug_png = None
        debug_html_snippet = None

        if not products:
            try:
                debug_png = (
                    driver.get_screenshot_as_png()
                )

            except Exception:
                pass

            try:
                body_text = driver.execute_script(
                    "return document.body.innerText || '';"
                )

            except Exception:
                body_text = ""

            price_matches = len(
                re.findall(
                    r"\d[\d.,]*\s*(?:TL|₺)",
                    body_text,
                )
            )

            raw_html_snippet = ""

            body_lines = [
                line.strip()
                for line in body_text.split("\n")
                if line.strip()
            ]

            price_line_re = re.compile(
                r"^\d[\d.,]*\s*(?:TL|₺)$",
                re.IGNORECASE,
            )

            candidate_name_line = None

            for index in range(
                len(body_lines) - 1
            ):
                if (
                    price_line_re.match(
                        body_lines[index + 1]
                    )
                    and len(
                        body_lines[index]
                    ) > 8
                    and not price_line_re.match(
                        body_lines[index]
                    )
                ):
                    candidate_name_line = (
                        body_lines[index]
                    )
                    break

            if candidate_name_line:
                position = html.find(
                    candidate_name_line
                )

                if position == -1:
                    first_word = (
                        candidate_name_line
                        .split(" ")[0]
                    )

                    position = html.find(
                        first_word
                    )

                if position != -1:
                    start = max(
                        0,
                        position - 1000,
                    )

                    end = min(
                        len(html),
                        position + 800,
                    )

                    raw_html_snippet = (
                        html[start:end]
                    )

            if not raw_html_snippet:
                match = re.search(
                    r"\d[\d.,]*\s*(?:TL|₺)",
                    html,
                )

                if match:
                    start = max(
                        0,
                        match.start() - 1200,
                    )

                    end = min(
                        len(html),
                        match.end() + 300,
                    )

                    raw_html_snippet = (
                        html[start:end]
                    )

            debug_html_snippet = (
                f"[TOPLAM HTML UZUNLUĞU: "
                f"{len(html)} karakter]\n"
                f"[GÖRÜNÜR METİNDE FİYAT "
                f"DESENİ SAYISI: "
                f"{price_matches}]\n\n"
                f"--- GÖRÜNÜR SAYFA METNİ "
                f"(ilk 2500 karakter) ---\n"
                f"{body_text[:2500]}\n\n"
                f"--- İLK FİYATIN ETRAFINDAKİ "
                f"HAM HTML ---\n"
                f"{raw_html_snippet}"
            )

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

    except Exception as exc:
        return (
            site,
            [],
            "Bağlantı Hatası / "
            f"Engellendi ({exc.__class__.__name__})",
            None,
            None,
        )

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def search_all_selenium(query: str):
    clean_query, quantity = (
        parse_query_quantity(query)
    )

    results = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3
    ) as executor:

        futures = {
            executor.submit(
                scrape_site,
                site,
                tmpl,
                clean_query,
            ): site
            for site, tmpl
            in SEARCH_URL_TEMPLATES.items()
        }

        for future in concurrent.futures.as_completed(
            futures
        ):
            try:
                (
                    site,
                    products,
                    status,
                    debug_png,
                    debug_html,
                ) = future.result()

                for product in products:
                    product.quantity = quantity

                    enrich_product(
                        product,
                        clean_query,
                        product.name,
                    )

                results.append(
                    (
                        site,
                        products,
                        status,
                        debug_png,
                        debug_html,
                    )
                )

            except Exception:
                pass

    return results


def search_basket(
    items: list[str],
    progress_callback=None,
) -> dict:
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
) -> dict:

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
            all_results.get(item, [])
        )

        found_sites = set()

        for (
            site,
            products,
            status,
            debug_png,
            debug_html,
        ) in site_results:

            priced = [
                product
                for product in products
                if product.price is not None
            ]

            if priced:
                cheapest = min(
                    priced,
                    key=lambda product: product.price,
                )

                comparison[site]["total"] += (
                    cheapest.price
                )

                comparison[site][
                    "found_count"
                ] += 1

                comparison[site]["picks"][
                    item
                ] = cheapest

                found_sites.add(site)

        for site in SEARCH_URL_TEMPLATES:
            if site not in found_sites:
                comparison[site][
                    "missing"
                ].append(item)

    return comparison


st.set_page_config(
    page_title="Komponent Fiyat Arama",
    page_icon="⚡",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main .block-container {
        padding-top: 1.1rem;
        padding-bottom: 1rem;
        max-width: 1400px;
    }

    .result-title {
        font-size: 1.2rem;
        font-weight: 700;
        margin: .4rem 0 .7rem;
    }

    .stock-badge {
        display: inline-block;
        padding: .2rem .6rem;
        border-radius: .5rem;
        font-weight: 700;
        font-size: .84rem;
    }

    .stock-ok {
        background: rgba(46,160,67,.20);
        color: #49d568;
        border: 1px solid
            rgba(73,213,104,.45);
    }

    .stock-no {
        background: rgba(220,53,69,.20);
        color: #ff6878;
        border: 1px solid
            rgba(255,104,120,.45);
    }

    .stock-unknown {
        background: rgba(140,140,140,.16);
        color: #b9b9b9;
        border: 1px solid
            rgba(180,180,180,.25);
    }

    .price-strong {
        font-weight: 800;
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title(
    "⚡ Komponent Fiyat Karşılaştırma"
)

tab_single, tab_basket = st.tabs(
    [
        "🔍 Tek Ürün",
        "🛒 Sepet Karşılaştırma",
    ]
)

with tab_single:
    query = st.text_input(
        "Aranacak Komponent:",
        placeholder=(
            "örn: esp32, direnç 10k, mp1584"
        ),
    )

    if st.button(
        "Fiyatları Getir",
        type="primary",
        use_container_width=True,
        key="single_search",
    ):

        if not query.strip():
            st.warning(
                "Lütfen bir ürün adı girin."
            )

        else:

            with st.spinner(
                "⚡ Mağazalar taranıyor..."
            ):
                site_results = (
                    search_all_selenium(query)
                )

            with st.expander(
                "🔍 Site Tarama Durumları",
                expanded=False,
            ):

                cols = st.columns(3)

                for index, (
                    site,
                    products,
                    status,
                    debug_png,
                    debug_html,
                ) in enumerate(site_results):

                    col = cols[index % 3]

                    if products:
                        col.success(
                            f"**{site}** — bulundu"
                        )
                    else:
                        col.info(
                            f"**{site}** — bulunamadı"
                        )

            all_products = [
                product
                for (
                    site,
                    products,
                    status,
                    debug_png,
                    debug_html,
                ) in site_results
                for product in products
            ]

            if not all_products:
                st.error(
                    "Hiçbir sitede sonuç bulunamadı."
                )

            else:

                all_products.sort(
                    key=lambda product: (
                        product.price is None,
                        (
                            product.price
                            if product.price is not None
                            else float("inf")
                        ),
                    )
                )

                st.markdown(
                    f"""
                    <div class="result-title">
                        🔎 Arama Sonuçları
                        ({len(all_products)})
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.caption(
                    f"🔎 {len(all_products)} sonuç • "
                    f"🟢 {sum(1 for p in all_products if p.stock == 'Var')} stokta • "
                    f"🔴 {sum(1 for p in all_products if p.stock == 'Yok')} stokta yok • "
                    f"⚪ {sum(1 for p in all_products if p.stock == 'Bilinmiyor')} bilinmiyor • "
                    f"💰 {sum(1 for p in all_products if p.price is not None)} fiyat doğrulandı"
                )

                rows_html = []

                for index, product in enumerate(
                    all_products,
                    start=1,
                ):

                    if product.stock == "Var":
                        stock_html = (
                            '<span class="stock-badge '
                            'stock-ok">'
                            '🟢 Var'
                            '</span>'
                        )

                    elif product.stock == "Yok":
                        stock_html = (
                            '<span class="stock-badge '
                            'stock-no">'
                            '🔴 Yok'
                            '</span>'
                        )

                    else:
                        stock_html = (
                            '<span class="stock-badge '
                            'stock-unknown">'
                            '⚪ Bilinmiyor'
                            '</span>'
                        )

                    if product.price is not None:
                        price_html = (
                            '<span class="price-strong">'
                            f"{product.price:,.2f} TL"
                            "</span>"
                        )
                    else:
                        price_html = "—"

                    safe_name = (
                        str(product.name)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )

                    safe_site = (
                        str(product.site)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )

                    safe_url = str(
                        product.url
                    ).replace(
                        '"',
                        "&quot;",
                    )

                    rows_html.append(
                        "<tr>"
                        f"<td>{index}</td>"
                        f"<td><b>{safe_site}</b></td>"
                        f"<td>{safe_name}</td>"
                        f"<td>{stock_html}</td>"
                        f"<td>{price_html}</td>"
                        "<td>"
                        f'<a href="{safe_url}" '
                        'target="_blank" '
                        'style="text-decoration:none;'
                        'font-weight:700;">'
                        "🌐 Siteye Git"
                        "</a>"
                        "</td>"
                        "</tr>"
                    )

                table_html = """
                <div style="overflow-x:auto;">
                    <table style="
                        width:100%;
                        border-collapse:collapse;
                    ">
                        <thead>
                            <tr>
                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">#</th>

                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">Mağaza</th>

                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">Ürün</th>

                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">Stok</th>

                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">Fiyat</th>

                                <th style="
                                    text-align:left;
                                    padding:.55rem;
                                ">Bağlantı</th>
                            </tr>
                        </thead>

                        <tbody>
                """

                table_html += "".join(
                    rows_html
                )

                table_html += """
                        </tbody>
                    </table>
                </div>
                """

                st.markdown(
                    table_html,
                    unsafe_allow_html=True,
                )


st.markdown(
    """
    <div style="
        text-align:center;
        margin-top:1.5rem;
        padding:.7rem 0;
        color:#888;
        font-size:.82rem;
    ">
        ⚡ Komponent Fiyat Karşılaştırma<br>
        <strong>Mehmet Özberk</strong>
    </div>
    """,
    unsafe_allow_html=True,
)


with tab_basket:

    st.caption(
        "Her satıra bir ürün yazın. "
        "Her ürün tüm sitelerde aranıp, "
        "hangi sitenin sepetin tamamını "
        "en ucuza karşıladığı hesaplanır."
    )

    basket_text = st.text_area(
        "Sepetiniz (her satıra bir ürün):",
        placeholder=(
            "esp32\n"
            "hc05\n"
            "1k direnç\n"
            "breadboard"
        ),
        height=150,
    )

    if st.button(
        "Sepeti Karşılaştır",
        type="primary",
        use_container_width=True,
        key="basket_search",
    ):

        items = [
            line.strip()
            for line in basket_text.split("\n")
            if line.strip()
        ]

        if not items:
            st.warning(
                "Lütfen en az bir ürün girin."
            )

        else:

            progress_bar = st.progress(
                0,
                text="Başlıyor...",
            )

            def update_progress(
                index,
                item,
            ):
                progress_bar.progress(
                    index / len(items),
                    text=(
                        f"({index + 1}/"
                        f"{len(items)}) "
                        f"'{item}' tüm sitelerde "
                        "aranıyor..."
                    ),
                )

            all_results = search_basket(
                items,
                progress_callback=update_progress,
            )

            progress_bar.progress(
                1.0,
                text="Tamamlandı!",
            )

            comparison = (
                build_basket_comparison(
                    items,
                    all_results,
                )
            )

            ranked_sites = sorted(
                comparison.items(),
                key=lambda item: (
                    len(item[1]["missing"]),
                    (
                        item[1]["total"]
                        if item[1]["found_count"] > 0
                        else float("inf")
                    ),
                ),
            )

            full_coverage = [
                (
                    site,
                    data,
                )
                for site, data in ranked_sites
                if not data["missing"]
                and data["found_count"] > 0
            ]

            if full_coverage:

                best_site, best_data = (
                    full_coverage[0]
                )

                st.success(
                    f"🏆 **En ucuz tam sepet: "
                    f"{best_site}** — "
                    f"Toplam: "
                    f"**{best_data['total']:,.2f} TL** "
                    f"({best_data['found_count']}/"
                    f"{len(items)} ürün bulundu)"
                )

            else:

                st.warning(
                    "Sepetin tamamını tek başına "
                    "karşılayan bir site bulunamadı. "
                    "Aşağıda en iyi kısmi eşleşmeler "
                    "listeleniyor."
                )

            summary_rows = []

            for site, data in ranked_sites:

                if data["found_count"] == 0:
                    continue

                summary_rows.append(
                    {
                        "Site": site,
                        "Bulunan": (
                            f"{data['found_count']}/"
                            f"{len(items)}"
                        ),
                        "Toplam Fiyat": (
                            f"{data['total']:,.2f} TL"
                        ),
                        "Eksik Ürünler": (
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
                    "📊 Site Karşılaştırması"
                )

                st.dataframe(
                    pd.DataFrame(summary_rows),
                    hide_index=True,
                    use_container_width=True,
                )

            else:

                st.error(
                    "Hiçbir sitede sepetteki "
                    "ürünlerden herhangi biri "
                    "bulunamadı."
                )

            with st.expander(
                "📋 Ürün Bazında Detay "
                "(hangi site hangi fiyatı verdi)",
                expanded=False,
            ):

                for item in items:

                    st.markdown(
                        f"**{item}**"
                    )

                    item_rows = []

                    for site, data in ranked_sites:

                        pick = data["picks"].get(
                            item
                        )

                        if pick:

                            item_rows.append(
                                {
                                    "Site": site,
                                    "Fiyat": (
                                        f"{pick.price:,.2f} TL"
                                    ),
                                    "Ürün Adı": pick.name,
                                    "Link": pick.url,
                                }
                            )

                    if item_rows:

                        item_rows.sort(
                            key=lambda row: (
                                float(
                                    row["Fiyat"]
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
                                "Link": (
                                    st.column_config
                                    .LinkColumn(
                                        "Satın Al"
                                    )
                                )
                            },
                            hide_index=True,
                            use_container_width=True,
                        )

                    else:

                        st.caption(
                            "Hiçbir sitede bulunamadı."
                        )
