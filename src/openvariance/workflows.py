"""High-level workflows shared by the command line and the MCP server.

Each function takes plain inputs (file paths, tickers, period labels) and
returns plain, JSON-ready results, so agents and scripts see exactly the same
behavior.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import commentary, dashboard, excel
from .attribution import Bridge, line_bridge
from .dataset import FinancialData, periods_through
from .drivers import DriverSet, explain, load_drivers, select
from .forecast import (
    ForecastResult,
    InferredAssumptions,
    default_scenarios,
    infer_assumptions,
    periods_per_year,
    run_scenarios,
)
from .importers import ImportResult, import_file
from .variance import Thresholds, VarianceReport, compare


@dataclass
class VarianceRun:
    report: VarianceReport
    bridge: Bridge | None
    summary: list[str]
    company: str

    def to_dict(self) -> dict[str, Any]:
        out = {
            "company": self.company,
            "summary": self.summary,
            "report": self.report.to_dict(),
        }
        if self.bridge is not None:
            out["bridge"] = self.bridge.to_dict()
        return out


def resolve_periods(
    actual: FinancialData, baseline: FinancialData, period: str | None, ytd: bool
) -> list[str]:
    """The periods to compare: one period, year to date through it, or the latest shared one."""
    shared = sorted(set(actual.periods) & set(baseline.periods))
    if not shared:
        raise ValueError(
            f"The two datasets share no periods. Actual covers {_range(actual.periods)}, "
            f"baseline covers {_range(baseline.periods)}."
        )
    end = period or shared[-1]
    if end not in shared:
        raise ValueError(f"{end} isn't in both datasets. Shared periods: {_range(shared)}.")
    return periods_through(shared, end) if ytd else [end]


def _range(periods: Sequence[str]) -> str:
    return f"{periods[0]} to {periods[-1]}" if periods else "nothing"


def run_variance(
    actual: FinancialData,
    baseline: FinancialData,
    *,
    period: str | None = None,
    ytd: bool = False,
    actual_label: str = "Actual",
    baseline_label: str = "Budget",
    actual_drivers: dict[str, DriverSet] | None = None,
    baseline_drivers: dict[str, DriverSet] | None = None,
    thresholds: Thresholds | None = None,
    company: str | None = None,
) -> VarianceRun:
    periods = resolve_periods(actual, baseline, period, ytd)
    report = compare(
        actual,
        baseline,
        periods,
        actual_label=actual_label,
        baseline_label=baseline_label,
        thresholds=thresholds,
    )
    bridge: Bridge | None = None
    headline = report.headline
    if actual_drivers and baseline_drivers:
        bridge = explain(
            select(baseline_drivers, periods),
            select(actual_drivers, periods),
            metric=headline.line,
            start_label=baseline_label,
            end_label=actual_label,
        )
    elif headline.actual is not None and headline.baseline is not None:
        leaves = [
            r
            for r in report.rows
            if not r.is_total and r.impact is not None and _feeds(r.line, headline.line)
        ]
        bridge = line_bridge(
            [(r.label, r.impact) for r in leaves if r.impact is not None],
            metric_label=headline.label,
            start=headline.baseline.quantize(Decimal("0.01")),
            end=headline.actual.quantize(Decimal("0.01")),
            start_label=baseline_label,
            end_label=actual_label,
        )
    return VarianceRun(report, bridge, commentary.summarize(report, bridge), company or actual.name)


def _feeds(leaf: str, total: str) -> bool:
    from .accounts import leaf_components

    return leaf in leaf_components(total)


def export_variance_outputs(
    run: VarianceRun,
    excel_path: str | Path | None,
    dashboard_path: str | Path | None,
    source: str = "",
) -> list[Path]:
    written = []
    if excel_path:
        written.append(
            excel.export_variance(
                run.report,
                excel_path,
                bridge=run.bridge,
                commentary=run.summary,
                company=run.company,
            )
        )
    if dashboard_path:
        html = dashboard.render(
            run.report,
            bridge=run.bridge,
            commentary=run.summary,
            company=run.company,
            source=source,
        )
        written.append(dashboard.write(dashboard_path, html))
    return written


@dataclass
class ForecastRun:
    company: str
    inferred: InferredAssumptions
    results: list[ForecastResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "assumptions": self.inferred.assumptions.to_dict(),
            "assumption_sources": self.inferred.sources,
            "scenarios": [r.summary() for r in self.results],
            "failed_checks": [c.to_dict() for r in self.results for c in r.checks if not c.passed],
        }


def run_forecast(
    history: FinancialData,
    *,
    periods: int | None = None,
    overrides: dict[str, Any] | None = None,
    company: str | None = None,
) -> ForecastRun:
    last = history.periods[-1] if history.periods else None
    if last is None or history.value("total_assets", last) is None:
        raise ValueError(
            "A forecast needs a balance sheet for the latest period (cash, receivables, "
            "PP&E, debt, equity and so on), and this data only has "
            f"{', '.join(history.lines[:6]) or 'no lines'}. Import a balance sheet for "
            f"{last or 'the latest period'} as well, or use a public company's SEC data."
        )
    inferred = infer_assumptions(history)
    base = inferred.assumptions
    if overrides:
        base = base.with_changes(**overrides)
        for key in overrides:
            inferred.sources[key] = "set by user"
        inferred = InferredAssumptions(base, inferred.sources)
    ppy = periods_per_year(history.periods[-1])
    results = run_scenarios(history, default_scenarios(base, ppy), periods or ppy)
    return ForecastRun(company or history.name, inferred, results)


def load_data(
    path: str | Path,
    *,
    mapping: str | Path | None = None,
    name: str | None = None,
    period: str | None = None,
    sheet: str | None = None,
    accept: str | None = "high",
) -> ImportResult:
    return import_file(
        path, mapping=mapping, name=name, period=period, sheet=sheet, accept_suggestions=accept
    )


def load_driver_file(path: str | Path | None) -> dict[str, DriverSet] | None:
    return load_drivers(path) if path else None
