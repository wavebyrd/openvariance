from __future__ import annotations

import random
from decimal import Decimal
from fractions import Fraction
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from openvariance.attribution import build_bridge, shapley


def revenue(inputs: Any) -> Fraction:
    return inputs["price"] * inputs["units"]


def test_price_volume_split_matches_the_textbook_midpoint() -> None:
    base = {"price": Fraction(10), "units": Fraction(100)}
    actual = {"price": Fraction(12), "units": Fraction(110)}
    result = shapley(revenue, base, actual, {"Price": ["price"], "Volume": ["units"]})
    # Shapley for two drivers = average of both orderings = midpoint method.
    assert result == {"Price": Fraction(210), "Volume": Fraction(110)}


def test_order_of_groups_does_not_matter() -> None:
    def metric(x: Any) -> Fraction:
        return x["a"] * x["b"] - x["c"] * x["a"] + x["d"]

    base = {k: Fraction(v) for k, v in dict(a=3, b=7, c=2, d=5).items()}
    actual = {k: Fraction(v) for k, v in dict(a=4, b=6, c=5, d=1).items()}
    keys = list(base)
    one = shapley(metric, base, actual, {k: [k] for k in keys})
    random.Random(1).shuffle(keys)
    two = shapley(metric, base, actual, {k: [k] for k in keys})
    assert one == two


def test_symmetric_drivers_get_equal_credit() -> None:
    base = {"x": Fraction(1), "y": Fraction(1)}
    actual = {"x": Fraction(3), "y": Fraction(3)}
    result = shapley(lambda v: v["x"] * v["y"], base, actual, {"X": ["x"], "Y": ["y"]})
    assert result["X"] == result["Y"] == 4


values = st.fractions(min_value=-1000, max_value=1000, max_denominator=50)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(values, values), min_size=2, max_size=6))
def test_contributions_always_reconcile(pairs: list[tuple[Fraction, Fraction]]) -> None:
    keys = [f"k{i}" for i in range(len(pairs))]
    base = {k: b for k, (b, _) in zip(keys, pairs, strict=True)}
    actual = {k: a for k, (_, a) in zip(keys, pairs, strict=True)}

    def metric(x: Any) -> Fraction:
        total = Fraction(0)
        for i, k in enumerate(keys):
            total += x[k] * (i + 1)
            if i:
                total += x[k] * x[keys[i - 1]] / 7
        return total

    exact = shapley(metric, base, actual, {k: [k] for k in keys})
    assert sum(exact.values()) == metric(actual) - metric(base)
    bridge = build_bridge(
        metric, base, actual, {k: [k] for k in keys}, metric_label="M", drop_zero=False
    )
    assert bridge.reconciles and bridge.residual == 0
    assert bridge.condensed(3).reconciles


def test_bridge_rounds_to_cents_and_still_ties() -> None:
    base = {"a": Fraction(1, 3), "b": Fraction(1, 3), "c": Fraction(1, 3)}
    actual = {"a": Fraction(2, 3), "b": Fraction(2, 3), "c": Fraction(2, 3)}
    bridge = build_bridge(
        lambda x: (x["a"] + x["b"] + x["c"]) * 100,
        base,
        actual,
        {"A": ["a"], "B": ["b"], "C": ["c"]},
        metric_label="M",
    )
    assert bridge.start == Decimal("100.00") and bridge.end == Decimal("200.00")
    assert sorted(s.amount for s in bridge.steps) == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert bridge.reconciles
