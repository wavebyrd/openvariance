"""MCP server: OpenVariance as tools any MCP-compatible agent can call.

Runs locally over stdio. The agent app starts it on demand (``openvariance mcp``
or ``uvx openvariance mcp``), so there is no server for anyone to host and
files never leave the user's machine through this process.

The tools keep a small in-memory workspace of named datasets for the
session, so an agent can load actuals and a budget once and then ask several
questions about them.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import __version__, demo, workflows
from .accounts import BALANCE_LAYOUT, LINES
from .checks import Check, balance_check
from .dataset import FinancialData
from .drivers import DriverSet, load_drivers
from .importers import AccountMapping, suggest_mapping
from .importers import import_file as import_data
from .importers import inspect_file as _inspect
from .variance import Thresholds

INSTRUCTIONS = """\
OpenVariance does FP&A variance analysis and three-statement forecasting.

Rules for using it well:
1. Every number you report must come from a tool result. Never calculate,
   estimate or round figures yourself beyond what the tools return.
2. For a private company's files: call inspect_file first, show the user any
   account mapping that is not high confidence, and confirm before import_file.
   Save the confirmed mapping (save_mapping_to) so next month imports in one step.
3. If import_file reports unmapped accounts or failed checks, stop and resolve
   them with the user. Do not work around a check that fails.
4. Use run_variance for "how did we do vs budget/forecast/last year" questions.
   Its bridge explains why the headline moved; lead with that.
5. Variance sign convention: positive = favorable, F/U flags say which.
6. Mention check results (balance sheet balances, bridge reconciles) when you
   present a forecast or bridge.
