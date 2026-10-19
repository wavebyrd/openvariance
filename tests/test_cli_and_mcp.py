from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from openvariance.cli import main
from openvariance.mcp_server import build_server

from .conftest import EXAMPLES


def run(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert main([*args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_demo_command_writes_everything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "demo", "--out", str(tmp_path))
    assert out["bridge"]["reconciles"] is True
    assert {Path(f).name for f in out["files"]} == {
        "demo_variance.xlsx",
        "demo_dashboard.html",
        "demo_forecast.xlsx",
    }
    html = (tmp_path / "demo_dashboard.html").read_text(encoding="utf-8")
    assert "<script" not in html and "Operating income bridge" in html


def test_report_prints_when_stdout_is_not_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows pipes default to cp1252, which has no minus sign or em dash.
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["demo", "--out", str(tmp_path)]) == 0
    out.flush()
    assert "reconciles exactly" in out.buffer.getvalue().decode("utf-8")


def test_variance_from_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(
        capsys,
        "variance",
        "--actual",
        str(EXAMPLES / "demo_pl_quickbooks_2026.csv"),
        "--budget",
        str(EXAMPLES / "demo_budget_2026.xlsx"),
        "--accept",
        "medium",
        "--period",
        "2026-09",
        "--dashboard",
        str(tmp_path / "d.html"),
    )
    assert out["bridge"]["method"] == "line_items" and out["bridge"]["reconciles"]
    assert out["report"]["periods"] == ["2026-09"]


def test_unmapped_accounts_stop_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="need a mapping"):
        main(
            [
                "variance",
                "--actual",
                str(EXAMPLES / "demo_budget_2026.xlsx"),
                "--budget",
                str(EXAMPLES / "demo_budget_2026.xlsx"),
            ]
        )


def test_inspect_and_forecast(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "inspect", str(EXAMPLES / "demo_pl_quickbooks_2026.csv"))
    assert out["layout"] == "wide" and out["suggestions"]
    fc = run(
        capsys,
        "forecast",
        "--demo",
        "--periods",
        "3",
        "--set",
        "revenue_growth=0.01",
        "--excel",
        str(tmp_path / "f.xlsx"),
    )
    assert fc["assumption_sources"]["revenue_growth"] == "set by user"
    assert not fc["failed_checks"]


def test_sec_command_offline(
    crwv_facts: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run(capsys, "sec", "CRWV", "--facts", str(crwv_facts), "--quarters", "4")
    assert out["company"] == "CoreWeave, Inc." and len(out["periods"]) == 4


def call(server: MCPServer, tool: str, **arguments: Any) -> Any:
    result = asyncio.run(server.call_tool(tool, arguments))
    blocks = result[0] if isinstance(result, tuple) else getattr(result, "content", result)
    return json.loads(blocks[0].text)


def test_mcp_tools_end_to_end(tmp_path: Path) -> None:
    server = build_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {
        "load_demo",
        "inspect_file",
        "import_file",
        "run_variance",
        "run_forecast",
        "export_excel",
        "export_dashboard",
        "load_sec_company",
    } <= names
    call(server, "load_demo")
    v = call(server, "run_variance", actual="actual", baseline="budget", period="2026-09")
    assert v["bridge"]["method"] == "shapley" and v["bridge"]["reconciles"]
    f = call(server, "run_forecast", dataset="history", periods=3)
    assert f["failed_checks"] == []
    out = call(server, "export_excel", path=str(tmp_path / "v.xlsx"))
    assert Path(out["file"]).exists()
    imported = call(
        server,
        "import_file",
        path=str(EXAMPLES / "demo_budget_2026.xlsx"),
        name="budget2",
        accept="high",
    )
    assert imported["stored_as"] is None and "Other G&A" in imported["unmapped"]


def test_mcp_errors_reach_the_agent_in_plain_english() -> None:
    server = build_server()
    call(server, "load_demo")
    with pytest.raises(Exception, match="Loaded datasets: actual, budget, history"):
        asyncio.run(server.call_tool("run_variance", {"actual": "nope", "baseline": "budget"}))
    with pytest.raises(Exception, match="needs a balance sheet"):
        asyncio.run(server.call_tool("run_forecast", {"dataset": "actual"}))
