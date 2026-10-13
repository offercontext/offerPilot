import copy
from datetime import datetime, timezone
import json
import shlex
import subprocess
import sys
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from offerpilot.evaluation import core_external as external
from offerpilot.evaluation.cli import app
from offerpilot.evaluation.registry import file_hash, digest


@pytest.fixture
def captured(tmp_path, monkeypatch):
    source = tmp_path / "external.txt"
    source.write_text("ok synthetic-step: captured\nVerify local passed\n", encoding="utf-8")
    data = tmp_path / "data-manifest.json"
    data.write_text('{"fixture":"synthetic-v1"}', encoding="utf-8")
    code = dict(commit="a" * 40, diff_hash="b" * 64, lock_hash="c" * 64, files_hash="d" * 64)
    monkeypatch.setattr(external, "snapshot", lambda checkout: code)
    stamp = datetime.now(timezone.utc).isoformat()
    receipt = dict(
        schema_version=2,
        kind="external-check-receipt",
        check_id=str(uuid4()),
        check="oc-verify",
        command=["uv", "run", "oc", "verify", "--profile", "local"],
        exit_code=0,
        execution_complete=True,
        started_at=stamp,
        finished_at=stamp,
        mode="deterministic",
        report=dict(path=str(source), sha256=file_hash(source)),
        report_capture="stdout",
        code=dict(checkout=str(tmp_path), **code),
        data_definition="Exact synthetic fixture manifest of this captured check.",
        data_files=[dict(path=str(data), sha256=file_hash(data))],
        model=None,
        metric_definitions={},
    )
    manifest = tmp_path / "captured-data.json"
    manifest.write_text(
        json.dumps(
            dict(
                schema_version=1,
                kind="external-data-snapshot",
                check_id=receipt["check_id"],
                check=receipt["check"],
                definition=receipt["data_definition"],
                files=receipt["data_files"],
            )
        ),
        encoding="utf-8",
    )
    receipt["data_manifest"] = dict(path=str(manifest), sha256=file_hash(manifest))

    def run(changed=None):
        value = copy.deepcopy(receipt if changed is None else changed)
        path = tmp_path / (str(uuid4()) + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        out = tmp_path / (str(uuid4()) + ".result.json")
        return external.import_external(path, out), out

    return receipt, source, run


def test_external_import_is_standalone_and_executes_no_command(captured, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("executed command"))
    receipt, _, run = captured
    result, out = run()
    saved = json.loads(out.read_bytes())
    assert result.verdict == "pass" and result.quality == "unknown"
    assert saved["kind"] == "external-check-result" and saved["release_eligible"] is False
    assert not {"planned", "trial_results", "rules", "run_id"} & saved.keys()
    assert result.receipt.command == receipt["command"]


@pytest.mark.parametrize(
    "mutation",
    [
        "nonzero",
        "unfinished",
        "no_data",
        "wrong_footer",
        "drift",
        "hash",
        "shell",
        "mode",
        "secret",
        "extra",
    ],
)
def test_external_import_rejects_drift_and_preserves_missing_or_failed_checks(captured, mutation):
    receipt, source, run = captured
    expected = None
    if mutation == "nonzero":
        receipt.update(exit_code=1, data_files=[])
        expected = "fail"
    elif mutation == "unfinished":
        receipt["execution_complete"] = False
        expected = "incomplete"
    elif mutation == "no_data":
        receipt["data_files"] = []
        expected = "incomplete"
    elif mutation == "wrong_footer":
        source.write_text("ok one: captured\n", encoding="utf-8")
        receipt["report"]["sha256"] = file_hash(source)
        expected = "incomplete"
    elif mutation == "drift":
        receipt["code"]["commit"] = "e" * 40
    elif mutation == "hash":
        source.write_text("tampered", encoding="utf-8")
    elif mutation == "shell":
        receipt["command"] += [";", "anything"]
    elif mutation == "mode":
        receipt["mode"] = "real"
    elif mutation == "secret":
        receipt["data_definition"] = "sk-private-test-key-never-output"
    elif mutation == "extra":
        receipt["release_eligible"] = True
    if expected:
        result, _ = run(receipt)
        assert result.verdict == expected
    else:
        with pytest.raises(ValueError):
            run(receipt)


@pytest.mark.parametrize(
    "mutation", ["none", "fail", "missing_definition", "missing_sources", "invalid_number"]
)
def test_knowledge_metrics_keep_original_meaning_and_fail_precedence(captured, tmp_path, mutation):
    receipt, source, run = captured
    payload = dict(
        passed=True,
        gates={},
        metrics={"lexical_recall_at_5": 0.9},
        sources=[{}],
        queries=[{}],
        failures=[],
        fixture_errors=[],
        provider_summary={},
    )
    receipt.update(
        check="knowledge-acceptance",
        command=["oc", "knowledge-acceptance", "--profile", "v1"],
        metric_definitions={
            "lexical_recall_at_5": "Original KI recall hit fraction; not a Harness task pass rate."
        },
    )
    data_directory = tmp_path / "knowledge-fixtures"
    data_directory.mkdir()
    for name in ("manifest.json", "queries.json"):
        (data_directory / name).write_text("{}", encoding="utf-8")
    receipt["command"] += ["--fixtures-dir", str(data_directory), "--report", str(source)]
    receipt["data_files"] = [
        dict(path=str(p), sha256=file_hash(p)) for p in sorted(data_directory.iterdir())
    ]
    receipt["data_manifest"] = None
    if mutation == "fail":
        payload["passed"] = False
        receipt["data_files"] = []
    elif mutation == "missing_definition":
        receipt["metric_definitions"] = {}
    elif mutation == "missing_sources":
        payload["sources"] = []
    elif mutation == "invalid_number":
        payload["metrics"]["lexical_recall_at_5"] = float("inf")
    source.write_text(json.dumps(payload), encoding="utf-8")
    receipt["report"]["sha256"] = file_hash(source)
    result, _ = run(receipt)
    assert result.verdict == (
        "pass" if mutation == "none" else "fail" if mutation == "fail" else "incomplete"
    )
    if mutation != "invalid_number":
        assert result.metrics == payload["metrics"] and result.quality == "unknown"


def test_real_external_report_without_actual_model_evidence_stays_incomplete(captured):
    receipt, source, run = captured
    receipt.update(command=["oc", "verify", "--profile", "real-ai"], mode="real")
    source.write_text("ok step: captured\nVerify real-ai passed\n", encoding="utf-8")
    receipt["report"]["sha256"] = file_hash(source)
    result, _ = run(receipt)
    assert result.verdict == "incomplete" and "missing_model_snapshot" in result.reasons


@pytest.mark.parametrize("mutation", ["none", "profile", "opaque", "model"])
def test_real_model_identity_binds_profile_and_actual_provider_response(
    captured, tmp_path, mutation
):
    receipt, source, run = captured
    receipt.update(command=["oc", "verify", "--profile", "real-ai"], mode="real")
    source.write_text("ok synthetic step: captured\nVerify real-ai passed\n", encoding="utf-8")
    receipt["report"]["sha256"] = file_hash(source)
    profile = tmp_path / "profile.json"
    profile.write_text(
        '{"endpoint":"https://synthetic.invalid","model":"synthetic-alias"}', encoding="utf-8"
    )
    response = tmp_path / "response.json"
    response.write_text(
        json.dumps(
            dict(
                id="synthetic-request-id",
                model="synthetic-served-model",
                usage=dict(prompt_tokens=1, completion_tokens=1),
            )
        ),
        encoding="utf-8",
    )
    receipt["model"] = dict(
        profile_hash=digest(json.loads(profile.read_bytes())),
        profile=dict(path=str(profile), sha256=file_hash(profile)),
        actual_models=["synthetic-served-model"],
        evidence=[dict(path=str(response), sha256=file_hash(response))],
    )
    if mutation == "profile":
        receipt["model"]["profile_hash"] = "f" * 64
    elif mutation == "opaque":
        response.write_text("{}", encoding="utf-8")
        receipt["model"]["evidence"][0]["sha256"] = file_hash(response)
    elif mutation == "model":
        receipt["model"]["actual_models"] = ["synthetic-alias"]
    if mutation == "none":
        assert run(receipt)[0].verdict == "pass"
    else:
        with pytest.raises(ValueError):
            run(receipt)


def test_external_cli_returns_partial_code_and_never_overwrites(captured, tmp_path):
    receipt, _, _ = captured
    receipt["data_files"] = []
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    out = tmp_path / "out.json"
    argv = ["import-external", "--receipt", str(path), "--output", str(out)]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 3 and json.loads(out.read_bytes())["verdict"] == "incomplete"
    original = out.read_bytes()
    assert CliRunner().invoke(app, argv).exit_code == 2
    assert out.read_bytes() == original


@pytest.mark.parametrize(
    "missing", ["metrics", "definition", "blank_definition", "empty_metrics", "manifest"]
)
def test_missing_external_provenance_never_becomes_a_complete_pass(captured, missing):
    receipt, source, run = captured
    if missing in {"metrics", "empty_metrics"}:
        receipt.update(check="knowledge-acceptance", command=["oc", "knowledge-acceptance"])
        payload = dict(
            passed=False,
            sources=[{}],
            queries=[{}],
            gates={},
            failures=[],
            fixture_errors=[],
            provider_summary={},
        )
        if missing == "empty_metrics":
            payload["metrics"] = {}
        source.write_text(json.dumps(payload), encoding="utf-8")
        receipt["report"]["sha256"] = file_hash(source)
    elif missing == "definition":
        receipt["data_definition"] = None
    elif missing == "blank_definition":
        receipt["data_definition"] = "   "
    else:
        receipt["data_manifest"] = None
    result, _ = run(receipt)
    assert result.verdict == ("fail" if missing in {"metrics", "empty_metrics"} else "incomplete")


@pytest.mark.parametrize("mutation", ["report_path", "real_v1", "wrong_report"])
def test_external_command_rejects_nonexistent_options_and_cross_report_binding(captured, mutation):
    receipt, source, run = captured
    receipt.update(check="knowledge-acceptance", command=["oc", "knowledge-acceptance"])
    if mutation == "report_path":
        receipt["command"] += ["--report-path", str(source)]
    elif mutation == "real_v1":
        receipt.update(command=receipt["command"] + ["--real-ai"], mode="real")
    else:
        receipt["command"] += ["--report", str(source.with_name("other.json"))]
    with pytest.raises(ValueError):
        run(receipt)


def test_snapshot_blocks_git_external_diff_textconv_fsmonitor_and_clean_filters(
    tmp_path, monkeypatch
):
    from offerpilot.evaluation.runner import snapshot

    def native(*args):
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=" + str(tmp_path / "no-hooks"), *args],
            cwd=tmp_path,
            stderr=subprocess.PIPE,
        )

    native("init", "-q")
    native("config", "user.name", "Synthetic test")
    native("config", "user.email", "synthetic@example.invalid")
    (tmp_path / "uv.lock").write_text("synthetic-lock", encoding="utf-8")
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    native("add", "uv.lock", "file.txt")
    native("commit", "-qm", "test: 合成快照夹具")
    marker = tmp_path / "helper-was-executed"
    helper = tmp_path / "helper.py"
    helper.write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unexpected')\n",
        encoding="utf-8",
    )
    command = (
        shlex.quote(sys.executable.replace("\\", "/"))
        + " "
        + shlex.quote(str(helper).replace("\\", "/"))
    )
    native("config", "diff.external", command)
    native("config", "diff.evil.textconv", command)
    native("config", "filter.evil.clean", command)
    native("config", "filter.evil.process", command)
    native("config", "filter.evil.required", "true")
    native("config", "core.fsmonitor", command)
    (tmp_path / ".gitattributes").write_text("*.txt diff=evil filter=evil\n", encoding="utf-8")
    target.write_text("changed", encoding="utf-8")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", command)
    value = snapshot(tmp_path)
    assert len(value["commit"]) == 40 and not marker.exists()
    assert value["files_hash"] and value["diff_hash"]


