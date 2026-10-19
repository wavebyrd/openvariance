"""Map a company's own accounts onto the standard chart, then check the result ties.

Suggestions come from three sources, strongest first: the section an account
sits in ("Cost of Goods Sold"), keywords in its name ("Salaries"), and common
small-business account number ranges (4000s are revenue). Every suggestion
carries a confidence and a plain-English reason. Nothing is imported until
every account has a mapping, and saved mappings make next month a one-step import.

A mapping target is a line key (``revenue``), a negated line key for contra
accounts (``-revenue`` for returns and discounts), or ``ignore``.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..accounts import LINES, line
from ..checks import Check, tie_check
from ..dataset import Entry, FinancialData
from ..money import ZERO
from .tabular import Inspection, SourceRow

MAPPING_VERSION = 1


@dataclass(frozen=True, slots=True)
class Suggestion:
    source: str
    target: str
    confidence: str  # "high", "medium", "low"
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "account": self.source,
            "suggested_line": self.target,
            "confidence": self.confidence,
            "reason": self.reason,
        }


_SECTION_RULES: tuple[tuple[str, str, str], ...] = (
    (
        r"cost of (goods|sales|revenue)|cogs|direct costs?",
        "cogs",
        "it sits in the cost of sales section",
    ),
    (r"other income|non-?operating income", "other_income", "it sits in the other income section"),
    (
        r"other expense|non-?operating expense",
        "-other_income",
        "it sits in the other expense section (non-operating, so it reduces other income)",
    ),
    (
        r"^(income|revenue|sales|ordinary income)\b|operating income$|^revenues?$",
        "revenue",
        "it sits in the income section",
    ),
)

_SPECIFIC: tuple[tuple[str, str, str], ...] = (
    (r"depreciation|amortization", "depreciation_amortization", "depreciation or amortization"),
    (r"interest expense|interest paid|loan interest", "interest_expense", "interest expense"),
    (r"interest income|dividend income|investment income", "other_income", "investment income"),
    (r"income tax|provision for (income )?tax", "income_tax", "income tax"),
)

_CONTRA_REVENUE = r"refund|returns|discounts? (given|allowed)|sales discount|allowance"

_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    (r"interest expense|interest paid|loan interest", "interest_expense", "high"),
    (r"interest income|investment income|dividend income|gain on", "other_income", "high"),
    (r"income tax|provision for (income )?tax", "income_tax", "high"),
    (r"federal tax|state tax|franchise tax", "income_tax", "medium"),
    (r"depreciation|amortization", "depreciation_amortization", "high"),
    (_CONTRA_REVENUE, "-revenue", "medium"),
    (r"cost of (goods|sales|revenue)|\bcogs\b", "cogs", "high"),
    (r"\brevenues?\b", "revenue", "high"),
    (
        r"materials|freight[- ]in|inventory adj|merchant|payment processing|direct labor",
        "cogs",
        "medium",
    ),
    (
        r"hosting|\baws\b|azure|google cloud|\bgcp\b|cloud infrastructure|data center",
        "cogs",
        "medium",
    ),
    (
        r"salar|wage|payroll|bonus|commission|benefit|health insurance|401\(?k|pension|"
        r"\bpto\b|severance|recruit|employee|staff|personnel|contract labor|contractor",
        "payroll",
        "high",
    ),
    (r"software|subscription|saas|licen[cs]e|computer|technology|it services", "software", "high"),
    (
        r"advertis|marketing|\bads?\b|promotion|sponsor|trade show|conference|events|seo",
        "marketing",
        "high",
    ),
    (
        r"\brent\b|lease|utilit|office (rent|space)|facilit|janitor|cleaning|electric|water\b|\bgas\b",
        "rent_facilities",
        "high",
    ),
    (r"legal|accounting|audit|consult|professional|bookkeep|tax prep", "professional_fees", "high"),
    (r"travel|meal|lodging|airfare|hotel|mileage|entertainment|uber|lyft", "travel", "high"),
    (r"research|development|r&d|engineering", "research_development", "low"),
    (
        r"office supplies|supplies|postage|bank (fee|charge)|dues|telephone|internet|misc",
        "other_opex",
        "high",
    ),
    (
        r"insurance|phone|shipping|repair|maintenance|general|other|fees|charitable|license|"
        r"permit",
        "other_opex",
        "medium",
    ),
    (
        r"sales|revenue|income|fees? earned|subscription revenue|service revenue",
        "revenue",
        "medium",
    ),
)

_RANGES: tuple[tuple[int, int, str, str], ...] = (
    (4000, 4999, "revenue", "4000-series accounts are usually revenue"),
    (5000, 5999, "cogs", "5000-series accounts are usually cost of sales"),
    (6000, 7999, "other_opex", "6000-7000 series accounts are usually operating expenses"),
    (8000, 8999, "other_income", "8000-series accounts are usually other income or expense"),
)

_OPERATING_SECTIONS = re.compile(r"expense|operating|overhead|sg&a|g&a", re.IGNORECASE)


def suggest(row: SourceRow) -> Suggestion:
    """Best guess for one account, with how sure it is and why."""
    name = row.name.lower()
    section = row.section.lower()
    # Specific names win over section rules: interest and tax often sit under "Other Expenses".
    for pattern, target, what in _SPECIFIC:
        if re.search(pattern, name):
            return Suggestion(row.label, target, "high", f"the account name says {what}")
    for pattern, target, why in _SECTION_RULES:
        if re.search(pattern, section.split(" / ")[-1] if section else ""):
            if target == "revenue" and re.search(_CONTRA_REVENUE, name):
                return Suggestion(
                    row.label,
                    "-revenue",
                    "high",
                    "a contra-revenue account (returns or discounts) in the income section",
                )
            return Suggestion(row.label, target, "high", why)
    for pattern, target, confidence in _KEYWORDS:
        if re.search(pattern, name):
            if target == "revenue" and _OPERATING_SECTIONS.search(section):
                continue
            return Suggestion(
                row.label,
                target,
                confidence,
                f"the name matches {LINES[target.lstrip('-')].label.lower()} keywords",
            )
    if row.number and row.number[:4].isdigit():
        n = int(row.number[:4])
        for low, high, target, why in _RANGES:
            if low <= n <= high:
                return Suggestion(row.label, target, "low", why)
    if _OPERATING_SECTIONS.search(section):
        return Suggestion(
            row.label, "other_opex", "low", "an operating expense with no clearer match"
        )
    return Suggestion(row.label, "other_opex", "low", "no rule matched; please confirm")


@dataclass
class AccountMapping:
    """Saved account mapping: source account label -> target."""

    accounts: dict[str, str] = field(default_factory=dict)
    sign_convention: str = "auto"  # "auto", "natural", "credit_negative"

    @classmethod
    def load(cls, path: str | Path) -> AccountMapping:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("openvariance_mapping") != MAPPING_VERSION:
            raise ValueError(
                f"{path} is not an OpenVariance mapping file (version {MAPPING_VERSION})"
            )
        m = cls(dict(data.get("accounts", {})), data.get("sign_convention", "auto"))
        for source, target in m.accounts.items():
            validate_target(target, source)
        return m

    def save(self, path: str | Path) -> None:
        payload = {
            "openvariance_mapping": MAPPING_VERSION,
            "sign_convention": self.sign_convention,
            "accounts": dict(sorted(self.accounts.items())),
        }
        Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def validate_target(target: str, source: str = "") -> None:
    if target == "ignore":
        return
    key = target.removeprefix("-")
    if key not in LINES:
        raise ValueError(f"Mapping for {source!r} points to unknown line {target!r}")
    if line(key).is_computed:
        raise ValueError(
            f"Mapping for {source!r} points to computed line {key!r}; map to a leaf line"
        )


@dataclass
class ImportResult:
    data: FinancialData
    unmapped: list[str]
    suggestions: list[Suggestion]
    checks: list[Check]
    notes: list[str]

    @property
    def ok(self) -> bool:
        return not self.unmapped and all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "periods": self.data.periods,
            "lines": self.data.lines,
            "unmapped": self.unmapped,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "checks": [c.to_dict() for c in self.checks],
            "notes": self.notes,
        }


def suggest_mapping(
    inspection: Inspection, existing: AccountMapping | None = None
) -> list[Suggestion]:
    """Suggestions for every account not already in ``existing``."""
    seen: set[str] = set()
    out = []
    for row in inspection.details:
        if row.label in seen or (existing and row.label in existing.accounts):
            continue
        seen.add(row.label)
        out.append(suggest(row))
    return out


def apply_mapping(
    inspection: Inspection,
    mapping: AccountMapping,
    name: str,
    *,
    accept_suggestions: str | None = None,
) -> ImportResult:
    """Build FinancialData from an inspected file.

    ``accept_suggestions`` ("high", "medium" or "low") auto-accepts suggestions
    at or above that confidence for accounts the mapping doesn't cover.
    Anything still unmapped is reported, and the data excludes it.
    """
    rank = {"high": 3, "medium": 2, "low": 1}
    threshold = rank[accept_suggestions] if accept_suggestions else 99
    suggestions = suggest_mapping(inspection, mapping)
    targets = dict(mapping.accounts)
    for s in suggestions:
        if rank[s.confidence] >= threshold:
            targets[s.source] = s.target
    unmapped = sorted({r.label for r in inspection.details if r.label not in targets})
    notes: list[str] = []

    raw: dict[tuple[str, str, str | None], Decimal] = defaultdict(lambda: ZERO)
    for row in inspection.details:
        target = targets.get(row.label)
        if target is None or target == "ignore":
            continue
        sign = -1 if target.startswith("-") else 1
        key = target.removeprefix("-")
        for period, amount in row.values.items():
            raw[(period, key, row.department)] += sign * amount

    flip = _sign_flips(raw, mapping.sign_convention, inspection, notes)
    entries = [
        Entry(period, key, amount * flip.get(key, 1), dept)
        for (period, key, dept), amount in sorted(
            raw.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
        )
    ]
    data = FinancialData(name, entries, {"source": inspection.path})
    checks = _tie_out(inspection, data, flip)
    remaining = [s for s in suggestions if s.source in unmapped]
    return ImportResult(data, unmapped, remaining, checks, notes)


def _sign_flips(
    raw: Mapping[tuple[str, str, str | None], Decimal],
    convention: str,
    inspection: Inspection,
    notes: list[str],
) -> dict[str, int]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for (_, key, _), amount in raw.items():
        totals[key] += amount
    income = [k for k in totals if LINES[k].direction > 0 and LINES[k].kind.value == "flow"]
    expense = [k for k in totals if LINES[k].direction < 0]
    revenue = totals.get("revenue", ZERO)
    expense_total = sum((totals[k] for k in expense), ZERO)
    if convention == "credit_negative" or (
        convention == "auto" and revenue < 0 and expense_total >= 0
    ):
        notes.append(
            "Revenue and other income were stored as negative (credit) amounts; "
            "flipped them to positive."
        )
        return {k: -1 for k in income}
    if convention == "auto" and revenue > 0 and expense_total < 0:
        notes.append("Expenses were stored as negative amounts; flipped them to positive.")
        return {k: -1 for k in expense}
    return {}


_TOTAL_TARGETS: tuple[tuple[str, str], ...] = (
    (r"^total\s+(income|revenues?|sales)$", "revenue"),
    (r"^total\s+(cost of (goods sold|sales|revenue)|cogs)$", "cogs"),
    (r"^gross\s+profit$", "gross_profit"),
    (
        r"^(net\s+operating\s+income|operating\s+(income|profit)|net\s+ordinary\s+income)$",
        "operating_income",
    ),
    (r"^net\s+(income|profit|loss|earnings)$", "net_income"),
)


def _tie_out(inspection: Inspection, data: FinancialData, flip: Mapping[str, int]) -> list[Check]:
    """Compare recognizable totals in the source file with the mapped data."""
    checks: list[Check] = []
    for row in inspection.totals:
        name = row.name.strip().lower().rstrip(":")
        key = next((k for pattern, k in _TOTAL_TARGETS if re.match(pattern, name)), None)
        if key is None:
            continue
        for period, source_value in row.values.items():
            ours = data.value(key, period)
            if ours is None:
                continue
            expected = source_value
            detail = (
                f"{LINES[key].label} after mapping vs the file's {row.label!r} row (row {row.row})"
            )
            if flip and abs(ours + source_value) <= Decimal("0.01") and ours != 0:
                expected = -source_value
                detail += "; the file shows it with the opposite sign"
            checks.append(tie_check(f"Ties to source: {row.label}", period, expected, ours, detail))
    return checks
