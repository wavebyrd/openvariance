"""Reconciliation checks. A check never changes numbers; it reports whether they tie."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

TOLERANCE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    period: str
    passed: bool
    difference: Decimal
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "period": self.period,
            "passed": self.passed,
            "difference": str(self.difference.quantize(TOLERANCE)),
            "detail": self.detail,
        }


def balance_check(period: str, assets: Decimal, liabilities_equity: Decimal, name: str) -> Check:
    diff = assets - liabilities_equity
    ok = abs(diff) <= TOLERANCE
    detail = (
        "Assets equal liabilities plus equity"
        if ok
        else f"Assets differ from liabilities plus equity by {diff.quantize(TOLERANCE):,}"
    )
    return Check(name, period, ok, diff, detail)


def cash_check(period: str, opening: Decimal, change: Decimal, closing: Decimal) -> Check:
    diff = opening + change - closing
    ok = abs(diff) <= TOLERANCE
    detail = (
        "Opening cash plus net change equals closing cash"
        if ok
        else f"Cash roll-forward is off by {diff.quantize(TOLERANCE):,}"
    )
    return Check("Cash ties to cash flow statement", period, ok, diff, detail)


def tie_check(name: str, period: str, expected: Decimal, actual: Decimal, detail: str) -> Check:
    diff = actual - expected
    return Check(name, period, abs(diff) <= TOLERANCE, diff, detail)
