"""Public-company actuals from SEC EDGAR's free XBRL API (data.sec.gov).

SEC's ``companyfacts`` endpoint already parses XBRL into JSON, but it is not
yet usable as a set of financial statements. This module handles the parts
that are still hard:

* **Duplicates and restatements.** The same quarter is reported in several
  filings, sometimes at different rounding. The most recently filed value wins.
* **Year-to-date figures.** 10-Qs report 6- and 9-month totals next to the
  quarter. Discrete quarters are taken directly when reported and otherwise
  derived from consecutive year-to-date values with the same start date.
* **Q4.** It is never filed on its own. It is the 10-K's full year minus the
  9-month year-to-date figure.
* **Different tags for the same thing.** Each standard line has a ranked list
  of us-gaap tags, and per quarter the first one reported is used.
* **Lines a company doesn't break out.** Operating expenses not covered by a
  known tag are shown as one explicit line derived from reported operating
  income, never silently dropped.

SEC asks automated clients to identify themselves. Set ``OPENVARIANCE_SEC_USER_AGENT``
(for example ``"Jane Doe jane@example.com"``) or pass ``user_agent``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from .dataset import Entry, FinancialData
from .money import ZERO

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Ranked us-gaap tags per standard line. Duration (flow) facts.
FLOW_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ),
    "cogs": (
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
    ),
    "research_development": (
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ),
    "sales_marketing": ("SellingAndMarketingExpense", "MarketingAndAdvertisingExpense"),
    "general_administrative": ("GeneralAndAdministrativeExpense",),
    "selling_general_administrative": ("SellingGeneralAndAdministrativeExpense",),
    "interest_expense": (
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestExpenseNet",
    ),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditures": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities",),
}

# Instant (stock) facts.
STOCK_TAGS: dict[str, tuple[str, ...]] = {
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ),
    "accounts_receivable": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "inventory": ("InventoryNet",),
    "ppe_net": (
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
    ),
    "total_assets": ("Assets",),
    "accounts_payable": ("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"),
    "accrued_liabilities": ("AccruedLiabilitiesCurrent",),
    "deferred_revenue": ("ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"),
    "debt_current": ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
    "debt_noncurrent": ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
    "debt_total": ("LongTermDebt", "DebtInstrumentCarryingAmount"),
    "total_liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "total_liabilities_equity": ("LiabilitiesAndStockholdersEquity",),
}


class SecError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Fact:
    start: dt.date | None
    end: dt.date
    value: Decimal
    filed: dt.date
    form: str
    accession: str


# Fetching --------------------------------------------------------------------


def _cache_dir() -> Path:
    root = os.environ.get("OPENVARIANCE_CACHE") or (Path.home() / ".cache" / "openvariance")
    path = Path(root) / "sec"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _user_agent(user_agent: str | None) -> str:
    ua = (
        user_agent
        or os.environ.get("OPENVARIANCE_SEC_USER_AGENT")
        or os.environ.get("SEC_USER_AGENT")
    )
    if not ua or "@" not in ua:
        raise SecError(
            "SEC asks every automated client to identify itself with a name and email. "
            'Set OPENVARIANCE_SEC_USER_AGENT, for example: "Jane Doe jane@example.com".'
        )
    return ua


def _get_json(url: str, user_agent: str, cache: Path | None, max_age_hours: float) -> Any:
    if cache and cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SecError(f"SEC has no data at {url}") from None
        raise SecError(f"SEC request failed ({exc.code}): {url}") from None
    except urllib.error.URLError as exc:
        raise SecError(f"Couldn't reach SEC: {exc.reason}") from None
    if cache:
        cache.write_bytes(body)
    return json.loads(body)


def lookup_cik(ticker: str, user_agent: str | None = None) -> tuple[int, str]:
    """CIK and company name for a ticker, from SEC's ticker list."""
    data = _get_json(TICKERS_URL, _user_agent(user_agent), _cache_dir() / "tickers.json", 24)
    wanted = ticker.strip().upper()
    for item in data.values():
        if item["ticker"].upper() == wanted:
            return int(item["cik_str"]), str(item["title"])
    raise SecError(f"Ticker {ticker!r} isn't in SEC's list of company tickers")


def fetch_company_facts(
    ticker_or_cik: str | int, user_agent: str | None = None, max_age_hours: float = 12
) -> dict[str, Any]:
    """Raw companyfacts JSON, cached locally for ``max_age_hours``."""
    if isinstance(ticker_or_cik, int) or str(ticker_or_cik).isdigit():
        cik = int(ticker_or_cik)
    else:
        cik, _ = lookup_cik(str(ticker_or_cik), user_agent)
    ua = _user_agent(user_agent)
    return dict(
        _get_json(
            FACTS_URL.format(cik=cik), ua, _cache_dir() / f"CIK{cik:010d}.json", max_age_hours
        )
    )


