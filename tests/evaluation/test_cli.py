from pathlib import Path

from typer.testing import CliRunner

from offerpilot.evaluation.cli import app
from tests.evaluation.test_contracts import ROOT


def test_validate_has_no_product_start_and_rejects_real(tmp_path: Path, monkeypatch) -> None:
    from offerpilot import api
    monkeypatch.setattr(api, "create_app", lambda *a, **k: (_ for _ in ()).throw(AssertionError("started")))
    result = CliRunner().invoke(app, ["validate", "--suite", str(ROOT / "evals/suites/harness-p0-v1.json")])
    assert result.exit_code == 0 and "release_eligible=false" in result.stdout
    (tmp_path / "variants.json").write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(app, ["run", "--suite", str(ROOT / "evals/suites/harness-p0-v1.json"),
        "--variants", str(tmp_path / "variants.json"), "--output", str(tmp_path / "out"), "--mode", "real"])
    assert result.exit_code == 2 and not (tmp_path / "out").exists()
