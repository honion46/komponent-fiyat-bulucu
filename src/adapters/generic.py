from .base import SellerAdapter
from src.core.models import ProductResult
class UnconfiguredAdapter(SellerAdapter):
    def __init__(self, name, base_url=''): self.name, self.base_url = name, base_url
    def search(self, query):
        return [ProductResult(seller=self.name, name=query.mpn or query.raw, quantity_for_total=query.quantity, confidence=0.0, source='not-configured', error='Bu satıcı için gerçek arama adaptörü henüz yapılandırılmadı.')]
