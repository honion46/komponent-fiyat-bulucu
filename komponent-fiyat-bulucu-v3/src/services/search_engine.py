from concurrent.futures import ThreadPoolExecutor, as_completed
from src.adapters.robotistan import RobotistanAdapter
from src.adapters.direnc import DirencAdapter

class SearchEngine:
    def __init__(self, adapters=None):
        self.adapters=adapters or [RobotistanAdapter(),DirencAdapter()]
    def search(self,q):
        results=[]
        with ThreadPoolExecutor(max_workers=len(self.adapters)) as ex:
            futures=[ex.submit(a.search,q) for a in self.adapters]
            for f in as_completed(futures):
                try: results.extend(f.result())
                except Exception as e: results.append(type('R',(),{'error':str(e)})())
        return results
