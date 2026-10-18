from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from openvariance import demo
from openvariance.importers import (
    AccountMapping,
    import_file,
    inspect_file,
    parse_amount,
    parse_period,
    suggest,
)
from openvariance.importers.tabular import SourceRow

from .conftest import EXAMPLES


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.50", Decimal("1234.50")),
        ("(1,234.50)", Decimal("-1234.50")),
        ("$ 99", Decimal(99)),
        ("1,000-", Decimal(-1000)),
        ("-5", Decimal(-5)),
        ("12.00 CR", Decimal(-12)),
        ("—", None),
        ("", None),
        ("n/a", None),
        ("abc", None),
        (3.5, Decimal("3.5")),
        (7, Decimal(7)),
    ],
)
def test_parse_amount(text: object, expected: Decimal | None) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jan 2026", "2026-01"),
        ("September 2026", "2026-09"),
        ("Sep-26", "2026-09"),
        ("2026-03", "2026-03"),
        ("03/2026", "2026-03"),
        ("Q3 2026", "2026-Q3"),
        ("2026 Q4", "2026-Q4"),
        (dt.date(2026, 5, 31), "2026-05"),
        ("Total", None),
        ("Jan - Sep 2026", None),
        ("Revenue", None),
    ],
)
def test_parse_period(text: object, expected: str | None) -> None:
    assert parse_period(text) == expected


def test_quickbooks_export_imports_and_ties(demo_data) -> None:  # type: ignore[no-untyped-def]
    path = EXAMPLES / "demo_pl_quickbooks_2026.csv"
    insp = inspect_file(path)
    assert insp.layout == "wide" and insp.columns[0] == "2026-01" and insp.columns[-1] == "2026-09"
    assert len(insp.details) == 19
    assert all(not r.note.startswith("does not match") for r in insp.totals)
    result = import_file(path)  # high-confidence suggestions only
    assert result.ok, (result.unmapped, [c for c in result.checks if not c.passed])
    assert {c.name for c in result.checks} >= {
        "Ties to source: Total Income",
        "Ties to source: Net Income",
    }
    # Every line and every month must match the data the export was generated from.
    assert result.data.lines == demo_data.actual.lines
    for key in [*demo_data.actual.lines, "operating_income", "net_income"]:
        for period in demo_data.actual.periods:
            ours = result.data.value(key, period)
            expected = demo_data.actual.value(key, period)
            assert ours is not None and expected is not None
            assert abs(ours - expected) <= Decimal("0.02"), (key, period)


def test_budget_workbook_needs_confirmation_then_saves(tmp_path: Path) -> None:
    path = EXAMPLES / "demo_budget_2026.xlsx"
    insp = inspect_file(path)
    assert insp.sheet == "FY26 Budget" and len(insp.columns) == 12
    strict = import_file(path, accept_suggestions="high")
    assert "Other G&A" in strict.unmapped  # medium confidence: a person confirms it
    confirm = {s.source: s.target for s in strict.suggestions}
    assert confirm == {"Other G&A": "other_opex"}
    mapping = AccountMapping(confirm)
    mapping.save(tmp_path / "map.json")
    result = import_file(path, mapping=tmp_path / "map.json")
    assert result.ok
    budget = demo.load().budget
    assert result.data.lines == budget.lines
    for key in budget.lines:
        for period in budget.periods:
            ours, expected = result.data.value(key, period), budget.value(key, period)
            assert ours is not None and expected is not None
            assert abs(ours - expected) <= Decimal("0.5"), (key, period)


def test_units_and_credit_negative_long_format(tmp_path: Path) -> None:
    path = tmp_path / "tb.csv"
    path.write_text(
        "Trial balance (in thousands)\n"
        "Account,Period,Debit,Credit,Department\n"
        "4000 Sales,2026-01,,120.5,\n"
        "5000 Cost of sales,2026-01,40,,\n"
        "6100 Salaries,2026-01,30,,Eng\n"
        "6100 Salaries,2026-01,10,,Sales\n",
        encoding="utf-8",
    )
    result = import_file(path, accept_suggestions="low")
    assert result.ok
    d = result.data
    assert d.value("revenue", "2026-01") == Decimal("120500")
    assert d.value("payroll", "2026-01", "Eng") == Decimal("30000")
    assert any("flipped" in n for n in result.notes)


def test_department_columns_need_a_period(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["P&L by Class"])
    ws.append(["", "Engineering", "Sales", "Total"])
    ws.append(["Salaries", 100, 50, 150])
    ws.append(["Software", 20, 5, 25])
    path = tmp_path / "by_class.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="period"):
        inspect_file(path)
    result = import_file(path, period="2026-09")
    assert result.ok
    assert result.data.value("payroll", "2026-09", "Sales") == 50
    assert result.data.value("software", "2026-09") == 25


def test_unlabeled_subtotals_and_bad_totals_are_flagged(tmp_path: Path) -> None:
    path = tmp_path / "pl.csv"
    path.write_text(
        ",Jan 2026,Feb 2026\n"
        "Income,,\n"
        "Product sales,100,110\n"
        "Service sales,50,40\n"
        "Sales,150,150\n"
        "Total Income,151,150\n",
        encoding="utf-8",
    )
    insp = inspect_file(path)
    assert [r.label for r in insp.details] == ["Product sales", "Service sales"]
    assert any(r.kind == "subtotal" and r.label == "Sales" for r in insp.rows)
    assert any("doesn't equal" in w for w in insp.warnings)


def test_suggestions_explain_themselves() -> None:
    row = SourceRow(
        5,
        "6110 Salaries & Wages - Sales",
        "6110",
        "Salaries & Wages - Sales",
        {},
        "detail",
        "Expenses / Payroll Expenses",
    )
    s = suggest(row)
    assert (s.target, s.confidence) == ("payroll", "high")
    contra = SourceRow(6, "4900 Sales Returns", "4900", "Sales Returns", {}, "detail", "Income")
    assert suggest(contra).target == "-revenue"
    cogs = SourceRow(7, "Direct labor", None, "Direct labor", {}, "detail", "Cost of Goods Sold")
    assert suggest(cogs).target == "cogs"


def test_unsupported_files_say_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "old.xls"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match=r"\.xlsx"):
        inspect_file(path)
