from __future__ import annotations

from decimal import Decimal

import pytest

from openvariance import FinancialData, Thresholds, compare
from openvariance.variance import Status, pct_of_base


def data(name: str, rows: list[tuple[str, str, str] | tuple[str, str, str, str]]) -> FinancialData:
    return FinancialData.from_records(name, rows)


ACTUAL = data(
    "Co",
    [
        ("2026-01", "revenue", "1100"),
        ("2026-01", "cogs", "450"),
        ("2026-01", "payroll", "300", "Eng"),
        ("2026-01", "payroll", "120", "Sales"),
        ("2026-01", "software", "0"),
    ],
)
BUDGET = data(
    "Co",
    [
        ("2026-01", "revenue", "1000"),
        ("2026-01", "cogs", "400"),
        ("2026-01", "payroll", "280", "Eng"),
        ("2026-01", "payroll", "120", "Sales"),
        ("2026-01", "software", "50"),
    ],
)


def test_variance_is_favorable_signed() -> None:
    rep = compare(ACTUAL, BUDGET, ["2026-01"], thresholds=Thresholds(Decimal(1), Decimal("0.5")))
    assert rep.row("revenue").impact == 100 and rep.row("revenue").status is Status.FAVORABLE
    assert rep.row("cogs").impact == -50 and rep.row("cogs").status is Status.UNFAVORABLE
    assert rep.row("software").impact == 50 and rep.row("software").status is Status.FAVORABLE


def test_totals_are_derived_from_components() -> None:
    rep = compare(ACTUAL, BUDGET, ["2026-01"])
    gp = rep.row("gross_profit")
    assert (gp.actual, gp.baseline, gp.impact) == (650, 600, 50)
    oi = rep.row("operating_income")
    assert oi.actual == 1100 - 450 - 420 - 0
    assert oi.impact == sum(
        r.impact for r in rep.rows if r.line in ("revenue", "cogs", "payroll", "software")
    )


def test_department_drilldown_sums_to_line() -> None:
    rep = compare(ACTUAL, BUDGET, ["2026-01"])
    payroll = rep.row("payroll")
    assert [d.department for d in payroll.departments] == ["Eng", "Sales"]
    assert sum(d.impact for d in payroll.departments) == payroll.impact == -20


def test_percent_uses_absolute_base_and_handles_zero() -> None:
    assert pct_of_base(Decimal(50), Decimal(-200)) == Decimal("0.25")
    assert pct_of_base(Decimal(10), Decimal(0)) is None
    assert pct_of_base(None, Decimal(5)) is None


def test_loss_that_shrinks_is_favorable() -> None:
    a = data("Co", [("2026-01", "revenue", "100"), ("2026-01", "payroll", "150")])
    b = data("Co", [("2026-01", "revenue", "100"), ("2026-01", "payroll", "200")])
    oi = compare(a, b, ["2026-01"]).row("operating_income")
    assert oi.actual == -50 and oi.baseline == -100
    assert oi.impact == 50 and oi.pct == Decimal("0.5") and oi.status is Status.FAVORABLE


def test_materiality_thresholds() -> None:
    rep = compare(ACTUAL, BUDGET, ["2026-01"], thresholds=Thresholds(Decimal(60), Decimal("0.2")))
    flagged = {r.line for r in rep.material_rows()}
    assert flagged == {"revenue", "software"}  # $100 passes amount; software is -100%


def test_missing_data_is_explicit() -> None:
    a = data("Co", [("2026-01", "revenue", "10")])
    b = data("Co", [("2026-01", "revenue", "10"), ("2026-01", "travel", "5")])
    rep = compare(a, b, ["2026-01"])
    travel = rep.row("travel")
    assert travel.actual is None and travel.baseline == 5 and travel.impact == 5
    assert "marketing" not in {r.line for r in rep.rows}


def test_stocks_take_the_last_period_and_flows_sum() -> None:
    d = data(
        "Co",
        [
            ("2026-01", "cash", "10"),
            ("2026-02", "cash", "25"),
            ("2026-01", "revenue", "3"),
            ("2026-02", "revenue", "4"),
        ],
    )
    assert d.value("cash", ["2026-01", "2026-02"]) == 25
    assert d.value("revenue", ["2026-01", "2026-02"]) == 7


def test_computed_lines_cannot_be_stored() -> None:
    with pytest.raises(ValueError, match="computed"):
        data("Co", [("2026-01", "gross_profit", "1")])


def test_no_overlap_is_an_error() -> None:
    with pytest.raises(ValueError):
        compare(ACTUAL, BUDGET, ["2030-01"])
