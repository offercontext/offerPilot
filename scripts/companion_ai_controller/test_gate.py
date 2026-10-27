"""Full offline controller migration gate; skipped nodes never count as passing."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.companion_ai_validation.broker import require_linux
from scripts.companion_ai_validation.test_gate import CompletionLedger


def main() -> int:
    require_linux()
    import pytest

    root = Path(__file__).resolve().parents[2]
    ledger = CompletionLedger()
    code = pytest.main([
        "-q", str(root / "scripts/companion_ai_validation/tests"),
        str(root / "scripts/companion_ai_controller/tests"),
        str(root / "tests/test_companion_ai_platform_contract.py"),
    ], plugins=[ledger])
    report = ledger.report(int(code))
    report.update({"scope": "controller-migration-offline-only",
                   "production_protection_verified": False})
    output = root / "artifacts/companion-controller-offline.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
