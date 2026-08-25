import re
from .models import SearchQuery

QTY = re.compile(r"\b(\d+)\s*(?:adet|pcs|pieces?|parça)\b", re.I)
PKG = re.compile(r"\b(DIP|SOIC|SOP|TSSOP|QFN|DFN|TO)\s*[- ]?\s*(\d+)?\b", re.I)

def parse_query(raw: str) -> SearchQuery:
    text = " ".join(raw.split())
    qm = QTY.search(text)
    qty = int(qm.group(1)) if qm else 1
    pm = PKG.search(text)
    package = f"{pm.group(1).upper()}-{pm.group(2)}" if pm and pm.group(2) else (pm.group(1).upper() if pm else None)
    cleaned = QTY.sub("", text)
    if package:
        cleaned = PKG.sub("", cleaned)
    tokens = cleaned.split()
    mpn = tokens[0].upper() if tokens else text.upper()
    return SearchQuery(raw=raw, mpn=mpn, package=package, quantity=qty)
