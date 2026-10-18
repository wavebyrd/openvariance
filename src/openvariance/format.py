"""Number formatting for reports: accounting style, compact money, percentages."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def compact(value: Decimal | None, signed: bool = False) -> str:
    """$1.02M, $180K, $950. With ``signed``, a leading + or − (true minus sign)."""
    if value is None:
        return "—"
    sign = "−" if value < 0 else ("+" if signed and value > 0 else "")
    v = abs(value)
    for size, suffix in (
        (Decimal(1_000_000_000), "B"),
        (Decimal(1_000_000), "M"),
        (Decimal(1_000), "K"),
    ):
        if v >= size:
            scaled = v / size
            places = 2 if scaled < 10 else (1 if scaled < 100 else 0)
            text = f"{scaled.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)}"
            return f"{sign}${text}{suffix}"
    return f"{sign}${v.quantize(Decimal(1), ROUND_HALF_UP):,}"


def accounting(value: Decimal | None, places: int = 0, scale: Decimal = Decimal(1)) -> str:
    """1,234 or (1,234) for negatives; an em dash for missing data."""
    if value is None:
        return "—"
    v = (value / scale).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    if v == 0:
        return "0" if places == 0 else f"{0:.{places}f}"
    text = f"{abs(v):,.{places}f}"
    return f"({text})" if v < 0 else text


def percent(value: Decimal | None, places: int = 1, signed: bool = False) -> str:
    """12.5% or (12.5%); "n/m" when not meaningful (zero baseline)."""
    if value is None:
        return "n/m"
    v = (value * 100).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    text = f"{abs(v):.{places}f}%"
    if v < 0:
        return f"({text})" if not signed else f"−{text}"
    return f"+{text}" if signed and v > 0 else text
