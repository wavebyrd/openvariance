"""Generate the Demo Co. dataset shipped with OpenVariance.

Demo Co. is fictional: a B2B software company with three subscription plans
and five departments. Running this script rebuilds every demo file from fixed
inputs, so the data is reproducible and reviewable:

    python scripts/make_demo_data.py

Outputs
  src/openvariance/data/demo/drivers_budget.csv   FY2026 budget drivers, Jan-Dec
  src/openvariance/data/demo/drivers_actual.csv   FY2026 actual drivers, Jan-Sep
  src/openvariance/data/demo/balance_sheet.csv    actual month-end balances and cash flows
  examples/demo_pl_quickbooks_2026.csv            the actual P&L as a messy QuickBooks-style export
  examples/demo_budget_2026.xlsx                  the budget as a typical FP&A spreadsheet

The story in September 2026: two engineers start a month early, a new analytics
tool lands outside the budget, Pro plan volume beats plan, and Enterprise deals
close at a deeper discount.
"""

from __future__ import annotations

import csv
import random
import sys
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openvariance.drivers import DriverModel, DriverSet, save_drivers  # noqa: E402

DEMO = ROOT / "src" / "openvariance" / "data" / "demo"
EXAMPLES = ROOT / "examples"
MONTHS = [f"2026-{m:02d}" for m in range(1, 13)]
F = Fraction

PLANS = {  # units in Jan, monthly unit growth, price, unit cost
    "Starter": (5_500, F(9, 1000), F(99), F(22)),
    "Pro": (3_820, F(1, 100), F(499), F(95)),
    "Enterprise": (468, F(12, 1000), F(4150), F(820)),
}
DEPARTMENTS = {  # Jan headcount, hires by month index, monthly loaded cost
    "Engineering": (42, {2: 2, 4: 1, 6: 1, 8: 2, 10: 2}, F(16_500)),
    "Sales": (26, {3: 1, 5: 1, 7: 2, 10: 1}, F(14_000)),
    "Marketing": (9, {6: 1}, F(13_000)),
    "Customer Success": (17, {3: 1, 5: 1, 8: 1, 11: 1}, F(9_500)),
    "General and Administrative": (13, {7: 1}, F(12_500)),
}
FIXED = {
    "software": F(205_000),
    "marketing": F(310_000),
    "rent_facilities": F(145_000),
    "professional_fees": F(60_000),
    "travel": F(52_000),
    "other_opex": F(68_000),
    "depreciation_amortization": F(85_000),
    "interest_expense": F(14_600),
}
TAX_RATE = F(24, 100)


def money(x: Fraction) -> Fraction:
    """Round to whole dollars, like a real budget."""
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return F(int(d.quantize(Decimal(1), ROUND_HALF_UP)))


def budget() -> dict[str, DriverSet]:
    out = {}
    for i, period in enumerate(MONTHS):
        units = {p: F(round(u0 * (1 + g) ** i)) for p, (u0, g, _, _) in PLANS.items()}
        heads = {}
        for d, (h0, hires, _) in DEPARTMENTS.items():
            heads[d] = F(h0 + sum(n for m, n in hires.items() if m <= i))
        fixed = {
            k: (v * (1 + F(i, 200)) if k in ("marketing", "software") else v)
            for k, v in FIXED.items()
        }
        fixed = {k: money(v) for k, v in fixed.items()}
        s = DriverSet(
            units=units,
            price={p: v[2] for p, v in PLANS.items()},
            unit_cost={p: v[3] for p, v in PLANS.items()},
            headcount=heads,
            cost_per_head={d: v[2] for d, v in DEPARTMENTS.items()},
            fixed=fixed,
        )
        out[period] = with_tax(s)
    return out


def with_tax(s: DriverSet) -> DriverSet:
    pnl = DriverModel.pnl(DriverModel.inputs(s))
    pretax = (
        pnl["revenue"]
        - pnl["cogs"]
        - pnl["payroll"]
        - sum(v for k, v in s.fixed.items() if k != "income_tax")
    )
    fixed = dict(s.fixed)
    fixed["income_tax"] = money(max(pretax, F(0)) * TAX_RATE)
    return DriverSet(s.units, s.price, s.unit_cost, s.headcount, s.cost_per_head, fixed)


