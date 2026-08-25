import re
from .normalizer import clean_text

def _norm(v): return re.sub(r'[^A-Z0-9]','',str(v or '').upper())

def score(query, product):
    q,p=_norm(query.mpn),_norm(product.mpn)
    s=0.0
    if q and p:
        if q==p:s+=0.80
        elif q in p or p in q:s+=0.50
    if query.package and product.package:
        if _norm(query.package)==_norm(product.package):s+=0.20
        else:s-=0.10
    if product.name and q in _norm(product.name):s+=0.10
    return max(0.0,min(1.0,s))
