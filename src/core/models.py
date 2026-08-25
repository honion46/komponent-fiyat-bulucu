from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

@dataclass
class SearchQuery:
    raw: str
    mpn: str
    package: Optional[str] = None
    quantity: int = 1
    attributes: dict = field(default_factory=dict)

@dataclass
class ProductResult:
    seller: str
    name: str
    url: str
    mpn: Optional[str] = None
    manufacturer: Optional[str] = None
    package: Optional[str] = None
    stock_text: Optional[str] = None
    in_stock: Optional[bool] = None
    unit_price: Optional[Decimal] = None
    currency: str = "TRY"
    confidence: float = 0.0
    error: Optional[str] = None
    quantity: int = 1

    @property
    def total_price(self):
        return self.unit_price * self.quantity if self.unit_price is not None else None
