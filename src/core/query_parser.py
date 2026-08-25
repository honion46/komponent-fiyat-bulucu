import re
from .models import SearchQuery
QTY_RE = re.compile(r'\b(\d+)\s*(?:adet|pcs|piece|parça)\b', re.I)

def parse_query(raw: str) -> SearchQuery:
    text = ' '.join(raw.strip().split())
    qm = QTY_RE.search(text)
    quantity = int(qm.group(1)) if qm else 1
    package = None
    pm = re.search(r'\b(DIP|SOIC)[ -]?(\d+)\b', text, re.I)
    if pm: package = f'{pm.group(1).upper()}-{pm.group(2)}'
    cleaned = QTY_RE.sub('', text)
    if package: cleaned = re.sub(r'\b(?:DIP|SOIC)[ -]?\d+\b', '', cleaned, flags=re.I)
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9._/-]{1,30}', cleaned)
    mpn = max(tokens, key=len).upper() if tokens else cleaned.strip().upper()
    attrs = {}
    wm = re.search(r'\b(1/4|1/2|1|2)\s*W\b', text, re.I)
    if wm: attrs['power'] = {'1/4':'0.25W','1/2':'0.5W'}.get(wm.group(1), wm.group(1)+'W')
    return SearchQuery(raw=raw, mpn=mpn, package=package, quantity=quantity, attributes=attrs)