def actual(plan: dict[str, DriverSet]) -> dict[str, DriverSet]:
    rng = random.Random(2026)
    out = {}
    for period in MONTHS[:9]:
        b = plan[period]

        def wiggle(x: Fraction, pct: float) -> Fraction:
            return x * F(Decimal(str(round(1 + rng.uniform(-pct, pct), 4))))

        units = {p: F(round(wiggle(u, 0.02))) for p, u in b.units.items()}
        price = dict(b.price)
        unit_cost = {p: money(wiggle(c, 0.02) * 100) / 100 for p, c in b.unit_cost.items()}
        heads = dict(b.headcount)
        rates = {d: money(wiggle(r, 0.01)) for d, r in b.cost_per_head.items()}
        fixed = {
            k: (money(wiggle(v, 0.05)) if k != "income_tax" else v) for k, v in b.fixed.items()
        }
        if period == "2026-09":
            units["Pro"] = b.units["Pro"] + 160  # Pro volume beats plan
            units["Enterprise"] = b.units["Enterprise"] - 6
            price["Enterprise"] = F(3_995)  # deeper Enterprise discounting
            heads["Engineering"] = b.headcount["Engineering"] + 2  # October hires started early
            fixed["software"] = b.fixed["software"] + 90_000  # unbudgeted analytics tool
            rates = {d: r for d, r in b.cost_per_head.items()}
            rates["Engineering"] = F(16_650)
        s = DriverSet(units, price, unit_cost, heads, rates, fixed)
        out[period] = with_tax(s)
    return out


def balance_sheet(act: dict[str, DriverSet]) -> list[tuple[str, str, Fraction]]:
    bal = {
        "cash": F(8_200_000),
        "accounts_receivable": F(5_650_000),
        "inventory": F(0),
        "other_current_assets": F(450_000),
        "ppe_net": F(3_400_000),
        "other_assets": F(600_000),
        "accounts_payable": F(640_000),
        "accrued_liabilities": F(1_350_000),
        "deferred_revenue": F(2_400_000),
        "debt": F(2_500_000),
        "other_liabilities": F(300_000),
    }
    bal["equity"] = sum(
        bal[k]
        for k in (
            "cash",
            "accounts_receivable",
            "inventory",
            "other_current_assets",
            "ppe_net",
            "other_assets",
        )
    ) - sum(
        bal[k]
        for k in (
            "accounts_payable",
            "accrued_liabilities",
            "deferred_revenue",
            "debt",
            "other_liabilities",
        )
    )
    rows = [("2025-12", k, v) for k, v in bal.items()]
    days = F(365, 12)
    for period, s in act.items():
        pnl = DriverModel.pnl(DriverModel.inputs(s))
        expenses = pnl["cogs"] + pnl["payroll"] + sum(s.fixed.values())
        net_income = pnl["revenue"] - expenses
        da = s.fixed["depreciation_amortization"]
        ar = money(pnl["revenue"] * 36 / days)
        ap = money(pnl["cogs"] * 21 / days)
        capex = F(120_000)
        repay = F(20_000)
        cfo = net_income + da - (ar - bal["accounts_receivable"]) + (ap - bal["accounts_payable"])
        bal.update(
            accounts_receivable=ar,
            accounts_payable=ap,
            ppe_net=bal["ppe_net"] + capex - da,
            debt=bal["debt"] - repay,
            equity=bal["equity"] + net_income,
        )
        bal["cash"] = bal["cash"] + cfo - capex - repay
        rows += [(period, k, v) for k, v in bal.items()]
        rows += [
            (period, "operating_cash_flow", cfo),
            (period, "capital_expenditures", capex),
            (period, "investing_cash_flow", -capex),
            (period, "financing_cash_flow", -repay),
            (period, "net_change_in_cash", cfo - capex - repay),
        ]
    return rows


def dec(x: Fraction) -> str:
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return str(d.quantize(Decimal("0.01"), ROUND_HALF_UP))


