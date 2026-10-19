from __future__ import annotations

from decimal import Decimal

from openvariance import FinancialData, compare
from openvariance.attribution import line_bridge
from openvariance.commentary import drivers_sentence, headline, material_sentence
from openvariance.dashboard import render
from openvariance.format import accounting, compact, percent


def test_compact_money() -> None:
    assert compact(Decimal("1188691")) == "$1.19M"
    assert compact(Decimal("22000"), signed=True) == "+$22.0K"
    assert compact(Decimal("-90000"), signed=True) == "−$90.0K"
    assert compact(Decimal("950")) == "$950"
    assert compact(None) == "—"


def test_accounting_format() -> None:
    assert accounting(Decimal("-1234.5")) == "(1,235)"
    assert accounting(Decimal("4753442"), scale=Decimal(1000)) == "4,753"
    assert accounting(Decimal(0)) == "0"
    assert accounting(None) == "—"


def test_percent_format() -> None:
    assert percent(Decimal("0.125")) == "12.5%"
    assert percent(Decimal("-0.125")) == "(12.5%)"
    assert percent(Decimal("-0.125"), signed=True) == "−12.5%"
    assert percent(Decimal("0.05"), signed=True) == "+5.0%"
    assert percent(None) == "n/m"


ACTUAL = FinancialData.from_records(
    "Co",
    [
        ("2026-01", "revenue", 1100),
        ("2026-01", "cogs", 450),
        ("2026-01", "payroll", 420),
        ("2026-01", "software", 0),
    ],
)
BUDGET = FinancialData.from_records(
    "Co",
    [
        ("2026-01", "revenue", 1000),
        ("2026-01", "cogs", 400),
        ("2026-01", "payroll", 400),
        ("2026-01", "software", 50),
    ],
)
REPORT = compare(ACTUAL, BUDGET, ["2026-01"])
BRIDGE = line_bridge(
    [
        ("Revenue", Decimal(100)),
        ("Cost of sales", Decimal(-50)),
        ("Software", Decimal(50)),
        ("Payroll", Decimal(-20)),
    ],
    metric_label="Operating income",
    start=Decimal(150),
    end=Decimal(230),
)


def test_headline_sentence() -> None:
    assert headline(REPORT) == "Operating income was $230, $80 (53.3%) above budget."


def test_drivers_sentence_names_offsets() -> None:
    assert drivers_sentence(BRIDGE) == (
        "The biggest drivers were revenue (+$100) and software (+$50), "
        "partly offset by cost of sales (−$50) and payroll (−$20)."
    )


def test_material_sentence_skips_immaterial_lines() -> None:
    text = material_sentence(REPORT)
    assert text.startswith("Lines to look at: revenue +$100 (favorable, 10.0%)")
    assert "payroll" not in text


def test_dashboard_escapes_html_and_has_no_scripts() -> None:
    html = render(REPORT, bridge=BRIDGE, commentary=["x < y"], company="A & B <Co>")
    assert "A &amp; B &lt;Co&gt;" in html and "x &lt; y" in html
    assert "<script" not in html
    assert "<svg" in html and "Bridge reconciles" in html