def test_invalid_external_git_checkout_is_configuration_error_without_traceback(
    captured, tmp_path, monkeypatch
):
    from offerpilot.evaluation.runner import snapshot

    receipt, _, _ = captured
    monkeypatch.setattr(external, "snapshot", snapshot)
    receipt["code"]["checkout"] = str(tmp_path / "missing-checkout")
    path = tmp_path / "invalid-code-receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["import-external", "--receipt", str(path), "--output", str(tmp_path / "invalid-out.json")],
    )
    assert result.exit_code == 2 and "ValueError" in result.stderr
    assert not (tmp_path / "invalid-out.json").exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_malformed_external_report_preserves_native_failure_and_unknown_schema(captured, exit_code):
    receipt, source, run = captured
    receipt.update(
        check="knowledge-acceptance", command=["oc", "knowledge-acceptance"], exit_code=exit_code
    )
    source.write_text("{incomplete-json", encoding="utf-8")
    receipt["report"]["sha256"] = file_hash(source)
    result, _ = run(receipt)
    assert result.verdict == ("fail" if exit_code else "incomplete")
    assert result.completeness["source_report"] is False


def test_external_report_file_capture_must_be_bound_to_real_command(captured):
    receipt, _, run = captured
    receipt.update(
        check="knowledge-acceptance", command=["oc", "knowledge-acceptance"], report_capture="file"
    )
    with pytest.raises(ValueError):
        run(receipt)
