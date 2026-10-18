from __future__ import annotations

from pathlib import Path

import pytest

from openvariance import demo

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def demo_data() -> demo.Demo:
    return demo.load()


@pytest.fixture(scope="session")
def crwv_facts() -> Path:
    return FIXTURES / "crwv_companyfacts.json"
