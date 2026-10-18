"""A self-contained HTML dashboard: one file, no server, no scripts, works offline.

Everything is inline (CSS and an SVG waterfall), so the file can be opened
from disk, emailed, or printed. Department drill-down uses native
``<details>`` elements, so it works without JavaScript.
"""

from __future__ import annotations

import datetime as dt
import html
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from . import __version__, theme
from .attribution import Bridge
from .checks import Check
from .format import accounting, compact, percent
from .variance import Status, VarianceReport, VarianceRow

CSS = f"""
:root {{
  --green: {theme.GREEN}; --green-dark: {theme.GREEN_DARK}; --tint: {theme.GREEN_TINT};
  --rule: {theme.GREEN_RULE}; --ink: {theme.INK}; --muted: {theme.INK_MUTED};
  --fav: {theme.FAVORABLE}; --fav-bg: {theme.FAVORABLE_BG}; --unf: {theme.UNFAVORABLE};
  --unf-bg: {theme.UNFAVORABLE_BG};
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #FFFFFF; color: var(--ink);
  font: 14px/1.5 "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  font-variant-numeric: tabular-nums; }}
header {{ background: var(--green); color: #FFFFFF; padding: 14px 24px; display: flex;
  align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
header .brand {{ font-weight: 600; font-size: 17px; letter-spacing: .2px; }}
header .meta {{ color: #DCEFE3; font-size: 13px; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 24px 16px 40px; }}
h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
h2 {{ font-size: 15px; font-weight: 600; margin: 28px 0 10px; color: var(--green-dark); }}
.sub {{ color: var(--muted); margin: 0 0 18px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
.kpi {{ background: var(--tint); border-radius: 8px; padding: 14px 16px; }}
.kpi .label {{ color: var(--muted); font-size: 12.5px; }}
.kpi .value {{ font-size: 24px; font-weight: 600; margin: 2px 0; }}
.kpi .delta {{ font-size: 12.5px; }}
.fav {{ color: var(--fav); }} .unf {{ color: var(--unf); }} .muted {{ color: var(--muted); }}
.summary {{ background: var(--tint); border-left: 3px solid var(--green); padding: 12px 16px;
  margin-top: 16px; }}
.summary p {{ margin: 4px 0; }}
.chart {{ border: 1px solid var(--rule); border-radius: 8px; padding: 12px 8px 4px; }}
.legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin: 0 0 6px 8px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
.sw {{ width: 11px; height: 11px; border-radius: 2px; display: inline-block; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: var(--green); color: #FFFFFF; font-weight: 600; font-size: 12.5px;
  text-align: right; padding: 7px 10px; }}
th:first-child, td:first-child {{ text-align: left; }}
td {{ padding: 6px 10px; border-bottom: 1px solid var(--rule); text-align: right; }}
tr.total td {{ font-weight: 600; border-top: 1px solid var(--green); }}
tr.headline td {{ border-top: 2px solid var(--green); border-bottom: 2px solid var(--green); }}
tr.dept td {{ color: var(--muted); font-size: 13px; background: #FBFDFC; }}
tr.dept td:first-child {{ padding-left: 34px; }}
.d1 td:first-child {{ padding-left: 24px; }}
.badge {{ display: inline-block; min-width: 20px; text-align: center; font-size: 11px;
  font-weight: 700; border-radius: 4px; padding: 1px 6px; }}
.badge.F {{ background: var(--fav-bg); color: #0B5A2F; }}
.badge.U {{ background: var(--unf-bg); color: #7A1A12; }}
.flag {{ color: var(--unf); font-size: 11px; margin-left: 6px; }}
details summary {{ cursor: pointer; color: var(--muted); font-size: 12.5px; }}
.bridge td:last-child {{ text-align: left; color: var(--muted); font-size: 12.5px; }}
.checks {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }}
.check {{ font-size: 12.5px; border-radius: 999px; padding: 3px 10px; }}
.check.ok {{ background: var(--fav-bg); color: #0B5A2F; }}
.check.bad {{ background: var(--unf-bg); color: #7A1A12; }}
footer {{ color: var(--muted); font-size: 12px; margin-top: 36px; border-top: 1px solid var(--rule);
  padding-top: 12px; }}
@media print {{ header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .chart, table {{ break-inside: avoid; }} }}
"""


