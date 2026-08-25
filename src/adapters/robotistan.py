import json
import re
from urllib.parse import quote_plus, urljoin
from .base import SellerAdapter
from .http import HttpClient
from src.core.models import ProductResult
from src.core.normalizer import parse_price, clean_text
from src.core.matcher import score

class RobotistanAdapter(SellerAdapter):
    name = "Robotistan"
    base = "https://www.robotistan.com"

    def __init__(self):
        self.http = HttpClient()

    def search(self, q):
        # Robotistan documents /arama?q=... as its public search URL.
        search_url = f"{self.base}/arama?q={quote_plus(q.mpn)}"
        try:
            response = self.http.get(search_url)
            soup = self.http.soup(response.text)
            candidates = self._find_candidates(soup, q)
            results = []
            for name, url, price_hint, stock_hint in candidates[:12]:
                result = self._product(url, q, name, price_hint, stock_hint)
                if result:
                    results.append(result)
            return self._dedupe(results)
        except Exception as exc:
            return [ProductResult(self.name, q.raw, search_url, mpn=q.mpn, quantity=q.quantity, error=str(exc))]

    def _find_candidates(self, soup, q):
        out, seen = [], set()
        needle = q.mpn.lower()
        # Product links generally have descriptive titles and contain the search term.
        for a in soup.select("a[href]"):
            href = urljoin(self.base, a.get("href", ""))
            text = clean_text(a.get_text(" ", strip=True))
            if not href.startswith(self.base) or len(text) < 5:
                continue
            if needle not in text.lower():
                continue
            if href in seen:
                continue
            # Avoid navigation/category/search links.
            if any(x in href.lower() for x in ("/arama", "/kategori", "/marka", "/blog", "/iletisim")):
                continue
            card = a.find_parent(["li", "article", "div"])
            blob = clean_text(card.get_text(" ", strip=True) if card else "")
            price = self._price_from_text(blob)
            stock = self._stock_from_text(blob)
            seen.add(href)
            out.append((text, href, price, stock))
        return out

    def _product(self, url, q, fallback_name, price_hint, stock_hint):
        try:
            soup = self.http.soup(self.http.get(url).text)
            text = clean_text(soup.get_text(" ", strip=True))
            name = self._meta(soup, "og:title") or self._jsonld_name(soup) or fallback_name
            mpn = self._find_label_value(text, ["Ürün Kodu", "Ürün kodu", "Product Code"]) or self._jsonld_value(soup, "sku")
            brand = self._find_label_value(text, ["Marka", "Brand"]) or self._jsonld_brand(soup)
            price = self._jsonld_price(soup) or self._vat_price(text) or price_hint
            stock_text = self._stock_from_text(text) or stock_hint
            in_stock = self._stock_bool(stock_text, text)
            package = self._package_from_text(text)
            product = ProductResult(
                self.name, name, url, mpn=mpn or q.mpn, manufacturer=brand,
                package=package or q.package, stock_text=stock_text, in_stock=in_stock,
                unit_price=price, quantity=q.quantity
            )
            product.confidence = score(q, product)
            return product
        except Exception:
            return ProductResult(self.name, fallback_name, url, mpn=q.mpn, package=q.package, quantity=q.quantity, error="Ürün sayfası okunamadı")

    @staticmethod
    def _meta(soup, prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return clean_text(tag.get("content")) if tag else None

    @staticmethod
    def _jsonld(soup):
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or tag.get_text())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        yield item
            except Exception:
                continue

    def _jsonld_name(self, soup):
        for x in self._jsonld(soup):
            if x.get("name") and x.get("@type") in ("Product", ["Product"]):
                return clean_text(x["name"])

    def _jsonld_value(self, soup, key):
        for x in self._jsonld(soup):
            if x.get(key): return clean_text(x[key])

    def _jsonld_brand(self, soup):
        for x in self._jsonld(soup):
            b = x.get("brand")
            if isinstance(b, dict) and b.get("name"): return clean_text(b["name"])
            if isinstance(b, str): return clean_text(b)

    def _jsonld_price(self, soup):
        for x in self._jsonld(soup):
            offers = x.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offers, dict) and offers.get("price"):
                return parse_price(offers["price"])

    @staticmethod
    def _find_label_value(text, labels):
        for label in labels:
            m = re.search(re.escape(label) + r"\s*[:|-]\s*([^|]{1,80})", text, re.I)
            if m: return clean_text(m.group(1))
        return None

    @staticmethod
    def _vat_price(text):
        m = re.search(r"KDV\s+Dahil\s+Fiyat\s*[:]?\s*([\d.,]+)\s*TL", text, re.I)
        return parse_price(m.group(1)) if m else None

    @staticmethod
    def _price_from_text(text):
        m = re.search(r"(?:KDV\s+Dahil\s+Fiyat\s*)?([\d.]+,\d{2})\s*TL", text, re.I)
        return parse_price(m.group(1)) if m else None

    @staticmethod
    def _stock_from_text(text):
        for phrase in ("Stokta", "Sepete Ekle", "STOKLARA DÜŞÜNCE HABER VER", "Tükendi", "Stok Yok"):
            if phrase.lower() in text.lower(): return phrase
        return None

    @staticmethod
    def _stock_bool(stock, text):
        if not stock: return None
        s = stock.lower()
        if "stokta" in s or "sepete ekle" in s: return True
        if "tükendi" in s or "stok yok" in s or "haber ver" in s: return False
        return None

    @staticmethod
    def _package_from_text(text):
        m = re.search(r"\b(DIP|SOIC|SOP|TSSOP|QFN|DFN|TO)[ -]?(\d+)\b", text, re.I)
        return f"{m.group(1).upper()}-{m.group(2)}" if m else None

    @staticmethod
    def _dedupe(items):
        seen=set(); out=[]
        for x in items:
            if x.url and x.url not in seen:
                seen.add(x.url); out.append(x)
        return out[:10]
