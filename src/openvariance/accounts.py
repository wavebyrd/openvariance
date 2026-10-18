"""The standard chart of accounts that every data source is mapped onto.

Every amount OpenVariance stores is a *natural magnitude*: revenue is positive,
expenses are positive, assets and liabilities are positive. Signs only appear
in two places, both defined here:

* ``Line.formula`` says how computed lines (gross profit, operating income,
  totals) combine their components.
* ``Line.direction`` says whether an increase in the line helps (+1) or hurts
  (-1) profit. Variance analysis multiplies raw differences by this direction,
  so a positive variance is always favorable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Statement(str, Enum):
    INCOME = "income_statement"
    BALANCE = "balance_sheet"
    CASH_FLOW = "cash_flow"


class Kind(str, Enum):
    """Whether a line measures activity over a period or a balance at a point in time."""

    FLOW = "flow"  # summed across periods (revenue, expenses, cash flows)
    STOCK = "stock"  # taken at the last period (cash, receivables, debt)


@dataclass(frozen=True, slots=True)
class Line:
    key: str
    label: str
    statement: Statement
    kind: Kind
    direction: int  # +1 higher is favorable, -1 lower is favorable, 0 neutral
    formula: tuple[tuple[int, str], ...] = ()  # (sign, component key) for computed lines

    @property
    def is_computed(self) -> bool:
        return bool(self.formula)


def _flow(key: str, label: str, direction: int, *formula: tuple[int, str]) -> Line:
    return Line(key, label, Statement.INCOME, Kind.FLOW, direction, tuple(formula))


def _stock(key: str, label: str, *formula: tuple[int, str]) -> Line:
    return Line(key, label, Statement.BALANCE, Kind.STOCK, 0, tuple(formula))


def _cash(key: str, label: str, *formula: tuple[int, str]) -> Line:
    return Line(key, label, Statement.CASH_FLOW, Kind.FLOW, +1, tuple(formula))


OPEX_LINES = (
    "payroll",
    "software",
    "marketing",
    "rent_facilities",
    "professional_fees",
    "travel",
    "research_development",
    "sales_marketing",
    "general_administrative",
    "selling_general_administrative",
    "other_opex",
)

_LINES: tuple[Line, ...] = (
    # Income statement
    _flow("revenue", "Revenue", +1),
    _flow("cogs", "Cost of sales", -1),
    _flow("gross_profit", "Gross profit", +1, (+1, "revenue"), (-1, "cogs")),
    _flow("payroll", "Payroll and benefits", -1),
    _flow("software", "Software and subscriptions", -1),
    _flow("marketing", "Marketing and advertising", -1),
    _flow("rent_facilities", "Rent and facilities", -1),
    _flow("professional_fees", "Professional fees", -1),
    _flow("travel", "Travel and entertainment", -1),
    _flow("research_development", "Research and development", -1),
    _flow("sales_marketing", "Sales and marketing", -1),
    _flow("general_administrative", "General and administrative", -1),
    _flow("selling_general_administrative", "Selling, general and administrative", -1),
    _flow("other_opex", "Other operating expenses", -1),
    _flow("total_opex", "Total operating expenses", -1, *((+1, k) for k in OPEX_LINES)),
    _flow("ebitda", "EBITDA", +1, (+1, "gross_profit"), (-1, "total_opex")),
    _flow("depreciation_amortization", "Depreciation and amortization", -1),
    _flow(
        "operating_income",
        "Operating income",
        +1,
        (+1, "ebitda"),
        (-1, "depreciation_amortization"),
    ),
    _flow("interest_expense", "Interest expense", -1),
    _flow("other_income", "Other income (expense), net", +1),
    _flow(
        "pretax_income",
        "Income before taxes",
        +1,
        (+1, "operating_income"),
        (-1, "interest_expense"),
        (+1, "other_income"),
    ),
    _flow("income_tax", "Income tax expense", -1),
    _flow("net_income", "Net income", +1, (+1, "pretax_income"), (-1, "income_tax")),
    # Balance sheet
    _stock("cash", "Cash and equivalents"),
    _stock("accounts_receivable", "Accounts receivable"),
    _stock("inventory", "Inventory"),
    _stock("other_current_assets", "Other current assets"),
    _stock("ppe_net", "Property and equipment, net"),
    _stock("other_assets", "Other assets"),
    _stock(
        "total_assets",
        "Total assets",
        (+1, "cash"),
        (+1, "accounts_receivable"),
        (+1, "inventory"),
        (+1, "other_current_assets"),
        (+1, "ppe_net"),
        (+1, "other_assets"),
    ),
    _stock("accounts_payable", "Accounts payable"),
    _stock("accrued_liabilities", "Accrued liabilities"),
    _stock("deferred_revenue", "Deferred revenue"),
    _stock("debt", "Debt"),
    _stock("revolver", "Revolving credit facility"),
    _stock("other_liabilities", "Other liabilities"),
    _stock(
        "total_liabilities",
        "Total liabilities",
        (+1, "accounts_payable"),
        (+1, "accrued_liabilities"),
        (+1, "deferred_revenue"),
        (+1, "debt"),
        (+1, "revolver"),
        (+1, "other_liabilities"),
    ),
    _stock("equity", "Shareholders' equity"),
    _stock(
        "total_liabilities_equity",
        "Total liabilities and equity",
        (+1, "total_liabilities"),
        (+1, "equity"),
    ),
    # Cash flow statement
    _cash("operating_cash_flow", "Cash from operations"),
    _cash("capital_expenditures", "Capital expenditures"),
    _cash("investing_cash_flow", "Cash from investing"),
    _cash("financing_cash_flow", "Cash from financing"),
    _cash("net_change_in_cash", "Net change in cash"),
    _cash(
        "free_cash_flow",
        "Free cash flow",
        (+1, "operating_cash_flow"),
        (-1, "capital_expenditures"),
    ),
)

LINES: dict[str, Line] = {line.key: line for line in _LINES}

# Display order for an income statement variance report: (line key, indent depth).
PNL_LAYOUT: tuple[tuple[str, int], ...] = (
    ("revenue", 0),
    ("cogs", 0),
    ("gross_profit", 0),
    *((key, 1) for key in OPEX_LINES),
    ("total_opex", 0),
    ("ebitda", 0),
    ("depreciation_amortization", 0),
    ("operating_income", 0),
    ("interest_expense", 0),
    ("other_income", 0),
    ("pretax_income", 0),
    ("income_tax", 0),
    ("net_income", 0),
)

BALANCE_LAYOUT: tuple[str, ...] = (
    "cash",
    "accounts_receivable",
    "inventory",
    "other_current_assets",
    "ppe_net",
    "other_assets",
    "total_assets",
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue",
    "debt",
    "revolver",
    "other_liabilities",
    "total_liabilities",
    "equity",
    "total_liabilities_equity",
)

CASH_FLOW_LAYOUT: tuple[str, ...] = (
    "operating_cash_flow",
    "capital_expenditures",
    "investing_cash_flow",
    "financing_cash_flow",
    "net_change_in_cash",
    "free_cash_flow",
)


def line(key: str) -> Line:
    """Look up a line, with a helpful error for typos."""
    try:
        return LINES[key]
    except KeyError:
        raise KeyError(f"Unknown line {key!r}. Known lines: {', '.join(LINES)}") from None


def leaf_components(key: str) -> tuple[str, ...]:
    """All non-computed lines a line is built from (itself if it is not computed)."""
    target = line(key)
    if not target.is_computed:
        return (key,)
    out: list[str] = []
    for _, component in target.formula:
        out.extend(leaf_components(component))
    return tuple(out)
