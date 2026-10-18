"""Bring private-company data in from spreadsheets and accounting exports."""

from __future__ import annotations

import csv
from pathlib import Path

from ..dataset import FinancialData
from .mapping import (
    AccountMapping,
    ImportResult,
    Suggestion,
    apply_mapping,
    suggest,
    suggest_mapping,
)
from .tabular import Inspection, SourceRow, inspect_file, parse_amount, parse_period

__all__ = [
    "AccountMapping",
    "ImportResult",
    "Inspection",
    "SourceRow",
    "Suggestion",
    "apply_mapping",
    "import_file",
    "inspect_file",
    "is_standard_format",
    "parse_amount",
    "parse_period",
    "suggest",
    "suggest_mapping",
]


def is_standard_format(path: str | Path) -> bool:
    """True for CSVs already in OpenVariance's long format (period,line,amount[,department])."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        return False
    with open(path, newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh), [])
    return {"period", "line", "amount"} <= {h.strip().lower() for h in header}


def import_file(
    path: str | Path,
    *,
    mapping: AccountMapping | str | Path | None = None,
    name: str | None = None,
    period: str | None = None,
    sheet: str | None = None,
    accept_suggestions: str | None = "high",
) -> ImportResult:
    """Inspect, map and tie out a file in one step.

    Standard-format CSVs load directly. Anything else goes through inspection
    and mapping; by default only high-confidence suggestions are accepted, and
    every other account is reported as unmapped for a person to confirm.
    """
    path = Path(path)
    label = name or path.stem
    if is_standard_format(path):
        return ImportResult(FinancialData.from_csv(path, label), [], [], [], ["Standard format."])
    if mapping is not None and not isinstance(mapping, AccountMapping):
        mapping = AccountMapping.load(mapping)
    inspection = inspect_file(path, sheet=sheet, period=period)
    result = apply_mapping(
        inspection, mapping or AccountMapping(), label, accept_suggestions=accept_suggestions
    )
    result.notes[:0] = inspection.hints + inspection.warnings
    return result
