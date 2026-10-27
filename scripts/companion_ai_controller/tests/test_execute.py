"""No sockets, credentials or provider calls in fixed controller rehearsals."""
import base64
import json
import subprocess
import sys
import zipfile

import pytest

from scripts.companion_ai_controller import execute
from scripts.companion_ai_validation import broker, shadow_driver


def test_fixed_fake_uses_actual_durable_ledger_and_keeps_original_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SECRET_SENTINEL", "private-never-dump-4938")
    monkeypatch.setattr(broker.HTTPSUpstream, "__init__", lambda *a, **k: pytest.fail("network"))
    result = execute.execute_rehearsal(tmp_path / "out")
    assert result["provider_calls"] == 0 and result["simulated_calls"] == 6
    assert result["real_prior_calls"] == 48 and result["real_prior_settled_micro_cny"] == 411934
    assert result["matrix"]["passed"] and result["live_allowed"] is False
    assert result["budget"]["settled_micro_cny"] == 411934 + 6 * 280
    assert result["budget"]["reserved_micro_cny"] == 0
    lines = (tmp_path / "out" / "budget.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert len([row for row in rows if row["event"] == "reserved"]) == 6
    assert all(row["evidence_kind"] == execute.EVIDENCE_KIND for row in rows)
    requests = [json.loads(line) for line in (tmp_path / "out" / "requests.jsonl").read_text().splitlines()]
    assert [r["raw_body"].encode() for r in requests] == [
        shadow_driver.frozen_request(op) for op in shadow_driver.REQUEST_HASHES]
    responses = [json.loads(line) for line in (tmp_path / "out" / "responses.jsonl").read_text().splitlines()]
    assert all(json.loads(base64.b64decode(r["raw_base64"]))["model"] == "deepseek-flash"
               for r in responses)
    assert all(b"private-never-dump-4938" not in p.read_bytes() for p in (tmp_path / "out").iterdir())


@pytest.mark.parametrize("failure", ["missing_usage", "transport_unknown", "tool_response"])
def test_unknown_halts_without_retry_and_retains_reservation(tmp_path, failure):
    result = execute.execute_rehearsal(tmp_path / "out", failure=failure)
    assert result["simulated_calls"] == 1 and result["provider_calls"] == 0
    assert result["matrix"]["passed"] is False
    assert result["budget"]["reserved_micro_cny"] == 3_000_000
    assert result["budget"]["halted"] is True
    assert result["budget"]["settled_micro_cny"] == 411934
    assert all(row["status"] == "not_attempted" for row in result["matrix"]["cases"][1:])


def test_output_cannot_be_reopened_or_symlinked(tmp_path):
    out = tmp_path / "out"
    execute.execute_rehearsal(out)
    original = (out / "budget.jsonl").read_bytes()
    with pytest.raises(execute.RehearsalError, match="OUTPUT_EXISTS"):
        execute.execute_rehearsal(out)
    assert (out / "budget.jsonl").read_bytes() == original
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing")
    with pytest.raises(execute.RehearsalError, match="OUTPUT_EXISTS"):
        execute.execute_rehearsal(link)


def test_live_is_unconditionally_unimplemented(monkeypatch):
    monkeypatch.setenv("OFFERPILOT_COMPANION_AI_CONTROLLER_SHA", "a" * 40)
    with pytest.raises(execute.RehearsalError, match="LIVE_EXECUTION_NOT_IMPLEMENTED"):
        execute.execute_live({"live_allowed": True, "approved": True})


def test_input_cannot_add_injection_or_seventh_request(tmp_path, monkeypatch):
    original = shadow_driver.frozen_request
    monkeypatch.setattr(shadow_driver, "frozen_request", lambda op: original(op) + b'\n{"tools":[]}')
    with pytest.raises(Exception):
        execute.execute_rehearsal(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_manifest_excludes_unlisted_files_and_rejects_missing(tmp_path):
    out = tmp_path / "out"
    execute.execute_rehearsal(out)
    (out / "unrelated.txt").write_text("not_uploaded")
    assert set(execute.evidence_manifest(out)) == set(execute.FILES)
    (out / "requests.jsonl").unlink()
    with pytest.raises(execute.RehearsalError, match="EVIDENCE_MISSING"):
        execute.evidence_manifest(out)


def test_genuine_cli_through_zip_and_no_live_flag(tmp_path):
    out = tmp_path / "cli"
    proc = subprocess.run([sys.executable, "-m", "scripts.companion_ai_controller.rehearsal",
                           "--output-dir", str(out)], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "CONTROLLER_REHEARSAL_PASSED"
    with zipfile.ZipFile(out / "rehearsal.zip") as z:
        assert set(z.namelist()) == set(execute.FILES) | {"admission.json", "verification.json"}
        assert json.loads(z.read("summary.json"))["provider_calls"] == 0
        assert json.loads(z.read("admission.json"))["production_protection_verified"] is False
    proc = subprocess.run([sys.executable, "-m", "scripts.companion_ai_controller.rehearsal",
                           "--live", "--output-dir", str(tmp_path / "live")],
                          text=True, capture_output=True)
    assert proc.returncode != 0 and not (tmp_path / "live").exists()


def test_bad_cli_has_constant_error_and_does_not_dump_path(tmp_path):
    out = tmp_path / "private-path-sentinel"
    out.mkdir()
    proc = subprocess.run([sys.executable, "-m", "scripts.companion_ai_controller.rehearsal",
                           "--output-dir", str(out)], text=True, capture_output=True)
    assert proc.returncode == 1 and proc.stdout.strip() == "CONTROLLER_REHEARSAL_BLOCKED"
    assert "private-path-sentinel" not in proc.stdout + proc.stderr


def test_fake_transport_has_hard_six_call_ceiling():
    fake = execute._FakeUpstream(None)
    for operation in shadow_driver.REQUEST_HASHES:
        fake(shadow_driver.frozen_request(operation))
    with pytest.raises(execute.RehearsalError, match="REQUEST_LIMIT"):
        fake(shadow_driver.frozen_request("s01"))
    assert fake.calls == 6


def test_response_injection_is_retained_data_never_executed(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist"
    original = execute._FakeUpstream.__call__

    def injected(self, raw):
        status, body = original(self, raw)
        envelope = json.loads(body)
        review = json.loads(envelope["choices"][0]["message"]["content"])
        review["rationale"] = f"$(touch {marker}) <script>fetch('credential')</script>"
        envelope["choices"][0]["message"]["content"] = json.dumps(review)
        return status, broker.encode(envelope)

    monkeypatch.setattr(execute._FakeUpstream, "__call__", injected)
    report = execute.execute_rehearsal(tmp_path / "out")
    assert report["matrix"]["passed"]
    assert not marker.exists()
    assert "$(touch" in (tmp_path / "out" / "summary.json").read_text()


def test_real_cli_workflow_paths_zip_contract(tmp_path):
    import yaml
    from scripts.companion_ai_controller import rehearsal
    from scripts.companion_ai_controller.tests.test_workflow import WORKFLOW

    output = tmp_path / "output"
    assert rehearsal.run(output)
    document = yaml.safe_load(WORKFLOW.read_text())
    upload = next(step for step in document["jobs"]["rehearsal"]["steps"]
                  if step.get("uses", "").startswith("actions/upload-artifact@"))
    upload_paths = upload["with"]["path"].splitlines()
    assert "artifacts/companion-controller-rehearsal/rehearsal.zip" in upload_paths
    assert not any(p.endswith(".jsonl") for p in upload_paths)
    uploaded_members = set(execute.FILES) | set(rehearsal.RECEIPTS)
    with zipfile.ZipFile(output / "rehearsal.zip") as archive:
        assert set(archive.namelist()) == uploaded_members
        assert "requests.jsonl" in uploaded_members
        for name in uploaded_members:
            assert archive.read(name) == (output / name).read_bytes()


def test_label_write_failure_quarantines_raw_no_publishable_zip(tmp_path, monkeypatch):
    from scripts.companion_ai_controller import rehearsal
    from scripts.companion_ai_controller.tests.test_workflow import WORKFLOW
    import yaml

    def failed_write(*_args):
        raise OSError("SYNTHETIC_WRITE_FAILURE")

    monkeypatch.setattr(execute, "_write", failed_write)
    output = tmp_path / "failed"
    with pytest.raises(OSError):
        rehearsal.run(output)
    assert not (output / "rehearsal.zip").exists()
    status = json.loads((tmp_path / "failed-status.json").read_text())
    assert status["state"] == "started_not_complete" and status["provider_calls"] == 0
    assert status["evidence_kind"] == execute.EVIDENCE_KIND
    workflow = yaml.safe_load(WORKFLOW.read_text())
    upload = next(s for s in workflow["jobs"]["rehearsal"]["steps"]
                  if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert not any(p.endswith(".jsonl") or '*' in p for p in upload["with"]["path"].splitlines())


def test_unlabeled_detached_budget_is_never_packaged(tmp_path):
    out = tmp_path / "out"
    execute.execute_rehearsal(out)
    path = out / "budget.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0].pop("evidence_kind")
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    with pytest.raises(execute.RehearsalError, match="UNLABELED"):
        execute.evidence_manifest(out)


def test_changed_file_after_manifest_has_no_final_zip(tmp_path, monkeypatch):
    from scripts.companion_ai_controller import rehearsal
    original = rehearsal.evidence_manifest

    def changed(path):
        manifest = original(path)
        with (path / "requests.jsonl").open("ab") as stream:
            stream.write(b'{}\n')
        return manifest

    monkeypatch.setattr(rehearsal, "evidence_manifest", changed)
    with pytest.raises(execute.RehearsalError, match="ARCHIVE_CHANGED"):
        rehearsal.run(tmp_path / "out")
    assert not (tmp_path / "out" / "rehearsal.zip").exists()
