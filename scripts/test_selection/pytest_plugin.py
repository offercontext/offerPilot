from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--selection-catalog", default=None)
    parser.addoption("--selection-report", default=None)
    parser.addoption("--selection-ids", default=None)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    mapping_path = Path(config.rootpath) / "tests/selection-map.json"
    metadata = json.loads(mapping_path.read_bytes()).get("test_metadata", {}) if mapping_path.exists() else {}
    catalog = []
    for item in items:
        path = item.nodeid.split("::")[0].replace("\\", "/")
        markers = {m.name for m in item.iter_markers() if m.name in {"p0", "p1", "p2"}}
        entry = metadata.get(path, {})
        priority = entry.get("priority")
        if len(markers) > 1 or (markers and priority and markers != {priority}):
            raise pytest.UsageError(f"conflicting test priority: {item.nodeid}")
        if not markers and priority:
            item.add_marker(getattr(pytest.mark, priority))
            markers = {priority}
        catalog.append({"id": item.nodeid, "file": path, "surface": "backend",
                        "priority": next(iter(markers)) if markers else "unknown",
                        "modules": entry.get("modules", []), "requires": entry.get("requires", []),
                        "risk_tags": entry.get("risk_tags", []), "level": entry.get("level", "test"),
                        "estimated_seconds": entry.get("estimated_seconds")})
    output = config.getoption("--selection-catalog")
    if output:
        Path(output).write_text(json.dumps(catalog, sort_keys=True), encoding="utf-8")
    selected_path = config.getoption("--selection-ids")
    if selected_path:
        expected = json.loads(Path(selected_path).read_bytes())
        available = {item.nodeid for item in items}
        if not expected or len(expected) != len(set(expected)) or set(expected) - available:
            raise pytest.UsageError("invalid frozen selected node IDs")
        selected = set(expected)
        deselected = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        config.hook.pytest_deselected(items=deselected)


_REPORTS: list[dict[str, Any]] = []


def pytest_runtest_logreport(report: Any) -> None:
    if report.when == "call" or (report.when in {"setup", "teardown"} and report.outcome != "passed"):
        reason = str(report.longrepr[2]).removeprefix("Skipped: ") if report.skipped else None
        _REPORTS.append({"id": report.nodeid, "when": report.when, "outcome": report.outcome,
                         "reason": reason})


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    output = session.config.getoption("--selection-report")
    if output:
        Path(output).write_text(json.dumps({"reports": _REPORTS, "exit_code": exitstatus}), encoding="utf-8")
