import json
from types import SimpleNamespace
from uuid import uuid4

from tests.evaluation.p0_diagnostics import export_diagnostics


def test_p0_diagnostic_excludes_raw_private_material(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    output = tmp_path / "safe"
    monkeypatch.setenv("OFFERPILOT_P0_DIAGNOSTICS", str(output))
    secret = "synthetic-secret-never-export"
    frame = dict(type="fault_reached", seq=7, epoch=1, confirmation_token=secret,
                 body=secret, trial_key={"other": secret})
    (private / "epoch-1.control.jsonl").write_text(json.dumps(frame) + "\n")
    (private / "epoch-1.stderr").write_text(secret)
    plan = SimpleNamespace(run_id=uuid4(), execution_item_id="E06.legacy_saved", mode="deterministic")
    spec = SimpleNamespace(evaluator_limits=SimpleNamespace(model_dump=lambda: {"wall_seconds": 180}))
    source = dict.fromkeys(("commit", "diff_hash", "lock_hash", "files_hash"), "a" * 40)
    source["raw_config"] = secret
    result = SimpleNamespace(verdict="incomplete", execution_status="eval_error")
    evidence = SimpleNamespace(fragment_errors={"driver": "ReadTimeout"},
        http=[dict(method="POST", path="/api/chat/confirm", status=200, body=secret, response=secret)],
        facts=dict(driver_completed=False, control_closed=False,
                   cleanup_errors=[dict(operation="shutdown.wait", error_type="TimeoutError", epoch=1, text=secret)]),
        files={"epoch-1.stderr": dict(bytes=len(secret), sha256="f" * 64), "data.db": dict(raw=secret)})
    export_diagnostics(private, plan, spec, source, result, evidence)
    raw = next(output.glob("*.json")).read_text()
    value = json.loads(raw)
    assert secret not in raw and "confirmation_token" not in raw and "raw_config" not in raw
    assert value["errors"] == {"driver": "ReadTimeout"}
    assert value["control"] == [dict(type="fault_reached", seq=7, epoch=1, error_type="redacted")]
    assert value["driver"] == [dict(method="POST", route="/api/chat/confirm", status=200)]
    assert value["limits"] == {"wall_seconds": 180}
    assert len(value["stderr"]) == 1
    # Even fields selected for publication must reject plausible secret strings.
    frame.update(type=secret, error_type=secret)
    (private / "epoch-1.control.jsonl").write_text(json.dumps(frame) + "\n{broken")
    evidence.fragment_errors = {secret: secret, "driver": secret}
    evidence.http = [dict(method=secret, path=secret, status=secret)]
    evidence.facts["cleanup_errors"] = [dict(operation=secret, error_type=secret)]
    evidence.files["epoch-1.stderr"] = dict(bytes=secret, sha256=secret)
    source["commit"] = secret
    export_diagnostics(private, plan, spec, source, result, evidence)
    raw = next(output.glob("*.json")).read_text()
    assert secret not in raw
    assert json.loads(raw)["diagnostic_errors"] == ["control_invalid_json"]


def test_p0_diagnostic_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("OFFERPILOT_P0_DIAGNOSTICS", raising=False)
    export_diagnostics(tmp_path, None, None, None, None, None)
    assert not list(tmp_path.iterdir())
