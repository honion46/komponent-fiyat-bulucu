from .normalizer import clean_text

def score(query, product):
    s=0.0
    q=(query.mpn or '').upper(); p=(product.mpn or '').upper()
    if q and p:
        if q == p: s += .75
        elif q in p or p in q: s += .45
    if query.package and product.package and clean_text(query.package).lower() == clean_text(product.package).lower(): s += .25
    return min(s,1.0)
