"""Operating driver model: the "why" behind a private company's P&L.

A :class:`DriverSet` describes one period in operational terms (units sold,
prices, headcount, pay rates, fixed spend). :func:`pnl` turns drivers into
P&L lines, and :func:`explain` attributes the change in any P&L metric
between two driver sets to its causes with a reconciling Shapley bridge.

Revenue is decomposed the standard FP&A way:

    revenue = total units x sum(mix share_i x price_i)

so a change in revenue splits cleanly into volume (more units), mix (a
different blend of products) and price (different prices for the same blend).
Payroll splits into headcount and pay rate the same way.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .accounts import LINES, line
from .attribution import Bridge, build_bridge
from .dataset import Entry, FinancialData
from .money import to_decimal

VARIABLE_DRIVERS = ("units", "price", "unit_cost", "headcount", "cost_per_head")


@dataclass(frozen=True)
class DriverSet:
    """Drivers for one period, or an exact aggregate of several periods.

    ``headcount`` is in FTE-months, so a quarter with 10 people is 30, and
    ``cost_per_head`` is the fully loaded monthly cost per FTE.
    """

    units: Mapping[str, Fraction] = field(default_factory=dict)
    price: Mapping[str, Fraction] = field(default_factory=dict)
    unit_cost: Mapping[str, Fraction] = field(default_factory=dict)
    headcount: Mapping[str, Fraction] = field(default_factory=dict)
    cost_per_head: Mapping[str, Fraction] = field(default_factory=dict)
    fixed: Mapping[str, Fraction] = field(default_factory=dict)  # line key -> amount

    @property
    def products(self) -> list[str]:
        return sorted(set(self.units) | set(self.price) | set(self.unit_cost))

    @property
    def departments(self) -> list[str]:
        return sorted(set(self.headcount) | set(self.cost_per_head))

    @property
    def total_units(self) -> Fraction:
        return sum(self.units.values(), Fraction(0))

    @property
    def total_headcount(self) -> Fraction:
        return sum(self.headcount.values(), Fraction(0))


def aggregate(sets: Sequence[DriverSet]) -> DriverSet:
    """Combine periods so the model of the aggregate equals the sum of the periods' models.

    Units, headcount and fixed spend add up. Prices, unit costs and pay rates
    become weighted averages (total revenue / total units and so on), which is
    exactly what keeps revenue, cost of sales and payroll additive.
    """
    units: dict[str, Fraction] = defaultdict(Fraction)
    revenue: dict[str, Fraction] = defaultdict(Fraction)
    cogs: dict[str, Fraction] = defaultdict(Fraction)
    heads: dict[str, Fraction] = defaultdict(Fraction)
    payroll: dict[str, Fraction] = defaultdict(Fraction)
    fixed: dict[str, Fraction] = defaultdict(Fraction)
    for s in sets:
        for p in s.products:
            u = s.units.get(p, Fraction(0))
            units[p] += u
            revenue[p] += u * s.price.get(p, Fraction(0))
            cogs[p] += u * s.unit_cost.get(p, Fraction(0))
        for d in s.departments:
            h = s.headcount.get(d, Fraction(0))
            heads[d] += h
            payroll[d] += h * s.cost_per_head.get(d, Fraction(0))
        for key, amount in s.fixed.items():
            fixed[key] += amount

    def per(total: Mapping[str, Fraction], base: Mapping[str, Fraction]) -> dict[str, Fraction]:
        return {k: (total[k] / base[k] if base[k] else Fraction(0)) for k in base}

    return DriverSet(
        units=dict(units),
        price=per(revenue, units),
        unit_cost=per(cogs, units),
        headcount=dict(heads),
        cost_per_head=per(payroll, heads),
        fixed=dict(fixed),
    )


def to_inputs(drivers: DriverSet) -> dict[str, Any]:
    """Flatten a driver set into Shapley inputs (volume and mix separated)."""
    total = drivers.total_units
    mix = {
        p: (drivers.units.get(p, Fraction(0)) / total if total else Fraction(0))
        for p in drivers.products
    }
    out: dict[str, Any] = {
        "volume": total,
        "mix": mix,
        "price": dict(drivers.price),
        "unit_cost": dict(drivers.unit_cost),
        "headcount": dict(drivers.headcount),
        "cost_per_head": dict(drivers.cost_per_head),
    }
    for key, amount in drivers.fixed.items():
        out[f"fixed:{key}"] = amount
    return out


def pnl(inputs: Mapping[str, Any]) -> dict[str, Fraction]:
    """P&L leaf lines implied by Shapley inputs, in exact rational arithmetic."""
    volume: Fraction = inputs["volume"]
    mix: Mapping[str, Fraction] = inputs["mix"]
    price: Mapping[str, Fraction] = inputs["price"]
    unit_cost: Mapping[str, Fraction] = inputs["unit_cost"]
    heads: Mapping[str, Fraction] = inputs["headcount"]
    rate: Mapping[str, Fraction] = inputs["cost_per_head"]
    lines: dict[str, Fraction] = defaultdict(Fraction)
    for p, share in mix.items():
        units = volume * share
        lines["revenue"] += units * price.get(p, Fraction(0))
        lines["cogs"] += units * unit_cost.get(p, Fraction(0))
    for d, h in heads.items():
        lines["payroll"] += h * rate.get(d, Fraction(0))
    for key, amount in inputs.items():
        if key.startswith("fixed:"):
            lines[key.removeprefix("fixed:")] += amount
    return dict(lines)


def line_metric(key: str) -> Callable[[Mapping[str, Any]], Fraction]:
    """A function of inputs returning the exact value of any P&L line, computed or not."""

    def value(inputs: Mapping[str, Any]) -> Fraction:
        return _evaluate(key, pnl(inputs))

    return value


def _evaluate(key: str, leaves: Mapping[str, Fraction]) -> Fraction:
    spec = line(key)
    if not spec.is_computed:
        return leaves.get(key, Fraction(0))
    return sum((sign * _evaluate(c, leaves) for sign, c in spec.formula), Fraction(0))


def default_groups(baseline: DriverSet, actual: DriverSet) -> dict[str, list[str]]:
    """Driver groups for attribution: volume, mix, price, unit cost, headcount, pay rate, fixed."""
    groups: dict[str, list[str]] = {
        "Volume": ["volume"],
        "Mix": ["mix"],
        "Price": ["price"],
        "Unit cost": ["unit_cost"],
        "Headcount": ["headcount"],
        "Pay rate": ["cost_per_head"],
    }
    for key in sorted(set(baseline.fixed) | set(actual.fixed), key=list(LINES).index):
        groups[short_label(key)] = [f"fixed:{key}"]
    return groups


SHORT_LABELS = {
    "software": "Software",
    "marketing": "Marketing",
    "rent_facilities": "Facilities",
    "professional_fees": "Professional fees",
    "travel": "Travel",
    "other_opex": "Other opex",
    "depreciation_amortization": "D&A",
    "interest_expense": "Interest",
    "income_tax": "Income tax",
    "research_development": "R&D",
    "sales_marketing": "Sales and marketing",
    "general_administrative": "G&A",
    "selling_general_administrative": "SG&A",
    "other_income": "Other income",
}


def short_label(key: str) -> str:
    """Compact driver names for charts; falls back to the full line label."""
    return SHORT_LABELS.get(key, LINES[key].label)


def explain(
    baseline: DriverSet,
    actual: DriverSet,
    *,
    metric: str = "operating_income",
    start_label: str = "Budget",
    end_label: str = "Actual",
) -> Bridge:
    """Reconciling bridge from baseline to actual for one P&L metric, by driver."""
    base_in = to_inputs(baseline)
    act_in = to_inputs(actual)
    for key in ("mix", "price", "unit_cost", "headcount", "cost_per_head"):
        _align(base_in[key], act_in[key])
    for key in set(base_in) | set(act_in):
        if key.startswith("fixed:"):
            base_in.setdefault(key, Fraction(0))
            act_in.setdefault(key, Fraction(0))
    return build_bridge(
        line_metric(metric),
        base_in,
        act_in,
        default_groups(baseline, actual),
        metric_label=line(metric).label,
        start_label=start_label,
        end_label=end_label,
        details=_details(baseline, actual),
    )


def _align(a: dict[str, Fraction], b: dict[str, Fraction]) -> None:
    for k in set(a) | set(b):
        a.setdefault(k, Fraction(0))
        b.setdefault(k, Fraction(0))


def _fmt(value: Fraction, places: int = 0) -> str:
    return f"{float(value):,.{places}f}"


def _details(baseline: DriverSet, actual: DriverSet) -> dict[str, str]:
    def avg(
        ds: DriverSet, rates: Mapping[str, Fraction], weights: Mapping[str, Fraction]
    ) -> Fraction:
        total = sum(weights.values(), Fraction(0))
        if not total:
            return Fraction(0)
        return sum((weights[k] * rates.get(k, Fraction(0)) for k in weights), Fraction(0)) / total

    out = {
        "Volume": f"{_fmt(actual.total_units)} units vs {_fmt(baseline.total_units)}",
        "Price": "average price at actual mix: "
        f"${_fmt(avg(actual, actual.price, actual.units), 2)} vs "
        f"${_fmt(avg(actual, baseline.price, actual.units), 2)}",
        "Unit cost": "average unit cost at actual mix: "
        f"${_fmt(avg(actual, actual.unit_cost, actual.units), 2)} vs "
        f"${_fmt(avg(actual, baseline.unit_cost, actual.units), 2)}",
        "Headcount": f"{_fmt(actual.total_headcount)} vs {_fmt(baseline.total_headcount)} "
        "FTE-months",
        "Pay rate": "average monthly cost per FTE: "
        f"${_fmt(avg(actual, actual.cost_per_head, actual.headcount))} vs "
        f"${_fmt(avg(actual, baseline.cost_per_head, actual.headcount))}",
    }
    moved = []
    for d in sorted(set(actual.headcount) | set(baseline.headcount)):
        diff = actual.headcount.get(d, Fraction(0)) - baseline.headcount.get(d, Fraction(0))
        if diff:
            moved.append(f"{d} {'+' if diff > 0 else ''}{_fmt(diff)}")
    if moved:
        out["Headcount"] += f" ({', '.join(moved)})"
    shares = []
    for p in sorted(set(actual.units) | set(baseline.units)):
        a = actual.units.get(p, Fraction(0)) / actual.total_units if actual.total_units else 0
        b = baseline.units.get(p, Fraction(0)) / baseline.total_units if baseline.total_units else 0
        shares.append(f"{p} {_fmt(Fraction(a) * 100, 1)}% vs {_fmt(Fraction(b) * 100, 1)}%")
    out["Mix"] = "unit share: " + "; ".join(shares)
    for key in sorted(set(actual.fixed) | set(baseline.fixed)):
        out[short_label(key)] = (
            f"${_fmt(actual.fixed.get(key, Fraction(0)))} vs "
            f"${_fmt(baseline.fixed.get(key, Fraction(0)))}"
        )
    return out


def load_drivers(path: str | Path) -> dict[str, DriverSet]:
    """Read driver data in long format: ``period,driver,key,value``.

    ``driver`` is one of units, price, unit_cost (keyed by product), headcount,
    cost_per_head (keyed by department), or a P&L line key such as ``software``
    for fixed spend (key left blank).
    """
    raw: dict[str, dict[str, dict[str, Fraction]]] = defaultdict(lambda: defaultdict(dict))
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"period", "driver", "key", "value"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Driver file is missing columns: {sorted(missing)}")
        for n, row in enumerate(reader, start=2):
            driver = row["driver"].strip()
            if driver not in VARIABLE_DRIVERS and driver not in LINES:
                raise ValueError(f"Line {n}: unknown driver {driver!r}")
            if driver in LINES and LINES[driver].is_computed:
                raise ValueError(f"Line {n}: {driver!r} is a computed line, not a driver")
            value = Fraction(to_decimal(row["value"].strip()))
            raw[row["period"].strip()][driver][row["key"].strip()] = value
    sets: dict[str, DriverSet] = {}
    for period, drivers in sorted(raw.items()):
        fixed = {k: sum(v.values(), Fraction(0)) for k, v in drivers.items() if k in LINES}
        sets[period] = DriverSet(
            units=dict(drivers.get("units", {})),
            price=dict(drivers.get("price", {})),
            unit_cost=dict(drivers.get("unit_cost", {})),
            headcount=dict(drivers.get("headcount", {})),
            cost_per_head=dict(drivers.get("cost_per_head", {})),
            fixed=fixed,
        )
    return sets


def save_drivers(sets: Mapping[str, DriverSet], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["period", "driver", "key", "value"])
        for period, s in sorted(sets.items()):
            for driver in VARIABLE_DRIVERS:
                for key, value in sorted(getattr(s, driver).items()):
                    writer.writerow([period, driver, key, _dec(value)])
            for key, value in sorted(s.fixed.items()):
                writer.writerow([period, key, "", _dec(value)])


def _dec(value: Fraction) -> str:
    return str(to_decimal(value).normalize()) if value.denominator != 1 else str(value.numerator)


def to_financial_data(
    sets: Mapping[str, DriverSet], name: str, meta: dict[str, str] | None = None
) -> FinancialData:
    """The P&L implied by drivers, with payroll broken out by department."""
    entries: list[Entry] = []
    for period, s in sorted(sets.items()):
        lines = pnl(to_inputs(s))
        for key in ("revenue", "cogs"):
            if key in lines:
                entries.append(Entry(period, key, to_decimal(lines[key])))
        for d in s.departments:
            amount = s.headcount.get(d, Fraction(0)) * s.cost_per_head.get(d, Fraction(0))
            entries.append(Entry(period, "payroll", to_decimal(amount), d))
        for key, amount in sorted(s.fixed.items()):
            entries.append(Entry(period, key, to_decimal(amount)))
    return FinancialData(name, entries, dict(meta or {}))


def select(sets: Mapping[str, DriverSet], periods: Iterable[str]) -> DriverSet:
    chosen = [sets[p] for p in periods if p in sets]
    if not chosen:
        raise ValueError("No driver data for the chosen periods")
    return aggregate(chosen)
