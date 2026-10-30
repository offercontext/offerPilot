"""Linux-only mandatory fake suite: every collected node must finish and pass."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.companion_ai_validation.broker import require_linux


class CompletionLedger:
    def __init__(self) -> None:
        self.collected: list[str] = []
        self.finished: dict[str, dict[str, str]] = {}
        self.deselected: list[str] = []

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = [item.nodeid for item in session.items]

    def pytest_deselected(self, items: list[Any]) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        phases = self.finished.setdefault(report.nodeid, {})
        if report.when in phases:
            phases[report.when] = "duplicate"
        else:
            phases[report.when] = report.outcome

    def report(self, exit_code: int) -> dict[str, Any]:
        passed = (exit_code == 0 and bool(self.collected) and not self.deselected
                  and len(set(self.collected)) == len(self.collected)
                  and set(self.finished) == set(self.collected)
                  and all(phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
                          for phases in self.finished.values()))
        return {"passed": passed, "exit_code": exit_code, "collected": self.collected,
                "finished": self.finished, "deselected": self.deselected}


def main() -> int:
    require_linux()
    import pytest

    root = Path(__file__).resolve().parents[2]
    ledger = CompletionLedger()
    code = pytest.main(["-q", str(root / "scripts/companion_ai_validation/tests"),
                        str(root / "experiments/shadow_quote_locator/test_locator.py"),
                        str(root / "experiments/shadow_quote_v2/test_harness.py")], plugins=[ledger])
    report = ledger.report(int(code))
    output = root / "artifacts/companion-ai-offline.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
