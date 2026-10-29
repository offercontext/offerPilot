import json
import subprocess
from types import SimpleNamespace

import pytest

from offerpilot.evaluation import core_runner, core_planning
from offerpilot.evaluation.runner import ROOT


@pytest.mark.parametrize("failure", ["git", "drift", "trial"])
def test_failed_pretrial_keeps_denominator_not_run_and_safe_failure_chain(tmp_path, monkeypatch, failure):
    source = dict(commit="a" * 40, diff_hash="b" * 64, lock_hash="c" * 64, files_hash="d" * 64)
    monkeypatch.setattr(core_planning, "snapshot", lambda _: source)
    monkeypatch.setattr(core_planning, "target_environment", lambda _:
        dict(python="test", sqlite="test", dependencies={}))
    config = tmp_path / "variants.json"
    config.write_text(json.dumps([dict(variant_id="candidate", checkout=str(tmp_path), interpreter="unused")]))
    folder = tmp_path / "run"
    manifest = core_planning.create_plan(ROOT / "evals/suites/harness-core-v1.json", config, folder)
    monkeypatch.setattr(core_runner, "read_plan", lambda _: manifest)
    monkeypatch.setattr(core_runner, "RunLedger", lambda *args: SimpleNamespace())
    snapshots, trials = [], []

    def snapshot(_):
        snapshots.append(1)
        if failure == "git":
            try:
                raise subprocess.TimeoutExpired("COMMAND-SECRET", 30, output="OUTPUT-SECRET")
            except subprocess.TimeoutExpired as exc:
                raise ValueError("MESSAGE-SECRET") from exc
        return source | ({"diff_hash": "e" * 64} if failure == "drift" else {})

    def run_trial(*args):
        trials.append(1)
        private = args[2]
        private.mkdir(parents=True)
        (private / "original-output.txt").write_text("PARTIAL-SECRET", encoding="utf-8")
        raise OSError("TRIAL-SECRET")

    monkeypatch.setattr(core_runner, "snapshot", snapshot)
    monkeypatch.setattr(core_runner, "run_trial", run_trial)
    summary = core_runner.run_plan(folder / "run-plan.json")
    rows = json.loads((folder / "trial-results.json").read_bytes())
    diagnostics = json.loads((folder / "execution-failures.json").read_bytes())
    assert len(rows) == len(manifest.planned_trials) == 19
    assert len(diagnostics) == (1 if failure == "trial" else 19)
    assert summary["counts"] == {"pass": 0, "fail": 0, "incomplete": 19}
    if failure == "trial":
        assert rows[0]["execution_status"] == "eval_error" and rows[0]["evidence_hash"] != "0" * 64
        from pathlib import Path
        from offerpilot.evaluation.core_storage import load
        evidence = load(Path(rows[0]["private_directory"]), rows[0]["evidence_hash"])
        assert evidence["errors"] == {}
        assert evidence["facts"]["attempt_started"] and evidence["facts"]["consumption"]["in_flight_unknown"]
        original = Path(evidence["facts"]["partial_evidence"][0]["path"])
        assert original.read_text(encoding="utf-8") == "PARTIAL-SECRET"
        original.write_text("tampered synthetic fixture", encoding="utf-8")
        assert load(Path(rows[0]["private_directory"]), rows[0]["evidence_hash"])["errors"] == {
            "partial_evidence": "invalid"}
    assert all(row["execution_status"] == "not_run" and row["evidence_hash"] == "0" * 64
               and row["private_directory"] == "" for row in rows[int(failure == "trial"):])
    assert all(row["retried"] is False for row in diagnostics)
    assert len(snapshots) == (1 if failure == "trial" else 19) and len(trials) == int(failure == "trial")
    assert {row["phase"] for row in diagnostics} == {
        "trial_execution" if failure == "trial" else "source_snapshot"}
    assert [row["trial_key"] for row in diagnostics] == [p.model_dump(mode="json", include={
        "run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}) for p in manifest.planned_trials[:len(diagnostics)]]
    assert "SECRET" not in json.dumps(diagnostics)
    assert [item["type"] for item in diagnostics[0]["exception_chain"]] == (
        ["ValueError", "TimeoutExpired"] if failure == "git" else
        ["OSError"] if failure == "trial" else ["ValueError"])
