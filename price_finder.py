"""
Basit, modüler fiyat-bulucu adaptörleri.

- MockAdapter: test amaçlı sabit/rasgele fiyat döndürür.
- ScraperAdapter: verilen URL şablonunda {sku} ile URL oluşturur, sayfayı alır ve
  verilen CSS seçiciyle fiyat metnini çıkarır. (Kullanıcı CSS seçici verir.)

Fonksiyonlar:
- lookup_prices(adapter, skus) -> list of dicts
"""
import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import random
import time

class PriceResult(Dict):
    pass

class BaseAdapter:
    name = "base"

    def lookup(self, sku: str) -> PriceResult:
        raise NotImplementedError

class MockAdapter(BaseAdapter):
    name = "mock"

    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)

    def lookup(self, sku: str) -> PriceResult:
        # deterministic-ish mock: base from hash, small randomness
        base = (abs(hash(sku)) % 1000) / 10.0 + 1.0
        variance = random.uniform(-0.2, 0.2) * base
        price = round(max(0.01, base + variance), 2)
        return {
            "supplier": "MockSupplier",
            "sku": sku,
            "price": price,
            "currency": "USD",
            "in_stock": random.choice([True, True, True, False]),
            "url": None,
            "raw": None,
            "error": None,
        }

class ScraperAdapter(BaseAdapter):
    name = "scraper"

    def __init__(self, url_template: str, price_css_selector: str, headers: Dict[str,str] = None, timeout: float = 10.0, delay: float = 0.5):
        """
        url_template: e.g. "https://supplier.example/search?q={sku}"
        price_css_selector: CSS selector to extract price text from page (e.g. ".price .value")
        headers: optional request headers
        timeout: request timeout
        delay: seconds to sleep between requests (politeness)
        """
        self.url_template = url_template
        self.price_css_selector = price_css_selector
        self.headers = headers or {"User-Agent": "price-finder-bot/1.0 (+https://example)"}
        self.timeout = timeout
        self.delay = delay

    def _normalize_price(self, text: str) -> Optional[float]:
        if not text:
            return None
        # Remove non-numeric except ., and comma; handle comma as decimal if needed
        t = text.strip()
        # Remove currency symbols and letters
        t = re.sub(r"[^\d.,\-]", "", t)
        if t == "":
            return None
        # If multiple separators, try to guess decimal point
        if t.count(",") > 0 and t.count(".") == 0:
            t = t.replace(",", ".")
        # If both present, remove thousands separators (commas)
        if t.count(",") > 0 and t.count(".") > 0:
            if t.find(",") < t.find("."):
                t = t.replace(",", "")
            else:
                t = t.replace(".", "").replace(",", ".")
        try:
            return float(t)
        except Exception:
            return None

    def lookup(self, sku: str) -> PriceResult:
        url = self.url_template.format(sku=sku)
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout)
            resp.raise_for_status()
            html = resp.text
            soup = BeautifulSoup(html, "lxml")
            elem = soup.select_one(self.price_css_selector)
            price = None
            raw = None
            if elem:
                raw = elem.get_text(separator=" ", strip=True)
                price = self._normalize_price(raw)
            # optional stock detection (simple heuristic)
            in_stock = True
            stock_indicators = ["out of stock", "sold out", "stok yok", "tükenmiş"]
            page_text = soup.get_text(" ").lower()
            for patt in stock_indicators:
                if patt in page_text:
                    in_stock = False
                    break
            time.sleep(self.delay)
            return {
                "supplier": url.split("/")[2] if "//" in url else "scraper",
                "sku": sku,
                "price": price,
                "currency": None,
                "in_stock": in_stock,
                "url": url,
                "raw": raw,
                "error": None,
            }
        except Exception as e:
            return {
                "supplier": url.split("/")[2] if "//" in url else "scraper",
                "sku": sku,
                "price": None,
                "currency": None,
                "in_stock": False,
                "url": url,
                "raw": None,
                "error": str(e),
            }

def lookup_prices(adapter: BaseAdapter, skus: List[str]) -> List[PriceResult]:
    results = []
    for sku in skus:
        sku = sku.strip()
        if not sku:
            continue
        res = adapter.lookup(sku)
        results.append(res)
    return results
