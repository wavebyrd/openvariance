"""Decimal helpers. Money never touches binary floating point inside the engine."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

ZERO = Decimal(0)
CENT = Decimal("0.01")

Number = Decimal | int | float | str | Fraction


def to_decimal(value: Number) -> Decimal:
    """Convert a number to Decimal without picking up float representation noise."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Not a finite number: {value!r}")
        return Decimal(repr(value))
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Not a number: {value!r}") from None


def round_money(value: Decimal, quantum: Decimal = CENT) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def allocate(
    parts: Sequence[Fraction], quantum: Decimal = CENT, total: Decimal | None = None
) -> list[Decimal]:
    """Round exact parts to ``quantum`` so the rounded parts sum to a rounded total.

    The total defaults to the rounded sum of the parts. Uses the largest-remainder
    method: every part is floored, then the units lost to rounding go to the parts
    with the largest remainders (or come from the smallest, if the target is lower).
    This is what keeps a variance bridge reconciling to the cent after rounding.
    """
    if not parts:
        return []
    q = Fraction(quantum)
    scaled = [p / q for p in parts]
    floors = [math.floor(s) for s in scaled]
    if total is None:
        target = round_half_up(sum(scaled, Fraction(0)))
    else:
        target = int(Fraction(total) / q)
    shortfall = target - sum(floors)
    order = sorted(range(len(parts)), key=lambda i: (scaled[i] - floors[i], -i), reverse=True)
    step = 1 if shortfall >= 0 else -1
    if step < 0:
        order.reverse()
    for n in range(abs(shortfall)):
        floors[order[n % len(order)]] += step
    return [Decimal(units) * quantum for units in floors]


def round_half_up(value: Fraction) -> int:
    """Round a Fraction to the nearest integer, halves away from zero."""
    sign = -1 if value < 0 else 1
    return sign * math.floor(abs(value) + Fraction(1, 2))
