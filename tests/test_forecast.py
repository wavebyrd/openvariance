from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from openvariance.forecast import (
    Assumptions,
    default_scenarios,
    forecast_periods,
    infer_assumptions,
    next_period,
    project,
    run_scenarios,
)

OPENING = {
    "revenue": Decimal(1000),
    "cogs": Decimal(400),
    "cash": Decimal(500),
    "accounts_receivable": Decimal(300),
    "inventory": Decimal(50),
    "other_current_assets": Decimal(20),
    "ppe_net": Decimal(800),
    "other_assets": Decimal(30),
    "accounts_payable": Decimal(100),
    "accrued_liabilities": Decimal(40),
    "deferred_revenue": Decimal(60),
    "debt": Decimal(400),
    "other_liabilities": Decimal(10),
}
OPENING["equity"] = sum(
    OPENING[k]
    for k in (
        "cash",
        "accounts_receivable",
        "inventory",
        "other_current_assets",
        "ppe_net",
        "other_assets",
    )
) - sum(
    OPENING[k]
    for k in (
        "accounts_payable",
        "accrued_liabilities",
        "deferred_revenue",
        "debt",
        "other_liabilities",
    )
)

BASE = Assumptions(
    revenue_growth=Decimal("0.03"),
    gross_margin=Decimal("0.6"),
    opex_pct_revenue=Decimal("0.35"),
    depreciation_rate=Decimal("0.02"),
    capex_pct_revenue=Decimal("0.05"),
    dso=Decimal(45),
    dio=Decimal(20),
    dpo=Decimal(30),
    tax_rate=Decimal("0.21"),
    interest_rate_annual=Decimal("0.08"),
    debt_change=Decimal(-5),
    dividends=Decimal(3),
)


def test_period_labels() -> None:
    assert next_period("2026-12") == "2027-01"
    assert next_period("2026-Q4") == "2027-Q1"
    assert forecast_periods("2026-Q3", 3) == ["2026-Q4", "2027-Q1", "2027-Q2"]


def test_model_balances_and_cash_ties() -> None:
    res = project(OPENING, BASE, forecast_periods("2026-Q4", 8), opening_period="2026-Q4")
    assert res.balanced, [c for c in res.checks if not c.passed]
    q1 = res.values["2027-Q1"]
    assert q1["revenue"] == Decimal(1000) * Decimal("1.03")
    assert q1["accounts_receivable"] == q1["revenue"] * 45 / (Decimal(365) / 4)
    assert q1["interest_expense"] == Decimal(400) * Decimal("0.08") / 4


rate = st.decimals(min_value=Decimal("-0.2"), max_value=Decimal("0.3"), places=3)


@settings(max_examples=40, deadline=None)
@given(
    rate,
    st.decimals(min_value=Decimal("0.05"), max_value=Decimal("0.95"), places=2),
    st.decimals(min_value=0, max_value=120, places=1),
    st.decimals(min_value=-50, max_value=50, places=2),
)
def test_balances_for_any_assumptions(
    growth: Decimal, margin: Decimal, days: Decimal, borrowing: Decimal
) -> None:
    a = BASE.with_changes(
        revenue_growth=growth, gross_margin=margin, dso=days, dpo=days, debt_change=borrowing
    )
    res = project(OPENING, a, forecast_periods("2026-12", 12), opening_period="2026-12")
    assert res.balanced


def test_an_unbalanced_opening_is_reported_not_plugged() -> None:
    bad = {**OPENING, "cash": OPENING["cash"] + 7}
    res = project(bad, BASE, forecast_periods("2026-Q4", 2), opening_period="2026-Q4")
    opening = res.checks[0]
    assert not opening.passed and opening.difference == 7
    assert all(c.passed for c in res.checks[1:])  # the forecast adds no new gap


def test_infer_assumptions_explains_sources(demo_data) -> None:  # type: ignore[no-untyped-def]
    inferred = infer_assumptions(demo_data.history)
    assert set(inferred.sources) == set(Assumptions.__dataclass_fields__)
    assert Decimal("0.75") < inferred.assumptions.gross_margin < Decimal("0.85")
    assert inferred.assumptions.dso == Decimal("36.0")


def test_scenarios_bracket_the_base(demo_data) -> None:  # type: ignore[no-untyped-def]
    base = infer_assumptions(demo_data.history).assumptions
    results = run_scenarios(demo_data.history, default_scenarios(base, 12), 12)
    ebitda = {r.scenario: sum(r.values[p]["ebitda"] for p in r.periods) for r in results}
    assert ebitda["Downside"] < ebitda["Base"] < ebitda["Upside"]
    assert all(r.balanced for r in results)


def test_history_must_have_revenue() -> None:
    from openvariance import FinancialData

    with pytest.raises(ValueError):
        infer_assumptions(FinancialData.from_records("x", [("2026-01", "cash", 1)]))


def test_revolver_funds_shortfalls_and_is_repaid() -> None:
    hungry = BASE.with_changes(capex_pct_revenue="0.9", minimum_cash=100)
    res = project(OPENING, hungry, forecast_periods("2026-Q4", 6), opening_period="2026-Q4")
    assert res.balanced
    assert all(res.values[p]["cash"] >= 100 - Decimal("0.01") for p in res.periods)
    assert res.values[res.periods[-1]]["revolver"] > 0
    assert "funding_note" in res.summary()
    # Back to normal capex: excess cash pays the revolver down before cash builds up.
    rested = project(
        {**OPENING, "revolver": Decimal(200), "cash": OPENING["cash"] + 200},
        BASE.with_changes(minimum_cash=100),
        forecast_periods("2026-Q4", 8),
        opening_period="2026-Q4",
    )
    first = rested.values[rested.periods[0]]["revolver"]
    assert first < 200 and rested.balanced
