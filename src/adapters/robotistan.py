import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from bs4 import BeautifulSoup
from curl_cffi import requests

from .base import SellerAdapter
from src.core.models import ProductResult


class RobotistanAdapter(SellerAdapter):
    name = "Robotistan"

    # Robotistan'ın arama endpoint'i
    SEARCH_URL = "https://www.robotistan.com/search?text={}"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.robotistan.com/",
    }

    BASE_URL = "https://www.robotistan.com"

    # ---------------------------------------------------------
    # HTTP
    # ---------------------------------------------------------

    def _get(self, url):

        return requests.get(
            url,
            headers=self.HEADERS,
            impersonate="chrome",
            timeout=30,
            allow_redirects=True,
        )

    # ---------------------------------------------------------
    # PRICE
    # ---------------------------------------------------------

    def _parse_price(self, value):

        if value is None:
            return None

        value = str(value).strip()

        value = re.sub(
            r"[^0-9,.\-]",
            "",
            value,
        )

        if not value:
            return None

        # 1.234,56
        if "," in value and "." in value:

            if value.rfind(",") > value.rfind("."):
                value = value.replace(".", "")
                value = value.replace(",", ".")

            else:
                value = value.replace(",", "")

        # 1234,56
        elif "," in value:

            value = value.replace(",", ".")

        try:
            return Decimal(value)

        except InvalidOperation:
            return None

    def _find_price(self, soup):

        # Önce JSON-LD
        for script in soup.find_all(
            "script",
            type="application/ld+json",
        ):

            raw = script.get_text(
                " ",
                strip=True,
            )

            patterns = [
                r'"price"\s*:\s*"([^"]+)"',
                r'"price"\s*:\s*([0-9.,]+)',
            ]

            for pattern in patterns:

                match = re.search(
                    pattern,
                    raw,
                    re.I,
                )

                if match:

                    price = self._parse_price(
                        match.group(1)
                    )

                    if price is not None:
                        return price

        # Sayfa içindeki bilinen fiyat sınıfları
        selectors = [
            ".product-price",
            ".current-price",
            ".sale-price",
            ".price",
            "[itemprop='price']",
            "[data-price]",
        ]

        for selector in selectors:

            for element in soup.select(selector):

                value = (
                    element.get("content")
                    or element.get("data-price")
                    or element.get_text(
                        " ",
                        strip=True,
                    )
                )

                price = self._parse_price(value)

                if price is not None:
                    return price

        # Son çare: KDV dahil fiyat
        text = soup.get_text(
            " ",
            strip=True,
        )

        patterns = [
            r"KDV\s+Dahil(?:\s+Fiyat)?\s*:?\s*([\d.,]+)\s*TL",
            r"KDV\s+Dahil\s*([\d.,]+)\s*TL",
            r"([\d.,]+)\s*TL",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.I,
            )

            if match:

                price = self._parse_price(
                    match.group(1)
                )

                if price is not None:
                    return price

        return None

    # ---------------------------------------------------------
    # PRODUCT LINKS
    # ---------------------------------------------------------

    def _find_product_links(
        self,
        soup,
        query,
    ):

        links = []

        search_terms = []

        if query.mpn:
            search_terms.append(
                query.mpn.lower()
            )

        if query.raw:
            search_terms.append(
                query.raw.lower()
            )

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = a.get("href", "").strip()

            if not href:
                continue

            if href.startswith("/"):

                href = (
                    self.BASE_URL
                    + href
                )

            if not href.startswith(
                self.BASE_URL
            ):
                continue

            title = a.get_text(
                " ",
                strip=True,
            )

            combined = (
                f"{title} {href}"
            ).lower()

            # MPN veya sorgu bulunuyorsa aday
            matched = any(
                term in combined
                for term in search_terms
                if term
            )

            if not matched:
                continue

            # Arama / kategori / filtre sayfalarını ele
            blocked = [
                "/arama",
                "/search",
                "/kategori",
                "/marka",
                "?",
            ]

            if any(
                item in href.lower()
                for item in blocked
            ):
                continue

            if href not in links:
                links.append(href)

        return links[:10]

    # ---------------------------------------------------------
    # PRODUCT PARSER
    # ---------------------------------------------------------

    def _parse_product(
        self,
        url,
        query,
    ):

        response = self._get(url)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "lxml",
        )

        text = soup.get_text(
            " ",
            strip=True,
        )

        # -----------------------------------------------------
        # NAME
        # -----------------------------------------------------

        name = ""

        h1 = soup.find("h1")

        if h1:
            name = h1.get_text(
                " ",
                strip=True,
            )

        if not name:

            title = soup.find("title")

            if title:
                name = title.get_text(
                    " ",
                    strip=True,
                )

        # -----------------------------------------------------
        # JSON-LD
        # -----------------------------------------------------

        json_name = None
        json_mpn = None
        json_brand = None

        for script in soup.find_all(
            "script",
            type="application/ld+json",
        ):

            raw = script.get_text(
                " ",
                strip=True,
            )

            match = re.search(
                r'"name"\s*:\s*"([^"]+)"',
                raw,
                re.I,
            )

            if match and not json_name:
                json_name = match.group(1)

            match = re.search(
                r'"sku"\s*:\s*"([^"]+)"',
                raw,
                re.I,
            )

            if match and not json_mpn:
                json_mpn = match.group(1)

            match = re.search(
                r'"mpn"\s*:\s*"([^"]+)"',
                raw,
                re.I,
            )

            if match and not json_mpn:
                json_mpn = match.group(1)

            match = re.search(
                r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                raw,
                re.I,
            )

            if match and not json_brand:
                json_brand = match.group(1)

        if not name and json_name:
            name = json_name

        # -----------------------------------------------------
        # MPN
        # -----------------------------------------------------

        mpn = json_mpn

        if not mpn:

            patterns = [
                r"Ürün\s*Kodu\s*:?\s*([A-Za-z0-9._/-]+)",
                r"Stok\s*Kodu\s*:?\s*([A-Za-z0-9._/-]+)",
                r"Ürün\s*No\s*:?\s*([A-Za-z0-9._/-]+)",
            ]

            for pattern in patterns:

                match = re.search(
                    pattern,
                    text,
                    re.I,
                )

                if match:

                    mpn = match.group(1)
                    break

        # -----------------------------------------------------
        # PRICE
        # -----------------------------------------------------

        price = self._find_price(soup)

        # -----------------------------------------------------
        # STOCK
        # -----------------------------------------------------

        lower = text.lower()

        if any(
            phrase in lower
            for phrase in [
                "stokta yok",
                "stok dışı",
                "tükendi",
                "satışta değil",
            ]
        ):

            in_stock = False
            stock_text = "Yok"

        elif any(
            phrase in lower
            for phrase in [
                "sepete ekle",
                "stokta",
                "hemen al",
            ]
        ):

            in_stock = True
            stock_text = "Var"

        else:

            in_stock = None
            stock_text = "Bilinmiyor"

        # -----------------------------------------------------
        # PACKAGE
        # -----------------------------------------------------

        package = None

        package_match = re.search(
            r"\b("
            r"DIP[- ]?\d+"
            r"|SOIC[- ]?\d+"
            r"|TSSOP[- ]?\d+"
            r"|QFN[- ]?\d+"
            r"|SMD"
            r"|THT"
            r"|TO[- ]?\d+"
            r")\b",
            text,
            re.I,
        )

        if package_match:

            package = (
                package_match
                .group(1)
                .upper()
                .replace(" ", "-")
            )

        # -----------------------------------------------------
        # MATCH SCORE
        # -----------------------------------------------------

        confidence = 0.0

        searchable = (
            f"{name} "
            f"{text} "
            f"{mpn or ''}"
        ).lower()

        if query.mpn:

            qmpn = query.mpn.lower()

            if qmpn == (mpn or "").lower():
                confidence += 0.80

            elif qmpn in searchable:
                confidence += 0.60

        if query.package and package:

            if (
                query.package.lower()
                == package.lower()
            ):
                confidence += 0.20

        confidence = min(
            confidence,
            1.0,
        )

        # -----------------------------------------------------
        # RESULT
        # -----------------------------------------------------

        return ProductResult(
            seller=self.name,
            name=name or query.raw,
            url=url,
            mpn=mpn,
            manufacturer=json_brand,
            package=package,
            stock_text=stock_text,
            in_stock=in_stock,
            unit_price=price,
            currency="TRY",
            confidence=confidence,
            source="robotistan",
            error=None,
            quantity_for_total=query.quantity,
        )

    # ---------------------------------------------------------
    # SEARCH
    # ---------------------------------------------------------

    def search(
        self,
        query,
    ):

        search_term = (
            query.mpn
            or query.raw
        )

        search_url = self.SEARCH_URL.format(
            quote(search_term)
        )

        try:

            response = self._get(
                search_url
            )

            if response.status_code != 200:

                return [
                    ProductResult(
                        seller=self.name,
                        name=query.raw,
                        url=search_url,
                        mpn=query.mpn,
                        package=query.package,
                        stock_text="Bilinmiyor",
                        in_stock=None,
                        confidence=0.0,
                        source="robotistan",
                        error=(
                            f"HTTP "
                            f"{response.status_code}"
                        ),
                        quantity_for_total=query.quantity,
                    )
                ]

            soup = BeautifulSoup(
                response.text,
                "lxml",
            )

            links = self._find_product_links(
                soup,
                query,
            )

            # Eğer arama sonucundan link bulamazsak,
            # doğrudan Robotistan içinde MPN geçen
            # bağlantıları tekrar dene.
            if not links:

                for a in soup.find_all(
                    "a",
                    href=True,
                ):

                    href = a.get(
                        "href",
                        "",
                    )

                    if href.startswith("/"):

                        href = (
                            self.BASE_URL
                            + href
                        )

                    if (
                        href.startswith(
                            self.BASE_URL
                        )
                        and href not in links
                    ):

                        text = a.get_text(
                            " ",
                            strip=True,
                        ).lower()

                        if (
                            query.mpn
                            and query.mpn.lower()
                            in text
                        ):

                            links.append(
                                href
                            )

            links = links[:10]

            results = []

            for url in links:

                try:

                    product = self._parse_product(
                        url,
                        query,
                    )

                    if product:
                        results.append(
                            product
                        )

                except Exception as exc:

                    results.append(
                        ProductResult(
                            seller=self.name,
                            name=query.raw,
                            url=url,
                            mpn=query.mpn,
                            package=query.package,
                            stock_text="Bilinmiyor",
                            in_stock=None,
                            confidence=0.0,
                            source="robotistan",
                            error=str(exc),
                            quantity_for_total=query.quantity,
                        )
                    )

            # Hiç ürün linki bulunamadıysa bunu
            # açıkça göster.
            if not results:

                results.append(
                    ProductResult(
                        seller=self.name,
                        name=query.raw,
                        url=search_url,
                        mpn=query.mpn,
                        package=query.package,
                        stock_text="Bilinmiyor",
                        in_stock=None,
                        confidence=0.0,
                        source="robotistan",
                        error=(
                            "Arama sayfası açıldı fakat "
                            "ürün bağlantısı bulunamadı."
                        ),
                        quantity_for_total=query.quantity,
                    )
                )

            return results

        except Exception as exc:

            return [
                ProductResult(
                    seller=self.name,
                    name=query.raw,
                    url=search_url,
                    mpn=query.mpn,
                    package=query.package,
                    stock_text="Bilinmiyor",
                    in_stock=None,
                    confidence=0.0,
                    source="robotistan",
                    error=str(exc),
                    quantity_for_total=query.quantity,
                )
            ]
