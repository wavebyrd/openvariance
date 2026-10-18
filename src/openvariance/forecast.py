"""Driver-based three-statement forecast.

The model is deliberately simple and fully linked: assumptions drive the
income statement, working capital and capex drive the balance sheet, and the
cash flow statement is derived from the two. Cash is never a plug. It is the
opening balance plus the cash flow statement, and the model then *checks* that
assets equal liabilities plus equity in every period and reports any gap.

Per-period mechanics (documented in docs/methodology.md):

    revenue      = prior revenue x (1 + growth)
    cost of sales = revenue x (1 - gross margin)
    operating exp = revenue x opex % + fixed opex
    D&A          = opening PP&E x depreciation rate
    interest     = opening debt x annual rate x period length
    tax          = max(pretax income, 0) x tax rate
    receivables  = revenue x DSO / days in period
    inventory    = cost of sales x DIO / days in period
    payables     = cost of sales x DPO / days in period
    PP&E         = opening PP&E + capex - D&A
    equity       = opening equity + net income - dividends
    cash         = opening cash + operating + investing + financing cash flow
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from decimal import Decimal
from typing import Any

from .accounts import BALANCE_LAYOUT, LINES, Kind, Statement
from .checks import Check, balance_check, cash_check
from .dataset import Entry, FinancialData
from .money import ZERO, to_decimal

D = Decimal


@dataclass(frozen=True)
class Assumptions:
    """Forecast drivers. Rates are per period unless the name says annual."""

    revenue_growth: Decimal
    gross_margin: Decimal
    opex_pct_revenue: Decimal
    depreciation_rate: Decimal
    capex_pct_revenue: Decimal
    dso: Decimal
    dio: Decimal
    dpo: Decimal
    tax_rate: Decimal
    interest_rate_annual: Decimal
    fixed_opex: Decimal = ZERO
    debt_change: Decimal = ZERO
    dividends: Decimal = ZERO
    minimum_cash: Decimal = ZERO

    def with_changes(self, **changes: Any) -> Assumptions:
        return replace(self, **{k: to_decimal(v) for k, v in changes.items()})

    def to_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}


ASSUMPTION_LABELS: dict[str, str] = {
    "revenue_growth": "Revenue growth per period",
    "gross_margin": "Gross margin",
    "opex_pct_revenue": "Operating expenses, % of revenue",
    "fixed_opex": "Fixed operating expenses per period",
    "depreciation_rate": "Depreciation, % of opening PP&E per period",
    "capex_pct_revenue": "Capital expenditures, % of revenue",
    "dso": "Days sales outstanding",
    "dio": "Days inventory outstanding",
    "dpo": "Days payables outstanding",
    "tax_rate": "Tax rate",
    "interest_rate_annual": "Interest rate on debt (annual)",
    "debt_change": "Net borrowing (repayment) per period",
    "dividends": "Dividends per period",
    "minimum_cash": "Minimum cash (revolver draws below it)",
}

PERCENT_ASSUMPTIONS = {
    "revenue_growth",
    "gross_margin",
    "opex_pct_revenue",
    "depreciation_rate",
    "capex_pct_revenue",
    "tax_rate",
    "interest_rate_annual",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    assumptions: Assumptions
    description: str = ""


@dataclass
class ForecastResult:
    scenario: str
    opening_period: str
    periods: list[str]
    values: dict[str, dict[str, Decimal]]  # period -> line -> amount (leaf and computed)
    opening: dict[str, Decimal]
    assumptions: Assumptions
    checks: list[Check] = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return all(c.passed for c in self.checks)

    def value(self, key: str, period: str) -> Decimal:
        return self.values[period][key]

    def to_financial_data(self, name: str | None = None) -> FinancialData:
        entries = [
            Entry(p, key, amount)
            for p in self.periods
            for key, amount in self.values[p].items()
            if not LINES[key].is_computed
        ]
        return FinancialData(name or f"Forecast ({self.scenario})", entries)

    def summary(self) -> dict[str, Any]:
        last = self.periods[-1]
        flows = (
            "revenue",
            "gross_profit",
            "ebitda",
            "operating_income",
            "net_income",
            "free_cash_flow",
        )
        out: dict[str, Any] = {
            "scenario": self.scenario,
            "periods": [self.periods[0], last],
        }
        for key in flows:
            out[key] = str(sum((self.values[p][key] for p in self.periods), ZERO).quantize(D("1")))
        out["ending_cash"] = str(self.values[last]["cash"].quantize(D("1")))
        peak = max(self.values[p]["revolver"] for p in self.periods)
        out["peak_revolver"] = str(peak.quantize(D("1")))
        if peak > self.opening.get("revolver", ZERO):
            out["funding_note"] = (
                f"Cash needs exceed what the business generates; the revolver peaks at "
                f"{peak.quantize(D('1')):,} to keep cash at the minimum."
            )
        revenue = sum((self.values[p]["revenue"] for p in self.periods), ZERO)
        if revenue:
            ebitda = sum((self.values[p]["ebitda"] for p in self.periods), ZERO)
            out["ebitda_margin_pct"] = str((ebitda / revenue * 100).quantize(D("0.1")))
        out["checks_passed"] = self.balanced
        return out


def next_period(label: str) -> str:
    """``2026-09`` -> ``2026-10``; ``2026-Q4`` -> ``2027-Q1``."""
    year, rest = int(label[:4]), label[5:]
    if rest.startswith("Q"):
        q = int(rest[1:]) + 1
        return f"{year + (q > 4)}-Q{(q - 1) % 4 + 1}"
    month = int(rest) + 1
    return f"{year + (month > 12)}-{(month - 1) % 12 + 1:02d}"


def periods_per_year(label: str) -> int:
    return 4 if "Q" in label else 12


def opening_balances(history: FinancialData, period: str | None = None) -> dict[str, Decimal]:
    """Leaf balance sheet lines at a period (default: the latest), plus that period's revenue
    and cost of sales, which seed growth."""
    period = period or history.periods[-1]
    opening: dict[str, Decimal] = {}
    for key in BALANCE_LAYOUT:
        if LINES[key].is_computed:
            continue
        v = history.value(key, period)
        opening[key] = v if v is not None else ZERO
    for key in ("revenue", "cogs"):
        v = history.value(key, period)
        if v is None:
            raise ValueError(f"History has no {LINES[key].label.lower()} for {period}")
        opening[key] = v
    return opening


def project(
    opening: Mapping[str, Decimal],
    assumptions: Assumptions,
    periods: Sequence[str],
    *,
    opening_period: str,
    scenario: str = "Base",
) -> ForecastResult:
    """Run the forecast. ``opening`` holds leaf balance sheet lines plus last-period revenue."""
    a = assumptions
    days = D(365) / periods_per_year(periods[0])
    period_years = D(1) / periods_per_year(periods[0])
    prev = {k: to_decimal(v) for k, v in opening.items()}
    for key in BALANCE_LAYOUT:
        if not LINES[key].is_computed:
            prev.setdefault(key, ZERO)
    values: dict[str, dict[str, Decimal]] = {}
    checks: list[Check] = [
        balance_check(opening_period, _total_assets(prev), _total_le(prev), "Opening balance sheet")
    ]
    opening_gap = _total_assets(prev) - _total_le(prev)

    for period in periods:
        v: dict[str, Decimal] = {}
        v["revenue"] = prev["revenue"] * (1 + a.revenue_growth)
        v["cogs"] = v["revenue"] * (1 - a.gross_margin)
        v["other_opex"] = v["revenue"] * a.opex_pct_revenue + a.fixed_opex
        v["depreciation_amortization"] = prev["ppe_net"] * a.depreciation_rate
        v["interest_expense"] = (
            (prev["debt"] + prev["revolver"]) * a.interest_rate_annual * period_years
        )
        operating_income = (
            v["revenue"] - v["cogs"] - v["other_opex"] - v["depreciation_amortization"]
        )
        pretax = operating_income - v["interest_expense"]
        v["income_tax"] = max(pretax, ZERO) * a.tax_rate
        net_income = pretax - v["income_tax"]

        v["accounts_receivable"] = v["revenue"] * a.dso / days
        v["inventory"] = v["cogs"] * a.dio / days
        v["accounts_payable"] = v["cogs"] * a.dpo / days
        capex = v["revenue"] * a.capex_pct_revenue
        v["ppe_net"] = prev["ppe_net"] + capex - v["depreciation_amortization"]
        v["debt"] = prev["debt"] + a.debt_change
        v["equity"] = prev["equity"] + net_income - a.dividends
        for held in (
            "other_current_assets",
            "other_assets",
            "accrued_liabilities",
            "deferred_revenue",
            "other_liabilities",
        ):
            v[held] = prev[held]

        operating_cf = (
            net_income
            + v["depreciation_amortization"]
            - (v["accounts_receivable"] - prev["accounts_receivable"])
            - (v["inventory"] - prev["inventory"])
            + (v["accounts_payable"] - prev["accounts_payable"])
        )
        investing_cf = -capex
        # Revolver: draw whatever keeps cash at the minimum; repay from any excess above it.
        pre_cash = prev["cash"] + operating_cf + investing_cf + a.debt_change - a.dividends
        repay = min(prev["revolver"], max(pre_cash - a.minimum_cash, ZERO))
        draw = max(a.minimum_cash - pre_cash, ZERO)
        v["revolver"] = prev["revolver"] + draw - repay
        financing_cf = a.debt_change - a.dividends + draw - repay
        v["operating_cash_flow"] = operating_cf
        v["capital_expenditures"] = capex
        v["investing_cash_flow"] = investing_cf
        v["financing_cash_flow"] = financing_cf
        v["net_change_in_cash"] = operating_cf + investing_cf + financing_cf
        v["cash"] = prev["cash"] + v["net_change_in_cash"]

        _add_computed(v)
        checks.append(
            balance_check(
                period,
                v["total_assets"] - opening_gap,
                v["total_liabilities_equity"],
                "Balance sheet (net of any opening gap)" if opening_gap else "Balance sheet",
            )
        )
        checks.append(cash_check(period, prev["cash"], v["net_change_in_cash"], v["cash"]))
        values[period] = v
        prev = {**prev, **v}

    return ForecastResult(
        scenario, opening_period, list(periods), values, dict(opening), assumptions, checks
    )


def _total_assets(v: Mapping[str, Decimal]) -> Decimal:
    return sum((v.get(k, ZERO) for _, k in LINES["total_assets"].formula), ZERO)


def _total_le(v: Mapping[str, Decimal]) -> Decimal:
    liabilities = sum((v.get(k, ZERO) for _, k in LINES["total_liabilities"].formula), ZERO)
    return liabilities + v.get("equity", ZERO)


def _add_computed(v: dict[str, Decimal]) -> None:
    """Fill every computed line from the leaves, in dependency order."""

    def calc(key: str) -> Decimal:
        spec = LINES[key]
        if not spec.is_computed:
            return v.get(key, ZERO)
        return sum((sign * calc(c) for sign, c in spec.formula), ZERO)

    for key, spec in LINES.items():
        if spec.is_computed:
            v[key] = calc(key)


def forecast_periods(last_actual: str, count: int) -> list[str]:
    out, p = [], last_actual
    for _ in range(count):
        p = next_period(p)
        out.append(p)
    return out


# Assumptions from history -----------------------------------------------------


@dataclass(frozen=True)
class InferredAssumptions:
    assumptions: Assumptions
    sources: dict[str, str]


def infer_assumptions(history: FinancialData, lookback: int = 4) -> InferredAssumptions:
    """Starting assumptions from recent history, with a note on where each came from.

    These are a transparent starting point, not a prediction. Every value says
    whether it was derived from the data or fell back to a stated default.
    """
    periods = [p for p in history.periods if history.value("revenue", p)]
    if not periods:
        raise ValueError("History needs revenue to infer assumptions")
    recent = periods[-lookback:]
    last = recent[-1]
    ppy = periods_per_year(last)
    days = D(365) / ppy
    sources: dict[str, str] = {}
    span = f"last {len(recent)} {'quarters' if ppy == 4 else 'months'}"

    def total(key: str, ps: Sequence[str] = recent) -> Decimal:
        return history.value(key, list(ps)) or ZERO

    revenue = total("revenue")
    vals: dict[str, Decimal] = {}

    if len(periods) >= 2:
        window = periods[-(lookback + 1) :]
        first = history.value("revenue", window[0]) or ZERO
        end = history.value("revenue", window[-1]) or ZERO
        steps = len(window) - 1
        if first > 0 and end > 0:
            vals["revenue_growth"] = D(float(end / first) ** (1 / steps) - 1)
            sources["revenue_growth"] = f"compound growth over the {span}"
    if "revenue_growth" not in vals:
        vals["revenue_growth"] = D("0.02")
        sources["revenue_growth"] = "default (not enough history)"

    vals["gross_margin"] = (total("gross_profit") / revenue) if revenue else D("0.5")
    sources["gross_margin"] = f"{span}" if revenue else "default"
    opex = total("total_opex")
    vals["opex_pct_revenue"] = opex / revenue if revenue else D("0.3")
    sources["opex_pct_revenue"] = f"{span}"

    def latest(key: str) -> Decimal | None:
        return history.value(key, last)

    ppe = latest("ppe_net")
    da = history.value("depreciation_amortization", last)
    if ppe and da:
        vals["depreciation_rate"] = da / ppe
        sources["depreciation_rate"] = f"D&A / PP&E in {last}"
    else:
        vals["depreciation_rate"] = D("0.025") if ppy == 4 else D("0.0083")
        sources["depreciation_rate"] = "default (10% a year)"

    capex = total("capital_expenditures")
    if capex and revenue:
        vals["capex_pct_revenue"] = capex / revenue
        sources["capex_pct_revenue"] = f"{span}"
    else:
        vals["capex_pct_revenue"] = D("0.03")
        sources["capex_pct_revenue"] = "default"

    last_rev = latest("revenue") or ZERO
    last_cogs = latest("cogs") or ZERO
    for key, bal, base, default in (
        ("dso", "accounts_receivable", last_rev, D(45)),
        ("dio", "inventory", last_cogs, D(0)),
        ("dpo", "accounts_payable", last_cogs, D(30)),
    ):
        b = latest(bal)
        if b is not None and base:
            vals[key] = b / base * days
            sources[key] = f"{LINES[bal].label.lower()} in {last}"
        else:
            vals[key] = default
            sources[key] = "default"

    pretax, tax = total("pretax_income"), total("income_tax")
    if pretax > 0 and 0 <= tax / pretax <= D("0.40"):
        vals["tax_rate"] = tax / pretax
        sources["tax_rate"] = f"effective rate, {span}"
    else:
        vals["tax_rate"] = D("0.21")
        sources["tax_rate"] = "default (US federal statutory rate)"

    debt, interest = latest("debt"), total("interest_expense")
    if debt and interest:
        vals["interest_rate_annual"] = interest / debt * ppy / len(recent)
        sources["interest_rate_annual"] = f"interest / debt, {span}"
    else:
        vals["interest_rate_annual"] = D("0.07")
        sources["interest_rate_annual"] = "default"

    for key in ("fixed_opex", "debt_change", "dividends", "minimum_cash"):
        vals[key] = ZERO
        sources[key] = "default (none)"

    rounded = {k: _round_assumption(k, v) for k, v in vals.items()}
    return InferredAssumptions(Assumptions(**rounded), sources)


def _round_assumption(key: str, value: Decimal) -> Decimal:
    if key in PERCENT_ASSUMPTIONS:
        return value.quantize(D("0.0001"))
    if key in ("dso", "dio", "dpo"):
        return value.quantize(D("0.1"))
    return value.quantize(D("0.01"))


def default_scenarios(base: Assumptions, ppy: int) -> list[Scenario]:
    """Base, upside and downside cases with stated, symmetric changes to the base."""
    growth_delta = D("0.04") / ppy  # 4 points of annual growth
    up = base.with_changes(
        revenue_growth=base.revenue_growth + growth_delta,
        gross_margin=base.gross_margin + D("0.02"),
        opex_pct_revenue=max(base.opex_pct_revenue - D("0.01"), ZERO),
        dso=max(base.dso - 5, ZERO),
    )
    down = base.with_changes(
        revenue_growth=base.revenue_growth - growth_delta,
        gross_margin=base.gross_margin - D("0.02"),
        opex_pct_revenue=base.opex_pct_revenue + D("0.01"),
        dso=base.dso + 5,
    )
    note = (
        "growth {0} 4 pts a year, gross margin {0} 2 pts, opex {1} 1 pt of revenue, DSO {1} 5 days"
    )
    return [
        Scenario("Base", base, "Assumptions as set"),
        Scenario("Upside", up, "Base with " + note.format("+", "-")),
        Scenario("Downside", down, "Base with " + note.format("-", "+")),
    ]


def run_scenarios(
    history: FinancialData,
    scenarios: Sequence[Scenario],
    periods: int,
) -> list[ForecastResult]:
    last = history.periods[-1]
    opening = opening_balances(history, last)
    labels = forecast_periods(last, periods)
    return [
        project(opening, s.assumptions, labels, opening_period=last, scenario=s.name)
        for s in scenarios
    ]


def assumption_fields() -> list[str]:
    return [f.name for f in fields(Assumptions)]


def statement_lines(statement: Statement) -> list[str]:
    return [k for k, spec in LINES.items() if spec.statement is statement]


__all__ = [
    "ASSUMPTION_LABELS",
    "Assumptions",
    "ForecastResult",
    "InferredAssumptions",
    "Kind",
    "Scenario",
    "default_scenarios",
    "forecast_periods",
    "infer_assumptions",
    "next_period",
    "opening_balances",
    "project",
    "run_scenarios",
]
