def cheapest(results):
    valid=[r for r in results if r.unit_price is not None and r.in_stock is not False]
    return min(valid,key=lambda x:x.unit_price) if valid else None
