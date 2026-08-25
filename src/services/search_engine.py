from concurrent.futures import ThreadPoolExecutor, as_completed

from src.adapters.robotistan import RobotistanAdapter
from src.adapters.robothobi import RobotHobiAdapter
from src.core.models import ProductResult


class SearchEngine:

    def __init__(self, adapters=None):

        self.adapters = adapters or [
            RobotistanAdapter(),
            RobotHobiAdapter(),
        ]

    def _search_adapter(self, adapter, query):

        try:
            return adapter.search(query)

        except Exception as exc:

            return [
                ProductResult(
                    seller=getattr(
                        adapter,
                        "name",
                        adapter.__class__.__name__,
                    ),
                    name=query.raw,
                    url="",
                    mpn=query.mpn,
                    package=query.package,
                    stock_text="Bilinmiyor",
                    in_stock=None,
                    confidence=0.0,
                    source="adapter-error",
                    error=str(exc),
                    quantity_for_total=query.quantity,
                )
            ]

    def search(self, query):

        results = []

        if not self.adapters:
            return results

        with ThreadPoolExecutor(
            max_workers=len(self.adapters)
        ) as executor:

            futures = {
                executor.submit(
                    self._search_adapter,
                    adapter,
                    query,
                ): adapter
                for adapter in self.adapters
            }

            for future in as_completed(futures):

                try:
                    adapter_results = future.result()

                    if adapter_results:
                        results.extend(adapter_results)

                except Exception as exc:

                    adapter = futures[future]

                    results.append(
                        ProductResult(
                            seller=getattr(
                                adapter,
                                "name",
                                adapter.__class__.__name__,
                            ),
                            name=query.raw,
                            url="",
                            mpn=query.mpn,
                            package=query.package,
                            stock_text="Bilinmiyor",
                            in_stock=None,
                            confidence=0.0,
                            source="search-engine-error",
                            error=str(exc),
                            quantity_for_total=query.quantity,
                        )
                    )

        # En yüksek eşleşme puanı önce
        results.sort(
            key=lambda x: (
                x.confidence,
                x.unit_price is not None,
            ),
            reverse=True,
        )

        return results
