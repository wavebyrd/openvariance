"""OpenVariance: open source FP&A variance analysis that explains why your numbers moved."""

from __future__ import annotations

__version__ = "0.1.0"

from .attribution import Bridge, BridgeStep, build_bridge, shapley
from .dataset import Entry, FinancialData
from .drivers import DriverSet, explain
from .forecast import Assumptions, ForecastResult, Scenario, infer_assumptions, project
from .variance import Thresholds, VarianceReport, VarianceRow, compare

__all__ = [
    "Assumptions",
    "Bridge",
    "BridgeStep",
    "DriverSet",
    "Entry",
    "FinancialData",
    "ForecastResult",
    "Scenario",
    "Thresholds",
    "VarianceReport",
    "VarianceRow",
    "__version__",
    "build_bridge",
    "compare",
    "explain",
    "infer_assumptions",
    "project",
    "shapley",
]