# Normalizing -------------------------------------------------------------------


def _facts(company: Mapping[str, Any], tag: str) -> list[Fact]:
    node = company.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node:
        return []
    out = []
    for f in node.get("units", {}).get("USD", []):
        if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F"):
            continue
        out.append(
            Fact(
                start=dt.date.fromisoformat(f["start"]) if "start" in f else None,
                end=dt.date.fromisoformat(f["end"]),
                value=Decimal(str(f["val"])),
                filed=dt.date.fromisoformat(f["filed"]),
                form=f["form"],
                accession=f.get("accn", ""),
            )
        )
    return out


def _latest_filed(facts: Sequence[Fact]) -> dict[tuple[dt.date | None, dt.date], Fact]:
    """One fact per (start, end): the most recently filed, which reflects restatements."""
    best: dict[tuple[dt.date | None, dt.date], Fact] = {}
    for f in facts:
        k = (f.start, f.end)
        if k not in best or (f.filed, f.accession) > (best[k].filed, best[k].accession):
            best[k] = f
    return best


def _days(start: dt.date, end: dt.date) -> int:
    return (end - start).days + 1


def _is_quarter(days: int) -> bool:
    return 80 <= days <= 100


def discrete_quarters(facts: Sequence[Fact]) -> dict[dt.date, tuple[Decimal, str]]:
    """Quarter-end date -> (value, how it was obtained) for a duration tag."""
    by_span = _latest_filed(f for f in facts if f.start is not None)  # type: ignore[arg-type]
    out: dict[dt.date, tuple[Decimal, str]] = {}
    for (start, end), f in by_span.items():
        assert start is not None
        if _is_quarter(_days(start, end)):
            out[end] = (f.value, f"reported ({f.form} filed {f.filed})")
    by_start: dict[dt.date, list[Fact]] = defaultdict(list)
    for (start, _), f in by_span.items():
        assert start is not None
        by_start[start].append(f)
    for start, group in by_start.items():
        group.sort(key=lambda f: f.end)
        for prev, cur in pairwise(group):
            gap = (cur.end - prev.end).days
            if cur.end in out or not _is_quarter(gap):
                continue
            how = (
                "full year minus 9 months"
                if _days(start, cur.end) > 300
                else "year-to-date difference"
            )
            out[cur.end] = (
                cur.value - prev.value,
                f"derived: {how} ({cur.form} filed {cur.filed})",
            )
    return out


def instants(facts: Sequence[Fact]) -> dict[dt.date, Decimal]:
    return {
        end: f.value for (_, end), f in _latest_filed([f for f in facts if f.start is None]).items()
    }


def quarter_label(end: dt.date) -> str:
    """Calendar quarter of a period end, allowing for 52/53-week years ending early in a month."""
    d = end - dt.timedelta(days=7)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


@dataclass
class SecCompany:
    name: str
    cik: int
    data: FinancialData
    provenance: dict[tuple[str, str], str]  # (period, line) -> source tag and method
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.name,
            "cik": self.cik,
            "periods": self.data.periods,
            "lines": self.data.lines,
            "notes": self.notes,
        }


