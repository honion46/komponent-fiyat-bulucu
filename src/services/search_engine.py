from src.adapters.robotistan import RobotistanAdapter
from src.adapters.robothobi import RobotHobiAdapter
class SearchEngine:
    def __init__(self, adapters=None): self.adapters = adapters or [RobotistanAdapter(), RobotHobiAdapter()]
    def search(self, query):
        results=[]
        for adapter in self.adapters:
            try: results.extend(adapter.search(query))
            except Exception as exc:
                from src.core.models import ProductResult
                results.append(ProductResult(seller=adapter.name, name=query.mpn or query.raw, quantity_for_total=query.quantity, source='adapter-error', error=str(exc)))
        return results
