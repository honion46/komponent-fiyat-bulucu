from abc import ABC, abstractmethod
class SellerAdapter(ABC):
    name = 'unknown'
    @abstractmethod
    def search(self, query): raise NotImplementedError
