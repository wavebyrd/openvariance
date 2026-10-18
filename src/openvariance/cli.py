"""Command line: ``openvariance <command>``. Every command takes ``--json`` for scripts and agents."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import __version__, demo, workflows
from .format import accounting, compact, percent
from .importers import AccountMapping, inspect_file, suggest_mapping
from .variance import Status, Thresholds

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _color(enabled: bool) -> dict[str, str]:
    keys = ("green", "red", "dim", "bold", "reset")
    return dict(zip(keys, (GREEN, RED, DIM, BOLD, RESET) if enabled else ("",) * 5, strict=True))


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _print_report(run: workflows.VarianceRun, color: bool) -> None:
    c = _color(color)
    rep = run.report
    print(
        f"\n{c['bold']}{run.company}{c['reset']}  {rep.actual_label} vs {rep.baseline_label}, "
        f"{rep.periods[0] if len(rep.periods) == 1 else rep.periods[0] + ' to ' + rep.periods[-1]}\n"
    )
    width = max(len(r.label) + 2 * r.depth for r in rep.rows) + 2
    print(f"{'':{width}}{rep.actual_label:>14}{rep.baseline_label:>14}{'Variance':>14}{'Var %':>9}")
    for r in rep.rows:
        tone = (
            c["green"]
            if r.status is Status.FAVORABLE
            else c["red"]
            if r.status is Status.UNFAVORABLE
            else ""
        )
        label = ("  " * r.depth + r.label).ljust(width)
        if r.is_total:
            label = c["bold"] + label + c["reset"]
        flag = f" {r.status.value}" if r.status.value in ("F", "U") else ""
        print(
            f"{label}{accounting(r.actual):>14}{accounting(r.baseline):>14}"
            f"{tone}{accounting(r.impact):>14}{percent(r.pct):>9}{flag}{c['reset']}"
        )
    if run.bridge is not None:
        b = run.bridge
        print(
            f"\n{c['bold']}{b.metric} bridge{c['reset']} "
            f"{c['dim']}({'drivers, Shapley' if b.method == 'shapley' else 'line items'}){c['reset']}"
        )
        print(f"  {b.start_label:<34}{compact(b.start):>12}")
        for s in b.ranked():
            tone = c["green"] if s.amount > 0 else c["red"]
            print(
                f"  {s.label:<34}{tone}{compact(s.amount, signed=True):>12}{c['reset']}  "
                f"{c['dim']}{s.detail}{c['reset']}"
            )
        print(
            f"  {b.end_label:<34}{compact(b.end):>12}   "
            f"{c['dim']}{'reconciles exactly' if b.reconciles else 'DOES NOT RECONCILE'}{c['reset']}"
        )
    print()
    for sentence in run.summary:
        print(f"  {sentence}")
    print()


def cmd_demo(args: argparse.Namespace) -> int:
    d = demo.load()
    run = workflows.run_variance(
        d.actual,
        d.budget,
        period=args.period or d.report_month,
        ytd=args.ytd,
        actual_drivers=d.actual_drivers,
        baseline_drivers=d.budget_drivers,
        company=d.company,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = workflows.export_variance_outputs(
        run, out / "demo_variance.xlsx", out / "demo_dashboard.html", "Demo Co. sample data"
    )
    fc = workflows.run_forecast(d.history, company=d.company)
    from .excel import export_forecast

    written.append(
        export_forecast(
            fc.results, out / "demo_forecast.xlsx", company=d.company, sources=fc.inferred.sources
        )
    )
    if args.json:
        _emit({**run.to_dict(), "forecast": fc.to_dict(), "files": [str(p) for p in written]})
    else:
        _print_report(run, sys.stdout.isatty())
        print("Wrote:")
        for p in written:
            print(f"  {p}")
        if args.open:
            webbrowser.open((out / "demo_dashboard.html").resolve().as_uri())
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    insp = inspect_file(args.file, sheet=args.sheet, period=args.period)
    existing = AccountMapping.load(args.mapping) if args.mapping else None
    suggestions = suggest_mapping(insp, existing)
    if args.json:
        _emit({**insp.to_dict(), "suggestions": [s.to_dict() for s in suggestions]})
        return 0
    print(f"\n{insp.path}{' [' + insp.sheet + ']' if insp.sheet else ''}")
    print(f"  Layout: {insp.layout}, header on row {insp.header_row}")
    print(
        f"  {'Periods' if insp.layout != 'by_department' else 'Period'}: {', '.join(insp.columns)}"
    )
    print(f"  Accounts found: {len(insp.details)}; totals skipped: {len(insp.totals)}")
    for h in insp.hints:
        print(f"  - {h}")
    for w in insp.warnings:
        print(f"  ! {w}")
    if suggestions:
        print("\nSuggested mapping:")
        for s in suggestions:
            print(f"  {s.source[:46]:<48}-> {s.target:<28}{s.confidence:<7}{s.reason}")
    if args.write_mapping:
        mapping = existing or AccountMapping()
        mapping.accounts.update({s.source: s.target for s in suggestions})
        mapping.save(args.write_mapping)
        print(f"\nSaved mapping to {args.write_mapping}. Review it, then use --mapping.")
    return 0


def _load(
    path: str, mapping: str | None, period: str | None, sheet: str | None, accept: str, label: str
) -> Any:
    result = workflows.load_data(
        path,
        mapping=mapping,
        period=period,
        sheet=sheet,
        accept=None if accept == "none" else accept,
    )
    if result.unmapped:
        names = "\n  ".join(result.unmapped)
        raise SystemExit(
            f"{label}: {len(result.unmapped)} accounts need a mapping before this file can be used:\n"
            f"  {names}\nRun `openvariance inspect {path} --write-mapping map.json`, review it, "
            "then pass --mapping map.json (or --accept medium to take more suggestions)."
        )
    failed = [c for c in result.checks if not c.passed]
    if failed:
        lines = "\n  ".join(f"{c.period}: {c.detail} (off by {c.difference:,})" for c in failed)
        raise SystemExit(
            f"{label}: imported numbers don't tie to the file's own totals:\n  {lines}"
        )
    return result


def cmd_variance(args: argparse.Namespace) -> int:
    actual = _load(args.actual, args.mapping, args.period, args.sheet, args.accept, "Actual")
    baseline = _load(
        args.baseline,
        args.baseline_mapping or args.mapping,
        args.period,
        args.baseline_sheet,
        args.accept,
        "Baseline",
    )
    thresholds = Thresholds(Decimal(args.threshold), Decimal(args.threshold_pct) / 100)
    run = workflows.run_variance(
        actual.data,
        baseline.data,
        period=args.period,
        ytd=args.ytd,
        actual_label=args.actual_label,
        baseline_label=args.baseline_label,
        actual_drivers=workflows.load_driver_file(args.actual_drivers),
        baseline_drivers=workflows.load_driver_file(args.baseline_drivers),
        thresholds=thresholds,
        company=args.company,
    )
    written = workflows.export_variance_outputs(run, args.excel, args.dashboard, args.actual)
    if args.json:
        _emit(
            {
                **run.to_dict(),
                "import_notes": actual.notes + baseline.notes,
                "files": [str(p) for p in written],
            }
        )
    else:
        _print_report(run, sys.stdout.isatty())
        for p in written:
            print(f"Wrote {p}")
        if args.dashboard and args.open:
            webbrowser.open(Path(args.dashboard).resolve().as_uri())
    return 0


def cmd_sec(args: argparse.Namespace) -> int:
    from .sec import load_company

    company = load_company(
        args.ticker, quarters=args.quarters, user_agent=args.user_agent, facts_path=args.facts
    )
    if args.out:
        company.data.to_csv(args.out)
    if args.json:
        rows = {
            p: {k: str(company.data.value(k, p)) for k in company.data.lines}
            for p in company.data.periods
        }
        _emit({**company.to_dict(), "values": rows, **({"file": args.out} if args.out else {})})
        return 0
    d = company.data
    print(f"\n{company.name} (CIK {company.cik}), quarterly, $M\n")
    keys = ["revenue", "gross_profit", "operating_income", "net_income", "cash", "debt"]
    print(f"{'':24}" + "".join(f"{p:>11}" for p in d.periods))
    for k in keys:
        if d.has(k):
            from .accounts import LINES

            print(
                f"{LINES[k].label:24}"
                + "".join(
                    f"{accounting(d.value(k, p), scale=Decimal(1_000_000)):>11}" for p in d.periods
                )
            )
    for n in company.notes:
        print(f"\n  {n}")
    if args.out:
        print(f"\nWrote {args.out}")
    return 0


def cmd_forecast(args: argparse.Namespace) -> int:
    from .excel import export_forecast

    if args.demo:
        d = demo.load()
        history, company = d.history, d.company
    elif args.ticker:
        from .sec import load_company

        sec = load_company(args.ticker, user_agent=args.user_agent, facts_path=args.facts)
        history, company = sec.data, sec.name
    elif args.history:
        history = _load(args.history, args.mapping, None, None, args.accept, "History").data
        company = args.company or history.name
    else:
        raise SystemExit("Choose a source: --demo, --ticker TICKER, or --history FILE")
    overrides = dict(kv.split("=", 1) for kv in args.set or [])
    run = workflows.run_forecast(
        history, periods=args.periods, overrides=overrides, company=company
    )
    if args.excel:
        export_forecast(run.results, args.excel, company=company, sources=run.inferred.sources)
    if args.json:
        _emit({**run.to_dict(), **({"file": args.excel} if args.excel else {})})
        return 0
    print(f"\n{company}: {run.results[0].periods[0]} to {run.results[0].periods[-1]}\n")
    print(f"{'':22}" + "".join(f"{r.scenario:>14}" for r in run.results))
    for key, label in (
        ("revenue", "Revenue"),
        ("ebitda", "EBITDA"),
        ("net_income", "Net income"),
        ("free_cash_flow", "Free cash flow"),
        ("ending_cash", "Ending cash"),
    ):
        print(
            f"{label:22}"
            + "".join(f"{compact(Decimal(r.summary()[key])):>14}" for r in run.results)
        )
    if any(Decimal(r.summary()["peak_revolver"]) > 0 for r in run.results):
        print(
            f"{'Peak revolver':22}"
            + "".join(f"{compact(Decimal(r.summary()['peak_revolver'])):>14}" for r in run.results)
        )
    ok = all(r.balanced for r in run.results)
    print(
        f"\n  Checks: {'balance sheet balances and cash ties in every period' if ok else 'FAILED'}"
    )
    if args.excel:
        print(f"  Wrote {args.excel}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve

    serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openvariance",
        description="FP&A variance analysis that explains why your numbers moved.",
    )
    p.add_argument("--version", action="version", version=f"openvariance {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="Run everything on the built-in Demo Co. data")
    d.add_argument("--out", default="openvariance-demo", help="Folder for the output files")
    d.add_argument("--period", help="Month to report (default 2026-09)")
    d.add_argument("--ytd", action="store_true", help="Year to date through the period")
    d.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_demo)

    i = sub.add_parser("inspect", help="Show how a file will be read and suggest account mappings")
    i.add_argument("file")
    i.add_argument("--sheet")
    i.add_argument("--period", help="Period for files with departments across the top")
    i.add_argument("--mapping", help="Existing mapping file to extend")
    i.add_argument("--write-mapping", metavar="FILE", help="Save suggestions as a mapping file")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_inspect)

    v = sub.add_parser("variance", help="Compare actuals with a budget, forecast or prior period")
    v.add_argument("--actual", required=True, help="Actuals file (CSV or Excel)")
    v.add_argument("--baseline", "--budget", required=True, help="Budget, forecast or prior file")
    v.add_argument("--mapping", help="Account mapping for the actuals (and baseline)")
    v.add_argument("--baseline-mapping", help="Separate mapping for the baseline file")
    v.add_argument("--sheet")
    v.add_argument("--baseline-sheet")
    v.add_argument(
        "--accept",
        default="high",
        choices=["high", "medium", "low", "none"],
        help="Auto-accept mapping suggestions at this confidence or better",
    )
    v.add_argument("--period", help="Period to report, like 2026-09 (default: latest shared)")
    v.add_argument("--ytd", action="store_true", help="Year to date through the period")
    v.add_argument("--actual-label", default="Actual")
    v.add_argument("--baseline-label", default="Budget")
    v.add_argument("--actual-drivers", help="Driver file for the actuals (enables driver bridge)")
    v.add_argument("--baseline-drivers", help="Driver file for the baseline")
    v.add_argument("--threshold", default="10000", help="Materiality, dollars (default 10000)")
    v.add_argument("--threshold-pct", default="10", help="Materiality, percent (default 10)")
    v.add_argument("--company")
    v.add_argument("--excel", metavar="FILE", help="Write an Excel workbook")
    v.add_argument("--dashboard", metavar="FILE", help="Write an HTML dashboard")
    v.add_argument("--open", action="store_true", help="Open the dashboard when done")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_variance)

    s = sub.add_parser("sec", help="Quarterly statements for a public company from SEC filings")
    s.add_argument("ticker")
    s.add_argument("--quarters", type=int, default=12)
    s.add_argument("--out", metavar="CSV", help="Save in the standard format")
    s.add_argument(
        "--user-agent", help="Name and email for SEC (or set OPENVARIANCE_SEC_USER_AGENT)"
    )
    s.add_argument("--facts", help=argparse.SUPPRESS)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sec)

    f = sub.add_parser("forecast", help="Three-statement forecast with base, upside and downside")
    src = f.add_mutually_exclusive_group()
    src.add_argument("--demo", action="store_true", help="Use Demo Co.")
    src.add_argument("--ticker", help="Use a public company's SEC history")
    src.add_argument("--history", help="Use a history file (P&L plus balance sheet)")
    f.add_argument("--mapping")
    f.add_argument("--accept", default="high", choices=["high", "medium", "low", "none"])
    f.add_argument("--periods", type=int, help="Periods to forecast (default: one year)")
    f.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="Override an assumption, e.g. --set revenue_growth=0.03",
    )
    f.add_argument("--company")
    f.add_argument("--user-agent")
    f.add_argument("--facts", help=argparse.SUPPRESS)
    f.add_argument("--excel", metavar="FILE", help="Write the linked Excel model")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_forecast)

    m = sub.add_parser("mcp", help="Run the MCP server so AI agents can use OpenVariance")
    m.set_defaults(func=cmd_mcp)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"openvariance: {message}", file=sys.stderr)
        return 2
    except RuntimeError as exc:  # SEC and other environment problems
        print(f"openvariance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
