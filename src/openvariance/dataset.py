"""FinancialData: a set of amounts by period, line and (optionally) department."""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .accounts import LINES, Kind, line
from .money import ZERO, to_decimal


@dataclass(frozen=True, slots=True)
class Entry:
    period: str
    line: str
    amount: Decimal
    department: str | None = None


@dataclass
class FinancialData:
    """Amounts in natural magnitudes (see :mod:`openvariance.accounts`).

    Periods are sortable labels: ``2026-09`` for months, ``2026-Q3`` for quarters.
    Only leaf lines are stored. Computed lines (gross profit, operating income,
    totals) are always derived on read, so they can never disagree with their parts.
    """

    name: str
    entries: list[Entry] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for entry in self.entries:
            if line(entry.line).is_computed:
                raise ValueError(f"{entry.line!r} is a computed line; store its components instead")
        self._index: dict[tuple[str, str], list[Entry]] = defaultdict(list)
        for entry in self.entries:
            self._index[(entry.line, entry.period)].append(entry)

    @classmethod
    def from_records(
        cls,
        name: str,
        records: Iterable[Sequence[Any]],
        meta: dict[str, str] | None = None,
    ) -> FinancialData:
        """Build from ``(period, line, amount[, department])`` tuples."""
        entries = [
            Entry(str(r[0]), str(r[1]), to_decimal(r[2]), (r[3] or None) if len(r) > 3 else None)
            for r in records
        ]
        return cls(name, entries, dict(meta or {}))

    @property
    def periods(self) -> list[str]:
        return sorted({e.period for e in self.entries})

    @property
    def lines(self) -> list[str]:
        present = {e.line for e in self.entries}
        return [key for key in LINES if key in present]

    def departments(self, key: str | None = None) -> list[str]:
        return sorted(
            {e.department for e in self.entries if e.department and (key is None or e.line == key)}
        )

    def has(self, key: str, periods: Sequence[str] | None = None) -> bool:
        """True if the line (or any of its components) has data in the periods."""
        return self.value(key, periods) is not None

    def value(
        self,
        key: str,
        periods: str | Sequence[str] | None = None,
        department: str | None = None,
    ) -> Decimal | None:
        """Amount for a line over the given periods.

        Flows are summed across periods; stocks take the last period. Returns
        ``None`` (not zero) when there is no data, so missing data stays visible.
        A computed line is ``None`` only when every component is missing.
        """
        selected = self._select_periods(periods)
        target = line(key)
        if target.is_computed:
            total = ZERO
            seen = False
            for sign, component in target.formula:
                part = self.value(component, selected, department)
                if part is not None:
                    seen = True
                    total += sign * part
            return total if seen else None
        if target.kind is Kind.STOCK:
            selected = selected[-1:]
        amounts = [
            e.amount
            for p in selected
            for e in self._index.get((key, p), ())
            if department is None or e.department == department
        ]
        return sum(amounts, ZERO) if amounts else None

    def _select_periods(self, periods: str | Sequence[str] | None) -> list[str]:
        if periods is None:
            return self.periods
        if isinstance(periods, str):
            return [periods]
        return sorted(periods)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def filter(self, periods: Sequence[str]) -> FinancialData:
        keep = set(periods)
        return FinancialData(self.name, [e for e in self.entries if e.period in keep], self.meta)

    def merged(self, other: FinancialData, name: str | None = None) -> FinancialData:
        return FinancialData(name or self.name, [*self.entries, *other.entries], self.meta)

    def to_csv(self, path: str | Path) -> None:
        """Write the standard long format: period, line, amount, department."""
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["period", "line", "amount", "department"])
            for e in sorted(self.entries, key=lambda e: (e.period, e.line, e.department or "")):
                writer.writerow([e.period, e.line, str(e.amount), e.department or ""])

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None) -> FinancialData:
        """Read the standard long format written by :meth:`to_csv`."""
        path = Path(path)
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = {"period", "line", "amount"} - set(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    f"{path.name} is not in the standard format (missing {sorted(missing)}). "
                    "Use `openvariance inspect` and `openvariance import` for other layouts."
                )
            entries = [
                Entry(
                    row["period"],
                    row["line"],
                    to_decimal(row["amount"]),
                    row.get("department") or None,
                )
                for row in reader
            ]
        return cls(name or path.stem, entries)


def periods_through(periods: Sequence[str], end: str, start: str | None = None) -> list[str]:
    """Periods from ``start`` (default: first period in the same year) through ``end``."""
    year = end[:4]
    lower = start or f"{year}"
    return [p for p in sorted(periods) if lower <= p <= end and (start or p.startswith(year))]
