from concurrent.futures import ThreadPoolExecutor, as_completed

from src.adapters.robotistan import RobotistanAdapter
from src.adapters.robothobi import RobotHobiAdapter


class SearchEngine:

    def __init__(self):
        self.adapters = [
            RobotistanAdapter(),
            RobotHobiAdapter(),
        ]

    def search(self, query):
        results = []

        with ThreadPoolExecutor(max_workers=len(self.adapters)) as executor:

            futures = {
                executor.submit(adapter.search, query): adapter
                for adapter in self.adapters
            }

            for future in as_completed(futures):

                adapter = futures[future]

                try:
                    adapter_results = future.result()

                    if adapter_results:
                        results.extend(adapter_results)

                except Exception as exc:

                    # Adapter hata verdiğinde uygulama çökmemeli
                    results.append({
                        "seller": getattr(
                            adapter,
                            "name",
                            adapter.__class__.__name__
                        ),
                        "name": query.raw,
                        "mpn": query.mpn,
                        "package": query.package,
                        "url": "",
                        "unit_price": None,
                        "total_price": None,
                        "currency": "TRY",
                        "stock_text": "Hata",
                        "in_stock": None,
                        "confidence": 0,
                        "error": str(exc),
                    })

        return results