def normalize(company: Mapping[str, Any], quarters: int = 12) -> SecCompany:
    """Quarterly standard statements from companyfacts JSON."""
    name = str(company.get("entityName", "Company"))
    cik = int(company.get("cik", 0))
    flow: dict[str, dict[str, tuple[Decimal, str]]] = defaultdict(dict)
    for key, tags in FLOW_TAGS.items():
        for tag in tags:
            for end, (value, how) in discrete_quarters(_facts(company, tag)).items():
                period = quarter_label(end)
                flow[key].setdefault(period, (value, f"{tag}, {how}"))
    stock: dict[str, dict[str, tuple[Decimal, str]]] = defaultdict(dict)
    for key, tags in STOCK_TAGS.items():
        for tag in tags:
            for end, value in instants(_facts(company, tag)).items():
                stock[key].setdefault(quarter_label(end), (value, tag))

    periods = sorted(flow.get("revenue", {}))[-quarters:]
    if not periods:
        raise SecError(f"{name} has no quarterly revenue in its XBRL filings")
    entries: list[Entry] = []
    provenance: dict[tuple[str, str], str] = {}
    notes: list[str] = []

    def add(period: str, key: str, value: Decimal, how: str) -> None:
        entries.append(Entry(period, key, value))
        provenance[(period, key)] = how

    opex_keys = (
        "research_development",
        "sales_marketing",
        "general_administrative",
        "selling_general_administrative",
    )
    derived_opex = derived_bs = 0
    for p in periods:

        def get(k: str, p: str = p) -> tuple[Decimal, str] | None:
            return flow.get(k, {}).get(p)

        revenue = get("revenue")
        assert revenue is not None
        add(p, "revenue", *revenue)
        cogs = get("cogs")
        if cogs:
            add(p, "cogs", *cogs)
        known = ZERO
        for k in opex_keys:
            if k == "selling_general_administrative" and (
                get("sales_marketing") or get("general_administrative")
            ):
                continue
            if v := get(k):
                add(p, k, *v)
                known += v[0]
        da = get("depreciation_amortization")
        op = get("operating_income")
        if op:
            residual = revenue[0] - (cogs[0] if cogs else ZERO) - known - op[0]
            # D&A is reported on the cash flow statement; show it as its own line when the
            # remaining operating costs are large enough to contain it.
            if da and residual >= da[0]:
                add(p, "depreciation_amortization", da[0], f"{da[1]} (from cash flow statement)")
                residual -= da[0]
            if residual:
                add(
                    p,
                    "other_opex",
                    residual,
                    "derived: revenue - cost of sales - itemized opex - reported operating income",
                )
                derived_opex += 1
        for k in ("interest_expense", "income_tax"):
            if v := get(k):
                add(p, k, abs(v[0]), v[1])
        ni, pre = get("net_income"), get("pretax_income")
        if op and pre:
            interest = get("interest_expense")
            other = pre[0] - op[0] + (abs(interest[0]) if interest else ZERO)
            if other:
                add(
                    p, "other_income", other, "derived: pretax income - operating income + interest"
                )
        if ni and pre:
            tax = get("income_tax")
            implied_tax = pre[0] - ni[0]
            if tax is None and implied_tax:
                add(p, "income_tax", implied_tax, "derived: pretax income - net income")
        for k in ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow"):
            if v := get(k):
                add(p, k, *v)
        if v := get("capital_expenditures"):
            add(p, "capital_expenditures", *v)
        derived_bs += _balance_sheet(p, stock, add)

    if derived_opex:
        notes.append(
            "Operating expenses the company does not tag individually are shown as 'Other "
            "operating expenses', derived from reported operating income so the income "
            "statement ties to what was filed."
        )
    if derived_bs:
        notes.append(
            "'Other assets' and 'Other liabilities' are the reported totals minus the itemized "
            "lines, so the balance sheet ties to the filed totals."
        )
    notes.append("Quarters are labeled by calendar quarter of the period end date.")
    data = FinancialData(name, entries, {"source": "SEC EDGAR companyfacts", "cik": str(cik)})
    return SecCompany(name, cik, data, provenance, notes)


def _balance_sheet(
    period: str, stock: Mapping[str, Mapping[str, tuple[Decimal, str]]], add: Any
) -> int:
    def get(k: str) -> Decimal | None:
        v = stock.get(k, {}).get(period)
        return v[0] if v else None

    total_assets = get("total_assets")
    if total_assets is None:
        return 0
    derived = 0
    known_assets = ZERO
    for k in ("cash", "accounts_receivable", "inventory", "ppe_net"):
        if (v := get(k)) is not None:
            add(period, k, v, stock[k][period][1])
            known_assets += v
    add(
        period,
        "other_assets",
        total_assets - known_assets,
        "derived: total assets - itemized assets",
    )
    derived += 1
    debt = get("debt_total")
    if debt is None and (get("debt_current") is not None or get("debt_noncurrent") is not None):
        debt = (get("debt_current") or ZERO) + (get("debt_noncurrent") or ZERO)
    equity = get("equity")
    total_le = get("total_liabilities_equity") or total_assets
    total_liab = get("total_liabilities")
    if total_liab is None and equity is not None:
        total_liab = total_le - equity
    if total_liab is None or equity is None:
        return derived
    known_liab = ZERO
    for k in ("accounts_payable", "accrued_liabilities", "deferred_revenue"):
        if (v := get(k)) is not None:
            add(period, k, v, stock[k][period][1])
            known_liab += v
    if debt is not None:
        add(period, "debt", debt, "LongTermDebt or current + noncurrent debt")
        known_liab += debt
    add(
        period,
        "other_liabilities",
        total_liab - known_liab,
        "derived: total liabilities - itemized liabilities",
    )
    # Equity attributable to the parent plus any noncontrolling interest, so the totals tie.
    add(
        period,
        "equity",
        total_le - total_liab,
        "derived: total liabilities and equity - total liabilities",
    )
    return derived + 1


def load_company(
    ticker_or_cik: str | int,
    quarters: int = 12,
    user_agent: str | None = None,
    facts_path: str | Path | None = None,
) -> SecCompany:
    """Fetch (or read from ``facts_path``) and normalize a company's quarterly statements."""
    if facts_path:
        company = json.loads(Path(facts_path).read_text(encoding="utf-8"))
    else:
        company = fetch_company_facts(ticker_or_cik, user_agent)
    return normalize(company, quarters)
