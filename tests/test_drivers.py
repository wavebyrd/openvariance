from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from openvariance import compare, demo, explain
from openvariance.drivers import (
    aggregate,
    line_metric,
    load_drivers,
    save_drivers,
    select,
    to_inputs,
)


def test_aggregate_is_exactly_additive(demo_data: demo.Demo) -> None:
    months = list(demo_data.actual_drivers.values())
    metric = line_metric("operating_income")
    total = sum((metric(to_inputs(m)) for m in months), Fraction(0))
    assert metric(to_inputs(aggregate(months))) == total


def test_driver_bridge_matches_the_pnl_variance(demo_data: demo.Demo) -> None:
    for periods in (["2026-09"], [f"2026-{m:02d}" for m in range(1, 10)]):
        rep = compare(demo_data.actual, demo_data.budget, periods)
        bridge = explain(
            select(demo_data.budget_drivers, periods), select(demo_data.actual_drivers, periods)
        )
        oi = rep.row("operating_income")
        assert bridge.start == round(oi.baseline, 2) and bridge.end == round(oi.actual, 2)
        assert bridge.reconciles


def test_demo_story_is_explained_by_the_right_drivers(demo_data: demo.Demo) -> None:
    bridge = explain(demo_data.budget_drivers["2026-09"], demo_data.actual_drivers["2026-09"])
    steps = {s.label: s.amount for s in bridge.steps}
    assert steps["Software"] == -90_000
    assert steps["Price"] < 0  # Enterprise discounting
    assert steps["Headcount"] < 0  # early hires
    assert "Engineering +2" in next(s.detail for s in bridge.steps if s.label == "Headcount")


def test_driver_files_round_trip(tmp_path: Path, demo_data: demo.Demo) -> None:
    path = tmp_path / "drivers.csv"
    save_drivers(demo_data.budget_drivers, path)
    assert load_drivers(path) == demo_data.budget_drivers
