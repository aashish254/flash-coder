"""Formatting helpers."""
from __future__ import annotations


def money(cents: int) -> str:
    """1599 -> '$15.99'; negative values get a leading '-'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def receipt_line(name: str, qty: int, line_total_cents: int) -> str:
    """'Apple x3 -> $5.97' (name padded to 20 chars)."""
    return f"{name:<20} x{qty} -> {money(line_total_cents)}"
