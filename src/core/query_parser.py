import re
from src.core.models import SearchQuery
QTY=re.compile(r'\b(\d+)\s*(?:adet|pcs|pieces?|parça)\b',re.I)
PKG=re.compile(r'\b(DIP|SOIC|SOP|TSSOP|QFN|DFN|TO)\s*[- ]?\s*(\d+)?\b',re.I)

def parse_query(raw):
    text=' '.join(raw.split()); qm=QTY.search(text); qty=int(qm.group(1)) if qm else 1
    pm=PKG.search(text); package=f'{pm.group(1).upper()}-{pm.group(2)}' if pm and pm.group(2) else (pm.group(1).upper() if pm else None)
    cleaned=QTY.sub('',text); cleaned=PKG.sub('',cleaned)
    # Prefer the longest electronics-like token; this handles MP1584EN and LM358P.
    toks=[t for t in cleaned.split() if re.search(r'[A-Za-z]',t)]
    mpn=max(toks,key=len).upper() if toks else text.upper()
    return SearchQuery(raw=raw,mpn=mpn,package=package,quantity=qty)
