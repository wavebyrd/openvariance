"""Plain-English variance commentary, generated deterministically from the numbers.

No language model is involved: every sentence is a template filled from the
report and bridge, so the commentary can never state a number the engine did
not compute. An agent can rewrite it in its own words; the facts stay fixed.
"""

from __future__ import annotations

from decimal import Decimal

from .attribution import Bridge, BridgeStep
from .format import compact, percent
from .variance import Status, VarianceReport


def headline(report: VarianceReport) -> str:
    row = report.headline
    if row.actual is None or row.impact is None:
        return f"{row.label}: no data for {report.actual_label.lower()}."
    if row.status is Status.ON_PLAN:
        return f"{row.label} was {compact(row.actual)}, on {report.baseline_label.lower()}."
    direction = "above" if row.impact > 0 else "below"
    pct = f" ({percent(abs(row.pct))})" if row.pct is not None else ""
    return (
        f"{row.label} was {compact(row.actual)}, {compact(abs(row.impact))}{pct} "
        f"{direction} {report.baseline_label.lower()}."
    )


def drivers_sentence(bridge: Bridge, top: int = 3) -> str:
    ranked = [s for s in bridge.ranked() if s.amount != 0]
    if not ranked:
        return f"Nothing moved {bridge.metric.lower()} versus {bridge.start_label.lower()}."
    change = bridge.change
    against = [s for s in ranked if (s.amount < 0) == (change < 0) or change == 0][:top]
    offsets = [s for s in ranked if s not in against and (s.amount < 0) != (change < 0)][:2]

    def part(step: BridgeStep) -> str:
        return f"{step.label.lower()} ({compact(step.amount, signed=True)})"

    text = "The biggest drivers were " + _join([part(s) for s in against])
    if offsets and change != 0:
        text += ", partly offset by " + _join([part(s) for s in offsets])
    return text + "."


def material_sentence(report: VarianceReport, top: int = 3) -> str:
    rows = sorted(report.material_rows(), key=lambda r: abs(r.impact or Decimal(0)), reverse=True)
    if not rows:
        return "No individual line passed the materiality thresholds."
    parts = []
    for r in rows[:top]:
        word = "favorable" if r.status is Status.FAVORABLE else "unfavorable"
        pct = f", {percent(abs(r.pct))}" if r.pct is not None else ""
        parts.append(f"{r.label.lower()} {compact(r.impact, signed=True)} ({word}{pct})")
    return "Lines to look at: " + _join(parts) + "."


def summarize(report: VarianceReport, bridge: Bridge | None = None) -> list[str]:
    sentences = [headline(report)]
    if bridge is not None:
        sentences.append(drivers_sentence(bridge))
    sentences.append(material_sentence(report))
    return sentences


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