Start with load_demo if the user just wants to see what this can do.
"""


@dataclass
class Workspace:
    data: dict[str, FinancialData] = field(default_factory=dict)
    drivers: dict[str, dict[str, DriverSet]] = field(default_factory=dict)
    last_variance: workflows.VarianceRun | None = None
    last_forecast: workflows.ForecastRun | None = None

    def get(self, name: str) -> FinancialData:
        if name not in self.data:
            known = ", ".join(self.data) or "none yet"
            raise ValueError(f"No dataset named {name!r}. Loaded datasets: {known}.")
        return self.data[name]

    def describe(self, name: str) -> dict[str, Any]:
        d = self.data[name]
        return {
            "name": name,
            "company": d.name,
            "periods": [d.periods[0], d.periods[-1]] if d.periods else [],
            "period_count": len(d.periods),
            "lines": d.lines,
            "departments": d.departments(),
            "has_drivers": name in self.drivers,
        }


def build_server() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:  # pragma: no cover - mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server  # type: ignore[no-redef,attr-defined]

    try:
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:  # pragma: no cover - mcp 1.x
        from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

    server = Server("openvariance", instructions=INSTRUCTIONS)
    ws = Workspace()

    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a tool whose expected errors reach the agent as plain-English messages."""

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (ValueError, KeyError, OSError, RuntimeError) as exc:
                message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)
                raise ToolError(message) from exc

        server.tool()(wrapper)
        return wrapper

    @tool
    def load_demo() -> dict[str, Any]:
        """Load Demo Co., a fictional software company, as datasets 'actual', 'budget' and
        'history' (actual P&L plus balance sheet). Driver data is attached, so variance
        bridges explain causes (volume, price, mix, headcount, pay rate, spend)."""
        d = demo.load()
        ws.data.update(actual=d.actual, budget=d.budget, history=d.history)
        ws.drivers.update(
            actual=d.actual_drivers, budget=d.budget_drivers, history=d.actual_drivers
        )
        return {
            "loaded": [ws.describe(n) for n in ("actual", "budget", "history")],
            "suggested_next": "run_variance(actual='actual', baseline='budget', period='2026-09')",
        }

    @tool
    def inspect_file(
        path: str,
        sheet: str | None = None,
        period: str | None = None,
        mapping_path: str | None = None,
    ) -> dict[str, Any]:
        """Read a CSV or Excel export without importing it: layout, periods, every account
        row, skipped totals, hints, warnings, and a suggested mapping for each account with
        confidence and reason. `period` is required only for files with departments across
        the top. Pass `mapping_path` to only get suggestions for accounts it doesn't cover."""
        insp = _inspect(_path(path), sheet=sheet, period=period)
        existing = AccountMapping.load(_path(mapping_path)) if mapping_path else None
        return {
            **insp.to_dict(max_rows=150),
            "suggestions": [s.to_dict() for s in suggest_mapping(insp, existing)],
        }

    @tool
    def import_file(
        path: str,
        name: str,
        mapping: dict[str, str] | None = None,
        mapping_path: str | None = None,
        accept: str = "high",
        period: str | None = None,
        sheet: str | None = None,
        save_mapping_to: str | None = None,
        drivers_path: str | None = None,
    ) -> dict[str, Any]:
        """Import a file as dataset `name`. `mapping` gives confirmed targets per account
        label (a line key such as 'payroll', '-revenue' for contra accounts, or 'ignore');
        it is layered over `mapping_path`. Suggestions at or above `accept` confidence
        ('high', 'medium', 'low', or 'none') fill the rest. Returns unmapped accounts and
        tie-out checks against the file's own totals; the dataset is only stored when
        both are clean. `drivers_path` attaches operating drivers for driver bridges."""
        base = AccountMapping.load(_path(mapping_path)) if mapping_path else AccountMapping()
        if mapping:
            base.accounts.update(mapping)
        result = import_data(
            _path(path),
            mapping=base,
            name=name,
            period=period,
            sheet=sheet,
            accept_suggestions=None if accept == "none" else accept,
        )
        out = result.to_dict()
        if save_mapping_to:
            insp = _inspect(_path(path), sheet=sheet, period=period)
            final = AccountMapping(dict(base.accounts), base.sign_convention)
            for s in suggest_mapping(insp, base):
                if s.source not in result.unmapped:
                    final.accounts[s.source] = s.target
            final.save(_path(save_mapping_to))
            out["mapping_saved_to"] = str(_path(save_mapping_to))
        if result.ok:
            ws.data[name] = result.data
            if drivers_path:
                ws.drivers[name] = load_drivers(_path(drivers_path))
            out["stored_as"] = ws.describe(name)
        else:
            out["stored_as"] = None
            out["next_step"] = (
                "Resolve unmapped accounts with the user (pass them in `mapping`) "
                "or investigate failed checks, then import again."
            )
        return out

    @tool
    def load_sec_company(
        ticker: str, name: str | None = None, quarters: int = 12, user_agent: str | None = None
    ) -> dict[str, Any]:
        """Load a US public company's quarterly statements from SEC EDGAR (free, no key) as a
        dataset (default name: the ticker). SEC requires a name and email as user agent:
        pass `user_agent` or set OPENVARIANCE_SEC_USER_AGENT."""
        from .sec import load_company

        company = load_company(ticker, quarters=quarters, user_agent=user_agent)
        key = name or ticker.upper()
        ws.data[key] = company.data
        return {**ws.describe(key), "notes": company.notes}

    @tool
    def list_datasets() -> list[dict[str, Any]]:
        """Datasets loaded in this session."""
        return [ws.describe(n) for n in ws.data]

    @tool
    def run_variance(
        actual: str,
        baseline: str,
        period: str | None = None,
        ytd: bool = False,
        actual_label: str = "Actual",
        baseline_label: str = "Budget",
        materiality_amount: float = 10000,
        materiality_pct: float = 10,
    ) -> dict[str, Any]:
        """Compare two datasets line by line (variance > 0 is favorable) for one period or year to
        date, with department drill-down, materiality flags, a reconciling bridge for the
        headline line (by driver when driver data is attached, otherwise by line) and a
        plain-English summary built from the numbers."""
        run = workflows.run_variance(
            ws.get(actual),
            ws.get(baseline),
            period=period,
            ytd=ytd,
            actual_label=actual_label,
            baseline_label=baseline_label,
            actual_drivers=ws.drivers.get(actual),
            baseline_drivers=ws.drivers.get(baseline),
            thresholds=Thresholds(
                Decimal(str(materiality_amount)), Decimal(str(materiality_pct)) / 100
            ),
        )
        ws.last_variance = run
        return run.to_dict()

    @tool
    def run_forecast(
        dataset: str, periods: int | None = None, assumptions: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """Three-statement forecast from a dataset with a balance sheet, for Base, Upside and
        Downside. Starting assumptions are inferred from recent history (each with its
        source); override any with `assumptions`, e.g. {"revenue_growth": 0.02, "dso": 40}.
        Returns scenario totals and any failed balance or cash checks."""
        run = workflows.run_forecast(
            ws.get(dataset), periods=periods, overrides=dict(assumptions or {})
        )
        ws.last_forecast = run
        return run.to_dict()

    @tool
    def check_balance_sheet(dataset: str) -> list[dict[str, Any]]:
        """Check that assets equal liabilities plus equity in every period that has a
        balance sheet. Use it on imported history before forecasting."""
        d = ws.get(dataset)
        checks: list[Check] = []
        for p in d.periods:
            if d.value("total_assets", p) is None:
                continue
            checks.append(
                balance_check(
                    p,
                    d.value("total_assets", p) or Decimal(0),
                    d.value("total_liabilities_equity", p) or Decimal(0),
                    "Balance sheet balances",
                )
            )
        if not checks:
            raise ValueError(
                f"{dataset!r} has no balance sheet lines "
                f"({', '.join(k for k in BALANCE_LAYOUT if not LINES[k].is_computed)})"
            )
        return [c.to_dict() for c in checks]

    @tool
    def export_excel(path: str, kind: str = "variance") -> dict[str, str]:
        """Save the last variance ('variance') or forecast ('forecast') as an Excel workbook with
        live formulas. Returns the absolute path."""
        from . import excel

        target = _path(path)
        if kind == "forecast":
            if not ws.last_forecast:
                raise ValueError("Run run_forecast first")
            f = ws.last_forecast
            excel.export_forecast(f.results, target, company=f.company, sources=f.inferred.sources)
        else:
            if not ws.last_variance:
                raise ValueError("Run run_variance first")
            v = ws.last_variance
            excel.export_variance(
                v.report, target, bridge=v.bridge, commentary=v.summary, company=v.company
            )
        return {"file": str(target)}

    @tool
    def export_dashboard(path: str) -> dict[str, str]:
        """Save the last variance as a self-contained HTML dashboard (opens offline in any
        browser). Returns the absolute path."""
        if not ws.last_variance:
            raise ValueError("Run run_variance first")
        target = _path(path)
        workflows.export_variance_outputs(ws.last_variance, None, target)
        return {"file": str(target)}

    @tool
    def describe_lines() -> dict[str, Any]:
        """The standard chart of accounts: every line key, label, statement and whether
        higher is favorable. Use these keys in mappings."""
        return {
            "version": __version__,
            "lines": [
                {
                    "key": k,
                    "label": s.label,
                    "statement": s.statement.value,
                    "computed": s.is_computed,
                    "higher_is": {1: "favorable", -1: "unfavorable", 0: "neutral"}[s.direction],
                }
                for k, s in LINES.items()
            ],
            "mapping_targets": "a leaf line key, '-<key>' for contra accounts, or 'ignore'",
        }

    return server


def _path(path: str | None) -> Path:
    if path is None:
        raise ValueError("A path is required")
    return Path(path).expanduser().resolve()


def serve() -> None:
    build_server().run("stdio")
