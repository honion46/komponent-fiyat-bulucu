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
            r"\s*\d+\s*",
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
    options.add_argument
