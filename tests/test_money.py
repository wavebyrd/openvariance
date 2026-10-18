from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from openvariance.money import allocate, round_half_up, to_decimal


def test_to_decimal_avoids_float_noise() -> None:
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(Fraction(1, 4)) == Decimal("0.25")


def test_round_half_up_is_symmetric() -> None:
    assert round_half_up(Fraction(5, 2)) == 3
    assert round_half_up(Fraction(-5, 2)) == -3


def test_allocate_thirds_reconcile_to_the_cent() -> None:
    parts = [Fraction(100, 3)] * 3
    assert allocate(parts) == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(allocate(parts)) == Decimal("100.00")


@given(
    st.lists(
        st.fractions(min_value=-(10**7), max_value=10**7, max_denominator=997),
        min_size=1,
        max_size=12,
    )
)
def test_allocate_always_sums_to_rounded_total(parts: list[Fraction]) -> None:
    rounded = allocate(parts)
    assert sum(rounded) == Decimal(round_half_up(sum(parts) * 100)) / 100
    assert all(abs(Fraction(r) - p) < Fraction(2, 100) for r, p in zip(rounded, parts, strict=True))


@given(
    st.lists(
        st.fractions(min_value=-(10**6), max_value=10**6, max_denominator=97),
        min_size=1,
        max_size=8,
    ),
    st.integers(min_value=-1, max_value=1),
)
def test_allocate_hits_an_explicit_total(parts: list[Fraction], nudge: int) -> None:
    target = Decimal(round_half_up(sum(parts) * 100) + nudge) / 100
    assert sum(allocate(parts, total=target)) == target
