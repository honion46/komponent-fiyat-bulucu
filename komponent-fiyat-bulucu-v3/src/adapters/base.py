from abc import ABC, abstractmethod
from src.core.models import SearchQuery, ProductResult
class SellerAdapter(ABC):
    name=''
    @abstractmethod
    def search(self, query: SearchQuery): ...
