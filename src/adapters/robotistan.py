import re
from decimal import Decimal

from bs4 import BeautifulSoup
from curl_cffi import requests

from .base import SellerAdapter
from src.core.models import ProductResult


class RobotistanAdapter(SellerAdapter):
    name = "Robotistan"

    SEARCH_URL = "https://www.robotistan.com/arama?q={}"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) "
            "AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    }

    def _get(self, url):
        return requests.get(
            url,
            headers=self.HEADERS,
            impersonate="chrome",
            timeout=20,
        )

    def _price(self, text):
        patterns = [
            r"KDV\s+Dahil\s+Fiyat\s*:?\s*([\d.,]+)\s*TL",
            r"KDV\s+Dahil\s*[:\-]?\s*([\d.,]+)\s*TL",
            r'"price"\s*:\s*"([\d.,]+)"',
            r'"price"\s*:\s*([\d.]+)',
            r"([\d.,]+)\s*TL",
        ]

        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                value = m.group(1).replace(".", "").replace(",", ".")
                try:
                    return Decimal(value)
                except Exception:
                    pass

        return None

    def _product_links(self, soup, query):
        links = []

        q = query.mpn.lower() if query.mpn else query.raw.lower()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)

            if not href:
                continue

            if href.startswith("/"):
                href = "https://www.robotistan.com" + href

            if "robotistan.com" not in href:
                continue

            combined = f"{title} {href}".lower()

            if q in combined:
                if href not in links:
                    links.append(href)

        return links[:8]

    def _parse_product(self, url, query):
        response = self._get(url)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "lxml")

        text = soup.get_text(" ", strip=True)

        # Ürün adı
        name = ""

        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(" ", strip=True)

        if not name:
            title = soup.find("title")
            if title:
                name = title.get_text(" ", strip=True)

        # MPN / ürün kodu
        mpn = None

        patterns = [
            r"Ürün\s*Kodu\s*:?\s*([A-Za-z0-9._/-]+)",
            r"Ürün\s*Kodu\s*([A-Za-z0-9._/-]+)",
            r'"sku"\s*:\s*"([^"]+)"',
            r'"mpn"\s*:\s*"([^"]+)"',
        ]

        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                mpn = m.group(1).strip()
                break

        # JSON-LD'den de ürün bilgilerini dene
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.get_text(strip=True)

            m = re.search(r'"sku"\s*:\s*"([^"]+)"', raw, re.I)
            if m and not mpn:
                mpn = m.group(1)

            m = re.search(r'"mpn"\s*:\s*"([^"]+)"', raw, re.I)
            if m and not mpn:
                mpn = m.group(1)

        price = self._price(text)

        # Stok
        lower = text.lower()

        if (
            "stokta yok" in lower
            or "tükendi" in lower
            or "stok dışı" in lower
        ):
            in_stock = False
            stock_text = "Yok"
        elif (
            "sepete ekle" in lower
            or "sepete ekle" in lower
            or "stokta" in lower
        ):
            in_stock = True
            stock_text = "Var"
        else:
            in_stock = None
            stock_text = "Bilinmiyor"

        # Basit paket tespiti
        package = None

        package_match = re.search(
            r"\b(DIP[- ]?\d+|SOIC[- ]?\d+|SMD|THT|TO[- ]?\d+)\b",
            text,
            re.I,
        )

        if package_match:
            package = package_match.group(1).upper().replace(" ", "-")

        # Eşleşme
        confidence = 0.0

        qmpn = (query.mpn or "").lower()
        alltext = f"{name} {text} {mpn or ''}".lower()

        if qmpn and qmpn in alltext:
            confidence += 0.75

        if query.package and query.package.lower() in alltext:
            confidence += 0.20

        confidence = min(confidence, 1.0)

        return ProductResult(
            seller=self.name,
            name=name or query.raw,
            url=url,
            mpn=mpn,
            manufacturer="Robotistan",
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

    def search(self, query):

        search_url = self.SEARCH_URL.format(
            requests.utils.quote(query.raw)
        )

        try:
            response = self._get(search_url)

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
                        confidence=0,
                        source="robotistan",
                        error=f"HTTP {response.status_code}",
                        quantity_for_total=query.quantity,
                    )
                ]

            soup = BeautifulSoup(response.text, "lxml")

            links = self._product_links(soup, query)

            results = []

            for url in links:
                try:
                    product = self._parse_product(url, query)

                    if product:
                        results.append(product)

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
                            confidence=0,
                            source="robotistan",
                            error=str(exc),
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
                    confidence=0,
                    source="robotistan",
                    error=str(exc),
                    quantity_for_total=query.quantity,
                )
            ]
