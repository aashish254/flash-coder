"""Pricing rules: bulk and loyalty discounts. All math in cents."""
from __future__ import annotations

# bulk: 10% off any line with qty >= BULK_MIN_QTY
BULK_MIN_QTY = 5
BULK_PCT = 10

# loyalty: extra 5% off the cart subtotal for members
LOYALTY_PCT = 5


def bulk_discount_cents(line_total_cents: int, qty: int) -> int:
    """Discount for one line; integer cents, truncated (never rounded up)."""
    if qty >= BULK_MIN_QTY:
        return line_total_cents * BULK_PCT // 100
    return 0


def loyalty_discount_cents(subtotal_cents: int, member: bool) -> int:
    """Member discount on the post-bulk subtotal."""
    if member:
        return subtotal_cents * LOYALTY_PCT // 100
    return 0
