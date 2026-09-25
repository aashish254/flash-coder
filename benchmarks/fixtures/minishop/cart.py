"""The Cart: lines plus checkout math."""
from __future__ import annotations

from .models import CartLine, Product
from .pricing import bulk_discount_cents, loyalty_discount_cents


class Cart:
    def __init__(self, member: bool = False):
        self.member = member
        self.lines: list[CartLine] = []

    def add(self, product: Product, qty: int = 1) -> None:
        for line in self.lines:
            if line.product.sku == product.sku:
                line.qty += qty
                return
        self.lines.append(CartLine(product, qty))

    def subtotal_cents(self) -> int:
        """Sum of lines minus per-line bulk discounts."""
        return sum(l.total_cents - bulk_discount_cents(l.total_cents, l.qty)
                   for l in self.lines)

    def total_cents(self) -> int:
        """Subtotal minus loyalty discount for members."""
        sub = self.subtotal_cents()
        return sub - loyalty_discount_cents(sub, self.member)

    def heaviest_g(self) -> int:
        """Weight of the heaviest single unit in the cart (0 if empty)."""
        return max((l.product.weight_g for l in self.lines), default=0)
