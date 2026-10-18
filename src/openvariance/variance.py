"""Line-by-line variance analysis: actual vs budget, forecast, prior period or another scenario.

Conventions (documented in docs/methodology.md):

* ``delta``  = actual - baseline, in natural magnitudes.
* ``impact`` = delta x line direction. Positive is always favorable: revenue
  above budget is +, expenses above budget are -.
* ``pct``    = impact / |baseline|. Dividing by the absolute baseline keeps the
  sign meaningful when the baseline is negative (a loss that shrank is a
  favorable, positive percentage). It is ``None`` ("n/m") when the baseline is zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Any

from .accounts import PNL_LAYOUT, line
from .dataset import FinancialData
from .money import round_money


class Status(str, Enum):
    FAVORABLE = "F"
    UNFAVORABLE = "U"
    ON_PLAN = ""
    NO_DATA = "n/a"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """A line is material when its impact passes either threshold."""

    amount: Decimal = Decimal("10000")
    pct: Decimal = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class VarianceRow:
    line: str
    label: str
    depth: int
    is_total: bool
    actual: Decimal | None
    baseline: Decimal | None
    delta: Decimal | None
    impact: Decimal | None
    pct: Decimal | None
    status: Status
    material: bool
    department: str | None = None
    departments: tuple[VarianceRow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        out: dict[str, Any] = {
            "line": self.line,
            "label": self.label,
            "actual": num(self.actual),
            "baseline": num(self.baseline),
            "variance": num(self.impact),
            "variance_pct": None if self.pct is None else str(round(self.pct * 100, 2)),
            "status": self.status.value,
            "material": self.material,
        }
        if self.department:
            out["department"] = self.department
        if self.departments:
            out["departments"] = [d.to_dict() for d in self.departments]
        return out


@dataclass(frozen=True)
class VarianceReport:
    title: str
    actual_label: str
    baseline_label: str
    periods: tuple[str, ...]
    rows: tuple[VarianceRow, ...]
    thresholds: Thresholds = field(default_factory=Thresholds)

    def row(self, key: str) -> VarianceRow:
        for r in self.rows:
            if r.line == key:
                return r
        raise KeyError(f"{key!r} is not in this report")

    @property
    def headline(self) -> VarianceRow:
        """The bottom-line row to lead with: operating income if present, else the last total."""
        for key in ("operating_income", "net_income", "ebitda", "gross_profit", "revenue"):
            if any(r.line == key and r.actual is not None for r in self.rows):
                return self.row(key)
        return self.rows[-1]

    def material_rows(self) -> list[VarianceRow]:
        return [r for r in self.rows if r.material and not r.is_total]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "comparison": f"{self.actual_label} vs {self.baseline_label}",
            "periods": list(self.periods),
            "sign_convention": "variance > 0 is favorable; pct = variance / |baseline|",
            "thresholds": {"amount": str(self.thresholds.amount), "pct": str(self.thresholds.pct)},
            "rows": [r.to_dict() for r in self.rows],
        }


def pct_of_base(impact: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    """Percentage variance on an absolute base; None when the base is zero or missing."""
    if impact is None or baseline is None or baseline == 0:
        return None
    return impact / abs(baseline)


def _row(
    key: str,
    depth: int,
    actual: FinancialData,
    baseline: FinancialData,
    periods: Sequence[str],
    thresholds: Thresholds,
    department: str | None = None,
) -> VarianceRow:
    spec = line(key)
    a = actual.value(key, periods, department)
    b = baseline.value(key, periods, department)
    if a is None and b is None:
        delta = impact = pct = None
        status = Status.NO_DATA
    else:
        a0 = a if a is not None else Decimal(0)
        b0 = b if b is not None else Decimal(0)
        delta = a0 - b0
        impact = delta * spec.direction
        pct = pct_of_base(impact, b)
        if round_money(impact) == 0:
            status = Status.ON_PLAN
        else:
            status = Status.FAVORABLE if impact > 0 else Status.UNFAVORABLE
    material = impact is not None and (
        abs(impact) >= thresholds.amount or (pct is not None and abs(pct) >= thresholds.pct)
    )
    material = material and status in (Status.FAVORABLE, Status.UNFAVORABLE)
    return VarianceRow(
        line=key,
        label=spec.label,
        depth=depth,
        is_total=spec.is_computed,
        actual=a,
        baseline=b,
        delta=delta,
        impact=impact,
        pct=pct,
        status=status,
        material=material,
        department=department,
    )


def compare(
    actual: FinancialData,
    baseline: FinancialData,
    periods: Sequence[str],
    *,
    actual_label: str = "Actual",
    baseline_label: str = "Budget",
    title: str | None = None,
    thresholds: Thresholds | None = None,
    layout: Sequence[tuple[str, int]] = PNL_LAYOUT,
    keep_empty: bool = False,
) -> VarianceReport:
    """Compare two datasets line by line over ``periods`` (summed for flows).

    Lines with no data on either side are dropped unless ``keep_empty`` is set.
    Lines that have department detail get a department drill-down.
    """
    if not periods:
        raise ValueError("Choose at least one period to compare")
    thresholds = thresholds or Thresholds()
    rows: list[VarianceRow] = []
    for key, depth in layout:
        row = _row(key, depth, actual, baseline, periods, thresholds)
        if row.status is Status.NO_DATA and not keep_empty:
            continue
        depts = sorted(set(actual.departments(key)) | set(baseline.departments(key)))
        if depts and not line(key).is_computed:
            detail = tuple(
                _row(key, depth + 1, actual, baseline, periods, thresholds, d) for d in depts
            )
            row = replace(row, departments=detail)
        rows.append(row)
    if not rows:
        raise ValueError(
            f"No overlapping data for periods {list(periods)}. "
            f"Actual has {actual.periods[:1]}..{actual.periods[-1:]}, "
            f"baseline has {baseline.periods[:1]}..{baseline.periods[-1:]}."
        )
    span = periods[0] if len(periods) == 1 else f"{periods[0]} to {periods[-1]}"
    return VarianceReport(
        title=title or f"{actual.name}: {actual_label} vs {baseline_label}, {span}",
        actual_label=actual_label,
        baseline_label=baseline_label,
        periods=tuple(periods),
        rows=tuple(rows),
        thresholds=thresholds,
    )
