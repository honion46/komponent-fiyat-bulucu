from .normalizer import normalize_mpn, normalize_text

def match_score(query, product):
    score = 0.0
    if query.mpn and product.mpn:
        q, p = normalize_mpn(query.mpn), normalize_mpn(product.mpn)
        if q == p: score += .75
        elif q in p or p in q: score += .45
    if query.package and product.package and normalize_text(query.package).lower() == normalize_text(product.package).lower(): score += .20
    if query.manufacturer and product.manufacturer and normalize_text(query.manufacturer).lower() == normalize_text(product.manufacturer).lower(): score += .05
    return min(score, 1.0)
