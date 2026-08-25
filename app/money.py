"""Money helpers.

All monetary amounts in TrustRail are integers in the currency's smallest unit
(paise for INR), following Razorpay's convention. This avoids floating-point
rounding entirely. ₹5,000.00 is represented as 500000.
"""

from __future__ import annotations

CURRENCY_MINOR_UNITS = {"INR": 2, "USD": 2, "EUR": 2}
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€"}


def minor_units(currency: str) -> int:
    return CURRENCY_MINOR_UNITS.get(currency.upper(), 2)


def format_amount(minor: int, currency: str = "INR") -> str:
    """Render an integer minor-unit amount as a human string, e.g. ``₹5,000.00``."""
    exp = minor_units(currency)
    major = minor / (10**exp)
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), "")
    return f"{symbol}{major:,.{exp}f}"
