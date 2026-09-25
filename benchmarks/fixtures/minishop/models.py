"""Product and money primitives."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int
    weight_g: int = 0

    @property
    def price(self) -> float:
        """Price in whole currency units, rounded to 2 decimals."""
        return round(self.price_cents / 100, 2)


@dataclass
class CartLine:
    product: Product
    qty: int = 1

    @property
    def total_cents(self) -> int:
        return self.product.price_cents * self.qty
