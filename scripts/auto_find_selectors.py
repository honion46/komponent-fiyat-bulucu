#!/usr/bin/env python3
import json, re, sys
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

try:
    from turkiye_adapters import DEFAULTS
except Exception:
    DEFAULTS = {
        "Robotistan": {"url_template": "https://www.robotistan.com/arama?q={sku}", "price_selector": ".product-price"},
        "RobotHobi": {"url_template": "https://www.robothobi.com.tr/?s={sku}", "price_selector": ".price"},
        "Hepsiburada": {"url_template": "https://www.hepsiburada.com/ara?q={sku}", "price_selector": ".price"},
        "N11": {"url_template": "https://www.n11.com/arama?q={sku}", "price_selector": ".proDetailPrice"},
    }

HEADERS = {"User-Agent": "price-finder-bot/1.0 (+https://example)"}
PRICE_RE = re.compile(r"(?:\d{1,3}[.,])?\d{1,3}[.,]\d{2}")
SAMPLE_SKUS = ["arduino", "esp32", "atmega328", "lm317"]

def find_price_elements(soup):
    candidates = []
    for t in soup.find_all(text=True):
        if t and isinstance(t, str) and PRICE_RE.search(t.strip()):
            candidates.append((t.parent, t.strip()))
    return candidates

def selector_from_element(el):
    if el.name is None:
        return None
    if el.get('id'):
        return f"#{el.get('id')}"
    classes = el.get('class') or []
    if classes:
        return f"{el.name}.{classes[0]}"
    p = el.parent
    while p and p.name is not None and p.name != '[document]':
        if p.get('id'):
            return f"#{p.get('id')} {el.name}"
        pcls = p.get('class') or []
        if pcls:
            return f"{p.name}.{pcls[0]} {el.name}"
        p = p.parent
    return el.name

def test_selector_uniqueness(soup, selector, expected_text):
    try:
        sel = soup.select(selector)
    except Exception:
        return False
    if not sel:
        return False
    for el in sel:
        if expected_text in el.get_text(separator=" ", strip=True):
            return True
    return False

def detect_for_site(name, conf):
    url_template = conf.get("url_template")
    suggestions = []
    for sku in SAMPLE_SKUS:
        query = quote_plus(sku)
        url = url_template.format(sku=query)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            price_candidates = find_price_elements(soup)
            for el, raw_text in price_candidates[:6]:
                sel = selector_from_element(el)
                if not sel:
                    continue
                unique = test_selector_uniqueness(soup, sel, raw_text)
                suggestions.append({"sku": sku, "url": url, "raw_text": raw_text, "selector": sel, "unique": unique})
            if suggestions:
                break
        except Exception as e:
            suggestions.append({"sku": sku, "url": url, "error": str(e)})
    return suggestions

def main():
    out = {}
    for name, conf in DEFAULTS.items():
        out[name] = detect_for_site(name, conf)
    with open('scripts/selector_suggestions.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    snippet = {}
    for k, v in out.items():
        sel = None
        if isinstance(v, list) and v:
            for cand in v:
                if cand.get('unique'):
                    sel = cand.get('selector')
                    break
            if not sel:
                sel = v[0].get('selector')
        snippet[k] = {"url_template": DEFAULTS[k]['url_template'], "price_selector": sel}
    print(json.dumps(snippet, indent=2, ensure_ascii=False))
    print('Saved scripts/selector_suggestions.json')

if __name__ == '__main__':
    main()
