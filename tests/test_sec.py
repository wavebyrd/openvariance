from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from openvariance.sec import (
    Fact,
    SecError,
    _user_agent,
    discrete_quarters,
    load_company,
    quarter_label,
)


def fact(start: str, end: str, value: int, filed: str = "2026-01-01") -> Fact:
    return Fact(
        dt.date.fromisoformat(start),
        dt.date.fromisoformat(end),
        Decimal(value),
        dt.date.fromisoformat(filed),
        "10-Q",
        filed,
    )


def test_q4_is_full_year_minus_nine_months() -> None:
    facts = [
        fact("2025-01-01", "2025-03-31", 100),
        fact("2025-01-01", "2025-06-30", 220),
        fact("2025-01-01", "2025-09-30", 350),
        fact("2025-01-01", "2025-12-31", 500),
    ]
    q = discrete_quarters(facts)
    assert [q[d][0] for d in sorted(q)] == [100, 120, 130, 150]
    assert "full year minus 9 months" in q[dt.date(2025, 12, 31)][1]


def test_restated_values_win() -> None:
    facts = [
        fact("2025-04-01", "2025-06-30", 1212788000, "2025-08-01"),
        fact("2025-04-01", "2025-06-30", 1212000000, "2026-08-01"),
    ]
    assert discrete_quarters(facts)[dt.date(2025, 6, 30)][0] == 1212000000


def test_quarter_labels_handle_52_53_week_years() -> None:
    assert quarter_label(dt.date(2025, 9, 27)) == "2025-Q3"
    assert quarter_label(dt.date(2026, 1, 3)) == "2025-Q4"


def test_coreweave_fixture(crwv_facts: Path) -> None:
    co = load_company("CRWV", facts_path=crwv_facts, quarters=8)
    d = co.data
    assert co.name == "CoreWeave, Inc."
    assert d.value("revenue", "2025-Q4") == Decimal(5131000000) - Decimal(3559096000)
    assert d.value("revenue", "2025-Q3") == Decimal(1364676000)
    for p in d.periods:
        if d.value("total_assets", p) is not None:
            assert d.value("total_assets", p) == d.value("total_liabilities_equity", p)
        oi = d.value("operating_income", p)
        assert oi is not None


def test_user_agent_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENVARIANCE_SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecError, match="email"):
        _user_agent(None)
    assert _user_agent("Jane Doe jane@example.com") == "Jane Doe jane@example.com"