def _e(text: object) -> str:
    return html.escape(str(text))


def _kpi(row: VarianceRow, baseline_label: str) -> str:
    cls = {Status.FAVORABLE: "fav", Status.UNFAVORABLE: "unf"}.get(row.status, "muted")
    pct = f" ({percent(row.pct, signed=True)})" if row.pct is not None else ""
    status = f" · {row.status.value}" if row.status.value in ("F", "U") else ""
    return (
        f'<div class="kpi"><div class="label">{_e(row.label)}</div>'
        f'<div class="value">{_e(compact(row.actual))}</div>'
        f'<div class="delta {cls}">{_e(compact(row.impact, signed=True))}{_e(pct)} vs '
        f"{_e(baseline_label.lower())}{status}</div></div>"
    )


def waterfall_svg(bridge: Bridge, width: int = 1040, height: int = 300) -> str:
    """An accessible SVG waterfall. Unfavorable bars are hatched as well as red."""
    items: list[tuple[str, Decimal, Decimal, str]] = [
        (bridge.start_label, Decimal(0), bridge.start, "total")
    ]
    running = bridge.start
    for step in bridge.steps:
        items.append(
            (step.label, running, running + step.amount, "up" if step.amount >= 0 else "down")
        )
        running += step.amount
    items.append((bridge.end_label, Decimal(0), bridge.end, "total"))
    values = [v for _, a, b, _ in items for v in (a, b)]
    lo, hi = min(min(values), Decimal(0)), max(max(values), Decimal(0))
    if hi == lo:
        hi = lo + 1
    # Bridges move a little around a large total. When everything is positive and the
    # movement is small, start the axis above zero (and say so) so the drivers are visible.
    moving = [v for _, a, b, kind in items if kind != "total" for v in (a, b)] or values
    axis_note = ""
    levels = moving + [b for _, _, b, kind in items if kind == "total"]
    if min(levels) > 0 and min(moving) > (max(levels) - min(moving)) * 2:
        span = max(levels) - min(moving)
        lo = max(Decimal(0), min(moving) - span * Decimal("1.2"))
        ticks = _ticks(lo, hi)
        if len(ticks) >= 2:
            tick = ticks[1] - ticks[0]
            lo = max(Decimal(0), (lo / tick).to_integral_value(rounding="ROUND_FLOOR") * tick)
        axis_note = f"Axis starts at {compact(lo)}"
    pad = (hi - lo) * Decimal("0.08")
    hi = hi + pad
    if lo < 0:
        lo = lo - pad
    left, right, top, bottom = 70, 10, 22, 54
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / len(items)
    bar_w = min(56.0, slot * 0.62)

    def y(v: Decimal) -> float:
        return top + float((hi - v) / (hi - lo)) * plot_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{_e(_aria(bridge))}" xmlns="http://www.w3.org/2000/svg">',
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="'
        f'{theme.UNFAVORABLE_BAR}"/><line x1="0" y1="0" x2="0" y2="6" stroke="#FFFFFF" '
        'stroke-opacity=".55" stroke-width="2.5"/></pattern></defs>',
    ]
    for tick in _ticks(lo, hi):
        ty = y(tick)
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{ty:.1f}" y2="{ty:.1f}" '
            f'stroke="{"#9DB5A7" if tick == 0 else "#E6EFE9"}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{ty + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="{theme.INK_MUTED}">{_e(compact(tick))}</text>'
        )
    for i, (label, a, b, kind) in enumerate(items):
        x = left + slot * i + (slot - bar_w) / 2
        y1, y2 = y(max(a, b)), y(max(min(a, b), lo))
        h = max(y2 - y1, 1.0)
        fill = {"total": theme.GREEN_DARK, "up": theme.FAVORABLE_BAR, "down": "url(#hatch)"}[kind]
        amount = b - a if kind != "total" else b
        parts.append(
            f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'rx="3" fill="{fill}"><title>{_e(label)}: '
            f"{_e(compact(amount, signed=kind != 'total'))}</title></rect>"
        )
        ty = y1 - 6 if (kind != "down") else y2 + 14
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{ty:.1f}" text-anchor="middle" '
            f'font-size="11.5" font-weight="600" fill="{theme.INK}">'
            f"{_e(compact(amount, signed=kind != 'total'))}</text>"
        )
        name = label if len(label) <= 16 else label[:15] + "…"
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - bottom + 18}" text-anchor="middle" '
            f'font-size="11.5" fill="{theme.INK_MUTED}">{_e(name)}</text>'
        )
        if i < len(items) - 1:
            nx = left + slot * (i + 1) + (slot - bar_w) / 2
            parts.append(
                f'<line x1="{x + bar_w:.1f}" x2="{nx:.1f}" y1="{y(b):.1f}" y2="{y(b):.1f}" '
                f'stroke="#9DB5A7" stroke-dasharray="3 3" stroke-width="1"/>'
            )
    if axis_note:
        parts.append(
            f'<text x="{left}" y="{height - 6}" font-size="11" fill="{theme.INK_MUTED}">'
            f"{_e(axis_note)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _aria(bridge: Bridge) -> str:
    steps = "; ".join(f"{s.label} {compact(s.amount, signed=True)}" for s in bridge.steps)
    return (
        f"Waterfall of {bridge.metric} from {bridge.start_label} {compact(bridge.start)} to "
        f"{bridge.end_label} {compact(bridge.end)}: {steps}"
    )


def _ticks(lo: Decimal, hi: Decimal, count: int = 5) -> list[Decimal]:
    raw = (hi - lo) / count
    magnitude = Decimal(10) ** (len(str(int(abs(raw)))) - 1) if raw >= 1 else Decimal(1)
    step = next(
        m * magnitude for m in map(Decimal, ("1", "2", "2.5", "5", "10")) if m * magnitude >= raw
    )
    start = (lo / step).to_integral_value(rounding="ROUND_CEILING") * step
    ticks, t = [], start
    while t <= hi:
        ticks.append(t)
        t += step
    return ticks


def _table(report: VarianceReport, scale: Decimal, unit: str) -> str:
    head = (
        f"<table><thead><tr><th>Line ({_e(unit)})</th><th>{_e(report.actual_label)}</th>"
        f"<th>{_e(report.baseline_label)}</th><th>Variance</th><th>Var %</th><th>F/U</th>"
        "</tr></thead><tbody>"
    )
    body = []
    headline = report.headline.line
    for row in report.rows:
        classes = []
        if row.is_total:
            classes.append("total")
        if row.line == headline:
            classes.append("headline")
        if row.depth:
            classes.append(f"d{row.depth}")
        body.append(_tr(row, scale, " ".join(classes)))
        if row.departments:
            body.extend(_tr(d, scale, "dept", label=d.department or "") for d in row.departments)
    return head + "".join(body) + "</tbody></table>"


def _tr(row: VarianceRow, scale: Decimal, cls: str, label: str | None = None) -> str:
    color = {Status.FAVORABLE: "fav", Status.UNFAVORABLE: "unf"}.get(row.status, "")
    badge = (
        f'<span class="badge {row.status.value}">{row.status.value}</span>'
        if row.status.value in ("F", "U")
        else ""
    )
    flag = (
        '<span class="flag" title="Passes materiality thresholds">●</span>'
        if row.material and not row.is_total and cls != "dept"
        else ""
    )
    return (
        f'<tr class="{cls}"><td>{_e(label if label is not None else row.label)}{flag}</td>'
        f"<td>{accounting(row.actual, scale=scale)}</td>"
        f"<td>{accounting(row.baseline, scale=scale)}</td>"
        f'<td class="{color}">{accounting(row.impact, scale=scale)}</td>'
        f'<td class="{color}">{percent(row.pct)}</td><td>{badge}</td></tr>'
    )


def render(
    report: VarianceReport,
    *,
    bridge: Bridge | None = None,
    commentary: Sequence[str] = (),
    checks: Sequence[Check] = (),
    company: str = "",
    source: str = "",
) -> str:
    """The dashboard as an HTML string."""
    scale, unit = _scale(report)
    tiles = [
        k
        for k in ("revenue", "gross_profit", "ebitda", "operating_income", "net_income")
        if any(r.line == k for r in report.rows)
    ]
    if "operating_income" in tiles and "ebitda" in tiles:
        tiles.remove("ebitda")
    kpis = "".join(_kpi(report.row(k), report.baseline_label) for k in tiles[:4])
    summary = "".join(f"<p>{_e(s)}</p>" for s in commentary)
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(company or 'OpenVariance')} · {_e(report.actual_label)} vs "
        f"{_e(report.baseline_label)}</title><style>{CSS}</style></head><body>",
        f'<header><div class="brand">OpenVariance</div><div class="meta">{_e(company)}'
        f"{' · ' if company else ''}{_e(_span(report))} · {_e(report.actual_label)} vs "
        f"{_e(report.baseline_label)}</div></header><main>",
        f"<h1>{_e(company or report.title)}</h1>",
        f'<p class="sub">{_e(report.actual_label)} vs {_e(report.baseline_label)}, '
        f"{_e(_span(report))}</p>",
        f'<section class="kpis">{kpis}</section>',
    ]
    if summary:
        parts.append(f'<section class="summary">{summary}</section>')
    if bridge is not None:
        parts += [
            f"<h2>{_e(bridge.metric)} bridge, {_e(bridge.start_label.lower())} to "
            f"{_e(bridge.end_label.lower())}</h2>",
            '<div class="chart"><div class="legend">'
            f'<span><i class="sw" style="background:{theme.GREEN_DARK}"></i>Total</span>'
            f'<span><i class="sw" style="background:{theme.FAVORABLE_BAR}"></i>Favorable</span>'
            f'<span><i class="sw" style="background:{theme.UNFAVORABLE_BAR};background-image:'
            "repeating-linear-gradient(45deg,transparent 0 3px,rgba(255,255,255,.55) 3px 5px)"
            '"></i>Unfavorable</span></div>',
            waterfall_svg(bridge.condensed()),
            "</div>",
            _bridge_table(bridge),
        ]
    parts += [f"<h2>Variance by line</h2>{_table(report, scale, unit)}"]
    all_checks = list(checks)
    if bridge is not None:
        all_checks.append(
            Check(
                "Bridge reconciles",
                "",
                bridge.reconciles,
                bridge.residual,
                "Driver impacts add up to the total change",
            )
        )
    if all_checks:
        pills = "".join(
            f'<span class="check {"ok" if c.passed else "bad"}">'
            f"{'✓' if c.passed else '✕'} {_e(c.name)}{(' ' + _e(c.period)) if c.period else ''}</span>"
            for c in all_checks
        )
        parts.append(f'<h2>Checks</h2><div class="checks">{pills}</div>')
    today = dt.date.today()
    parts.append(
        f"<footer>Generated by OpenVariance {__version__} on {today:%B} {today.day}, {today.year}"
        f"{'. Source: ' + _e(source) if source else ''}. Variance is favorable-signed: positive helps "
        "profit. Var % divides by the absolute baseline; n/m means the baseline is zero."
        "</footer></main></body></html>"
    )
    return "".join(parts)


def _bridge_table(bridge: Bridge) -> str:
    rows = "".join(
        f'<tr><td>{_e(s.label)}</td><td class="{"fav" if s.amount > 0 else "unf"}">'
        f"{_e(compact(s.amount, signed=True))}</td><td>{_e(s.detail)}</td></tr>"
        for s in bridge.ranked()
    )
    return (
        '<details style="margin-top:10px"><summary>Driver detail</summary>'
        f'<table class="bridge"><thead><tr><th>Driver</th><th>Impact</th><th>What changed</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></details>"
    )


def _span(report: VarianceReport) -> str:
    p = report.periods
    return p[0] if len(p) == 1 else f"{p[0]} to {p[-1]}"


def _scale(report: VarianceReport) -> tuple[Decimal, str]:
    biggest = max((abs(r.actual or Decimal(0)) for r in report.rows), default=Decimal(0))
    if biggest >= 100_000_000:
        return Decimal(1_000_000), "$M"
    if biggest >= 100_000:
        return Decimal(1_000), "$K"
    return Decimal(1), "$"


def write(path: str | Path, html_text: str) -> Path:
    path = Path(path)
    path.write_text(html_text, encoding="utf-8")
    return path