def quickbooks_export(act: dict[str, DriverSet]) -> None:
    """The actual P&L the way QuickBooks Online exports it: titles, sections, totals."""
    months = list(act)
    names = {
        m: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][i] + " 2026"
        for i, m in enumerate(months)
    }
    lines: list[tuple[str, dict[str, Fraction] | None]] = []

    def row(label: str, f) -> None:  # type: ignore[no-untyped-def]
        lines.append((label, {m: f(act[m]) for m in months}))

    def pnl(m: DriverSet) -> dict[str, Fraction]:
        return DriverModel.pnl(DriverModel.inputs(m))

    lines.append(("Income", None))
    for plan, number in (("Starter", 4010), ("Pro", 4020), ("Enterprise", 4030)):
        row(f"   {number} Subscription Revenue - {plan}", lambda s, p=plan: s.units[p] * s.price[p])
    row("Total Income", lambda s: pnl(s)["revenue"])
    lines.append(("Cost of Goods Sold", None))
    row("   5010 Hosting and Infrastructure", lambda s: pnl(s)["cogs"] * F(7, 10))
    row("   5020 Customer Support Tools", lambda s: pnl(s)["cogs"] * F(3, 10))
    row("Total Cost of Goods Sold", lambda s: pnl(s)["cogs"])
    row("Gross Profit", lambda s: pnl(s)["revenue"] - pnl(s)["cogs"])
    lines.append(("Expenses", None))
    lines.append(("   Payroll Expenses", None))
    for d, number in (
        ("Engineering", 6110),
        ("Sales", 6120),
        ("Marketing", 6130),
        ("Customer Success", 6140),
        ("General and Administrative", 6150),
    ):
        row(
            f"      {number} Salaries & Wages - {d}",
            lambda s, d=d: s.headcount[d] * s.cost_per_head[d],
        )
    row("   Total Payroll Expenses", lambda s: pnl(s)["payroll"])
    for key, label in (
        ("software", "6200 Software Subscriptions"),
        ("marketing", "6300 Advertising & Marketing"),
        ("rent_facilities", "6400 Rent & Office"),
        ("professional_fees", "6500 Legal & Accounting"),
        ("travel", "6600 Travel & Meals"),
        ("other_opex", "6900 Office Supplies & Misc"),
        ("depreciation_amortization", "6950 Depreciation Expense"),
    ):
        row(f"   {label}", lambda s, k=key: s.fixed[k])
    row(
        "Total Expenses",
        lambda s: (
            pnl(s)["payroll"]
            + sum(v for k, v in s.fixed.items() if k not in ("interest_expense", "income_tax"))
        ),
    )
    row(
        "Net Operating Income",
        lambda s: (
            pnl(s)["revenue"]
            - pnl(s)["cogs"]
            - pnl(s)["payroll"]
            - sum(v for k, v in s.fixed.items() if k not in ("interest_expense", "income_tax"))
        ),
    )
    lines.append(("Other Expenses", None))
    row("   8100 Interest Expense", lambda s: s.fixed["interest_expense"])
    row("   8200 Income Tax Expense", lambda s: s.fixed["income_tax"])
    row("Total Other Expenses", lambda s: s.fixed["interest_expense"] + s.fixed["income_tax"])
    row(
        "Net Income",
        lambda s: pnl(s)["revenue"] - pnl(s)["cogs"] - pnl(s)["payroll"] - sum(s.fixed.values()),
    )

    def fmt(x: Fraction) -> str:
        d = (Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        text = f"{abs(d):,.2f}"
        return f"({text})" if d < 0 else text

    with open(EXAMPLES / "demo_pl_quickbooks_2026.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Demo Co."])
        w.writerow(["Profit and Loss"])
        w.writerow(["January - September, 2026"])
        w.writerow([])
        w.writerow(["", *(names[m] for m in months), "Total"])
        for label, values in lines:
            if values is None:
                w.writerow([label, *([""] * (len(months) + 1))])
            else:
                w.writerow(
                    [label, *(fmt(values[m]) for m in months), fmt(sum(values.values(), F(0)))]
                )
        w.writerow([])
        w.writerow(["Accrual Basis  Saturday, October 3, 2026 09:14 AM GMT-07:00"])


def budget_workbook(plan: dict[str, DriverSet]) -> None:
    """The budget as an FP&A analyst would lay it out: one row per account, months across."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "FY26 Budget"
    ws.append(["Demo Co. - FY2026 Operating Budget"])
    ws.append(["Approved by the board, December 2025"])
    ws.append([])
    ws.append(
        [
            "Account",
            *(
                f"{['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][i]}-26"
                for i in range(12)
            ),
            "FY2026",
        ]
    )
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="217346")

    def pnl(s: DriverSet) -> dict[str, Fraction]:
        return DriverModel.pnl(DriverModel.inputs(s))

    def add(label: str, fn) -> None:  # type: ignore[no-untyped-def]
        vals = [float(dec(fn(plan[m]))) for m in MONTHS]
        ws.append([label, *vals, sum(vals)])

    add("Revenue - Subscriptions", lambda s: pnl(s)["revenue"])
    add("Cost of Revenue - Hosting & Support", lambda s: pnl(s)["cogs"])
    for d in DEPARTMENTS:
        add(f"Salaries - {d}", lambda s, d=d: s.headcount[d] * s.cost_per_head[d])
    for key, label in (
        ("software", "Software & SaaS Tools"),
        ("marketing", "Marketing Programs"),
        ("rent_facilities", "Rent & Facilities"),
        ("professional_fees", "Professional Fees"),
        ("travel", "Travel & Entertainment"),
        ("other_opex", "Other G&A"),
        ("depreciation_amortization", "Depreciation"),
        ("interest_expense", "Interest Expense"),
        ("income_tax", "Income Tax Provision"),
    ):
        add(label, lambda s, k=key: s.fixed[k])
    for row in ws.iter_rows(min_row=5, min_col=2):
        for c in row:
            c.number_format = "#,##0;(#,##0)"
    ws.column_dimensions["A"].width = 38
    wb.save(EXAMPLES / "demo_budget_2026.xlsx")


def main() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    EXAMPLES.mkdir(exist_ok=True)
    plan = budget()
    act = actual(plan)
    save_drivers(plan, DEMO / "drivers_budget.csv")
    save_drivers(act, DEMO / "drivers_actual.csv")
    with open(DEMO / "balance_sheet.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "line", "amount", "department"])
        for period, key, value in balance_sheet(act):
            w.writerow([period, key, dec(value), ""])
    quickbooks_export(act)
    budget_workbook(plan)
    print("Demo data written to", DEMO.relative_to(ROOT), "and", EXAMPLES.relative_to(ROOT))


if __name__ == "__main__":
    main()
