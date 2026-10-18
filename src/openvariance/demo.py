"""Demo Co.: a fictional software company that ships with OpenVariance.

Use it to try every feature without your own data. Rebuild it with
``scripts/make_demo_data.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from .dataset import FinancialData
from .drivers import DriverSet, load_drivers, to_financial_data

COMPANY = "Demo Co."
REPORT_MONTH = "2026-09"


@dataclass(frozen=True)
class Demo:
    company: str
    budget_drivers: dict[str, DriverSet]
    actual_drivers: dict[str, DriverSet]
    budget: FinancialData
    actual: FinancialData
    history: FinancialData  # actual P&L plus month-end balance sheet and cash flows
    report_month: str


def load() -> Demo:
    root = resources.files("openvariance") / "data" / "demo"
    with resources.as_file(root / "drivers_budget.csv") as p:
        budget_drivers = load_drivers(p)
    with resources.as_file(root / "drivers_actual.csv") as p:
        actual_drivers = load_drivers(p)
    with resources.as_file(root / "balance_sheet.csv") as p:
        balances = FinancialData.from_csv(p, "Balance sheet")
    budget = to_financial_data(budget_drivers, COMPANY, {"scenario": "Budget"})
    actual = to_financial_data(actual_drivers, COMPANY, {"scenario": "Actual"})
    history = actual.merged(balances.filter([p for p in balances.periods if p in actual.periods]))
    return Demo(COMPANY, budget_drivers, actual_drivers, budget, actual, history, REPORT_MONTH)
