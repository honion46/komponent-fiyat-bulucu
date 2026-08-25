from concurrent.futures import ThreadPoolExecutor, as_completed
from src.adapters.robotistan import RobotistanAdapter
from src.adapters.direnc import DirencAdapter
from src.core.models import ProductResult

class SearchEngine:
    def __init__(self, adapters=None):
        self.adapters=adapters or [RobotistanAdapter(),DirencAdapter()]
    def search(self,q):
        results=[]
        with ThreadPoolExecutor(max_workers=len(self.adapters)) as ex:
            futures={ex.submit(a.search,q):a for a in self.adapters}
            for f,a in futures.items():
                try: results.extend(f.result())
                except Exception as e:
                    results.append(ProductResult(getattr(a,'name',a.__class__.__name__),q.raw,'',mpn=q.mpn,package=q.package,quantity=q.quantity,error=str(e)))
        return sorted(results,key=lambda r:(r.unit_price is None, -(r.confidence or 0), r.unit_price or 0))
