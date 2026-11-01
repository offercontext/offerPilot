"""Cross-platform contract only; Linux fake durability tests live outside testpaths."""
from pathlib import Path

import pytest

from scripts.companion_ai_validation import broker, run


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_platform_cannot_open_ledger_or_transport(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(broker.sys, "platform", platform)
    monkeypatch.setattr(broker.continuation, "lineage_fields",
                        lambda: pytest.fail("predecessor must not be read on unsupported platforms"))
    with pytest.raises(broker.GateError, match="unsupported_platform"):
        broker.Broker(tmp_path / "must-not-exist", lambda _: (200, b""), "a" * 40)
    with pytest.raises(broker.GateError, match="unsupported_platform"):
        broker.HTTPSUpstream("synthetic-unused-key")
    with pytest.raises(broker.GateError, match="unsupported_platform"):
        run.execute(lambda _: (200, b""), "a" * 40, tmp_path / "must-not-create")
    assert not list(tmp_path.iterdir())


def test_main_rejects_platform_before_reading_admission_or_key(monkeypatch, capsys):
    monkeypatch.setattr(broker.sys, "platform", "win32")
    monkeypatch.setattr(run, "check_admission", lambda *_: pytest.fail("admission must not be read"))
    assert run.main() == 1
    assert '"error_code": "unsupported_platform"' in capsys.readouterr().out


def test_linux_gate_cannot_be_silently_replaced_by_default_windows_collection():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/companion-real-ai.yml").read_text()
    assert "runs-on: ubuntu-latest" in workflow
    assert "python -m scripts.companion_ai_validation.test_gate" in workflow
    assert "tests/test_companion_ai_platform_contract.py" in workflow
    for name in ("broker", "driver", "preflight", "runner", "continuation"):
        assert not (root / f"tests/test_companion_ai_{name}.py").exists()
        assert (root / f"scripts/companion_ai_validation/tests/test_companion_ai_{name}.py").is_file()
