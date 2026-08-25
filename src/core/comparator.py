def comparable_results(results): return [r for r in results if r.error is None and r.unit_price is not None]
def cheapest(results):
    valid = comparable_results(results)
    return min(valid, key=lambda r: r.unit_price) if valid else None
