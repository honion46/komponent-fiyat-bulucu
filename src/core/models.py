from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

@dataclass
class SearchQuery:
    raw: str
    mpn: Optional[str] = None
    manufacturer: Optional[str] = None
    package: Optional[str] = None
    quantity: int = 1
    attributes: dict = field(default_factory=dict)
    def to_dict(self):
        return {'raw': self.raw, 'mpn': self.mpn, 'manufacturer': self.manufacturer, 'package': self.package, 'quantity': self.quantity, 'attributes': self.attributes}

@dataclass
class ProductResult:
    seller: str
    name: str
    url: str = ''
    mpn: Optional[str] = None
    manufacturer: Optional[str] = None
    package: Optional[str] = None
    stock: Optional[int] = None
    unit_price: Optional[Decimal] = None
    currency: str = 'TRY'
    confidence: float = 0.0
    source: str = 'unknown'
    error: Optional[str] = None
    quantity_for_total: int = 1
    @property
    def total_price(self):
        return self.unit_price * self.quantity_for_total if self.unit_price is not None else None
    def to_dict(self):
        return {'Satıcı': self.seller, 'Ürün': self.name, 'MPN': self.mpn or '', 'Üretici': self.manufacturer or '', 'Paket': self.package or '', 'Stok': self.stock if self.stock is not None else '', 'Birim Fiyat': str(self.unit_price) if self.unit_price is not None else '', 'Para Birimi': self.currency, 'Toplam': str(self.total_price) if self.total_price is not None else '', 'Güven': f'{self.confidence:.0%}', 'Kaynak': self.source, 'URL': self.url, 'Hata': self.error or ''}
