"""Driver attribution: split a change in a metric into the drivers that caused it.

When drivers interact (price x volume, headcount x pay rate), there is no
single right way to assign the cross term. Sequential substitution gives a
different answer for every ordering. OpenVariance uses the Shapley value
instead: each driver's contribution is its average marginal effect over every
order in which the drivers could be switched from baseline to actual. It has
three properties a finance team can rely on:

* Exact reconciliation: contributions always sum to the total change.
* Order independence: no arbitrary choice of which driver goes first.
* Symmetry: two drivers with identical effects get identical credit.

The math runs in exact rational arithmetic. Contributions are rounded to cents
with the largest-remainder method, so the rounded bridge still ties to the
rounded total. See docs/methodology.md for a worked example.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from math import factorial
from typing import Any

from .money import allocate, round_money, to_decimal

Inputs = Mapping[str, Any]
Metric = Callable[[Inputs], Decimal | Fraction]

MAX_DRIVER_GROUPS = 16  # 2^16 metric evaluations; keeps attribution instant


@dataclass(frozen=True, slots=True)
class BridgeStep:
    label: str
    amount: Decimal
    detail: str = ""


@dataclass(frozen=True)
class Bridge:
    """A walk from a starting value to an ending value through driver steps."""

    metric: str
    start_label: str
    end_label: str
    start: Decimal
    end: Decimal
    steps: tuple[BridgeStep, ...]
    method: str = "shapley"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def change(self) -> Decimal:
        return self.end - self.start

    @property
    def residual(self) -> Decimal:
        """Unexplained difference. Always zero for a Shapley bridge; shown so it can be checked."""
        return self.end - self.start - sum((s.amount for s in self.steps), Decimal(0))

    @property
    def reconciles(self) -> bool:
        return self.residual == 0

    def condensed(self, max_steps: int = 7, label: str = "All other") -> Bridge:
        """The largest steps, with the rest combined into one, for charts.

        Combining is exact, so the condensed bridge reconciles whenever the original does.
        """
        if len(self.steps) <= max_steps:
            return self
        keep = {id(s) for s in self.ranked()[: max_steps - 1]}
        kept = [s for s in self.steps if id(s) in keep]
        rest = [s for s in self.steps if id(s) not in keep]
        other = BridgeStep(
            label, sum((s.amount for s in rest), Decimal(0)), ", ".join(s.label for s in rest)
        )
        return replace(self, steps=(*kept, other))

    def ranked(self) -> list[BridgeStep]:
        return sorted(self.steps, key=lambda s: abs(s.amount), reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "method": self.method,
            "start": {"label": self.start_label, "value": str(self.start)},
            "steps": [
                {"driver": s.label, "impact": str(s.amount), "detail": s.detail} for s in self.steps
            ],
            "end": {"label": self.end_label, "value": str(self.end)},
            "change": str(self.change),
            "residual": str(self.residual),
            "reconciles": self.reconciles,
            "notes": list(self.notes),
        }


def shapley(
    metric: Metric,
    baseline: Inputs,
    actual: Inputs,
    groups: Mapping[str, Sequence[str]],
) -> dict[str, Fraction]:
    """Exact Shapley contribution of each driver group to ``metric(actual) - metric(baseline)``.

    ``groups`` maps a driver label to the input keys it switches together, so
    "Headcount" can move every department's headcount at once. Inputs not
    named in any group are held at their baseline values.
    """
    labels = list(groups)
    n = len(labels)
    if n == 0:
        raise ValueError("Give at least one driver group")
    if n > MAX_DRIVER_GROUPS:
        raise ValueError(f"At most {MAX_DRIVER_GROUPS} driver groups; got {n}. Combine some.")
    named = [key for keys in groups.values() for key in keys]
    if len(named) != len(set(named)):
        raise ValueError("Each input may belong to only one driver group")
    unknown = sorted(set(named) - set(baseline) - set(actual))
    if unknown:
        raise ValueError(f"Driver groups name inputs that do not exist: {unknown}")

    cache: dict[frozenset[int], Fraction] = {}

    def value(coalition: frozenset[int]) -> Fraction:
        if coalition not in cache:
            inputs = dict(baseline)
            for i in coalition:
                for key in groups[labels[i]]:
                    inputs[key] = actual[key]
            cache[coalition] = Fraction(metric(inputs))
        return cache[coalition]

    weights = [Fraction(factorial(k) * factorial(n - k - 1), factorial(n)) for k in range(n)]
    result: dict[str, Fraction] = {}
    for i, label in enumerate(labels):
        others = [j for j in range(n) if j != i]
        total = Fraction(0)
        for k in range(n):
            for subset in combinations(others, k):
                s = frozenset(subset)
                total += weights[k] * (value(s | {i}) - value(s))
        result[label] = total
    return result


def build_bridge(
    metric: Metric,
    baseline: Inputs,
    actual: Inputs,
    groups: Mapping[str, Sequence[str]],
    *,
    metric_label: str,
    start_label: str = "Budget",
    end_label: str = "Actual",
    details: Mapping[str, str] | None = None,
    drop_zero: bool = True,
) -> Bridge:
    """Shapley attribution packaged as a reconciling bridge, rounded to cents."""
    start = to_decimal(metric(baseline))
    end = to_decimal(metric(actual))
    exact = shapley(metric, baseline, actual, groups)
    labels = list(exact)
    start_r, end_r = round_money(start), round_money(end)
    parts = allocate([exact[k] for k in labels], total=end_r - start_r)
    steps = tuple(
        BridgeStep(label, amount, (details or {}).get(label, ""))
        for label, amount in zip(labels, parts, strict=True)
        if not (drop_zero and amount == 0)
    )
    return Bridge(metric_label, start_label, end_label, start_r, end_r, steps)


def line_bridge(
    rows: Sequence[tuple[str, Decimal]],
    *,
    metric_label: str,
    start: Decimal,
    end: Decimal,
    start_label: str = "Budget",
    end_label: str = "Actual",
) -> Bridge:
    """A bridge built from line-item variances when no driver data is available.

    ``rows`` are (label, favorable-signed impact). This explains *where* the
    change landed (which lines), not *why* (which drivers), and says so in its notes.
    """
    steps = tuple(BridgeStep(label, amount) for label, amount in rows if amount != 0)
    return Bridge(
        metric_label,
        start_label,
        end_label,
        start,
        end,
        steps,
        method="line_items",
        notes=("Built from line-item variances. Add driver data to see causes, not just lines.",),
    )
