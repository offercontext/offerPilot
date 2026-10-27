"""Exercise the real fake runner through the workflow's explicit artifact paths.

This models local upload collection/ZIP contents, not the GitHub upload service.
No real provider, workflow dispatch, key, or existing run artifact is accessed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile

import pytest
import yaml

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import run
from scripts.companion_ai_validation.tests.test_companion_ai_broker import response

from scripts.companion_ai_controller.tests.workflow_assets import historical_workflow

ROOT = Path(__file__).resolve().parents[3]

WORKFLOW = historical_workflow("legacy")
EVIDENCE_DIRECTORY = PurePosixPath("artifacts/companion-ai")
MEMBERS = frozenset({"summary.json", "responses.jsonl", "budget.jsonl", "requests.jsonl"})
PATHS = frozenset(str(EVIDENCE_DIRECTORY / name) for name in MEMBERS)
SENTINEL = b"SYNTHETIC_NOT_FOR_ARTIFACT_SECRET_HEADER_ENV"


def _upload_settings(workflow_text):
    # Parse the actual YAML structure and select the paid job's upload step;
    # a mention in comments/another job is not an artifact declaration.
    workflow = yaml.safe_load(workflow_text)
    uploads = [step for step in workflow["jobs"]["paid"]["steps"]
               if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1, "exactly one paid artifact upload required"
    return uploads[0]["with"]


def _declared_paths(workflow_text):
    value = _upload_settings(workflow_text)["path"]
    assert isinstance(value, str), "literal multiline artifact paths required"
    paths = value.splitlines()
    assert paths and all(path and path == path.strip() for path in paths)
    assert len(paths) == len(set(paths)), "duplicate artifact path"
    # Every declaration must be a literal member of the four-file allowlist;
    # directory/glob/parent/secret-like entries cannot expand upload scope.
    assert set(paths) <= PATHS, "artifact path outside explicit allowlist"
    return tuple(paths)


def _collect_zip(workspace, workflow_text):
    paths = _declared_paths(workflow_text)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in paths:
            path = workspace / relative
            assert path.is_file() and not path.is_symlink(), "declared artifact file missing"
            assert path.resolve().parent == (workspace / str(EVIDENCE_DIRECTORY)).resolve()
            # All declarations share this directory, so upload-artifact's common
            # ancestor maps each exact input to its basename inside the archive.
            bundle.write(path, arcname=path.name)
    return archive.getvalue()


def _assert_package(raw_archive, workspace, sent):
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as bundle:
        assert len(bundle.namelist()) == 4 and set(bundle.namelist()) == MEMBERS, (
            "artifact must retain exactly four evidence files")
        bodies = {name: bundle.read(name) for name in bundle.namelist()}
    for name, body in bodies.items():
        original = (workspace / str(EVIDENCE_DIRECTORY) / name).read_bytes()
        assert body == original
        assert hashlib.sha256(body).digest() == hashlib.sha256(original).digest()
        assert SENTINEL not in body
    summary = json.loads(bodies["summary.json"])
    requests = [json.loads(line) for line in bodies["requests.jsonl"].splitlines()]
    replies = [json.loads(line) for line in bodies["responses.jsonl"].splitlines()]
    ledger = [json.loads(line) for line in bodies["budget.jsonl"].splitlines()]
    reserved = [entry for entry in ledger if entry["event"] == "reserved"]
    assert summary["matrix"]["passed"] and summary["matrix"]["pass_scope"] == "mechanism_only"
    assert summary["budget"]["reserved_micro_cny"] == 0
    assert len(requests) == len(replies) == len(reserved) == len(sent) == 12
    assert summary["matrix"]["responses"] == replies
    for ordinal, (request, reply, reserve, actual) in enumerate(
            zip(requests, replies, reserved, sent, strict=True), 1):
        assert request["operation"] == reply["operation"] == reserve["operation"] == (
            gate.OPERATIONS[ordinal - 1])
        assert request["ordinal"] == ordinal
        assert request["raw_body"].encode() == actual
        assert request["body_sha256"] == hashlib.sha256(actual).hexdigest()
        assert request["body_sha256"] == reserve["body_sha256"]
        payload = json.loads(actual)
        assert payload["messages"][-1]["content"] == reply["user_input"]
        assert payload["thinking"] == {"type": "disabled"} and payload["stream"] is False
        assert payload["max_tokens"] == 512 and payload["model"] == gate.MODEL


@pytest.fixture(scope="module")
def generated_evidence(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("companion-artifact-workspace")
    sent = []

    def fake(raw):
        sent.append(raw)
        return 200, gate.encode(response(content="合成打包验收回复，不代表真实人格结果。"))

    # Only the approved test clock and final HTTPS boundary are substituted.
    # Actual run.execute -> broker -> product HTTP driver creates all four files.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
        report = run.execute(fake, "b" * 40, workspace / str(EVIDENCE_DIRECTORY))
    assert report["matrix"]["passed"], report
    assert report["budget"]["request_count"] == len(sent) == 12
    assert {path.name for path in (workspace / str(EVIDENCE_DIRECTORY)).iterdir()} == MEMBERS
    for relative in (".env", "config.json", "artifacts/provider-secret.txt",
                     "artifacts/companion-ai/.env", "artifacts/companion-ai/headers.json",
                     "artifacts/companion-ai/provider-config.json",
                     "artifacts/companion-ai/nested/env.txt"):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SENTINEL)
    return workspace, sent


def test_actual_workflow_packages_complete_runner_evidence_and_excludes_other_files(generated_evidence):
    workspace, sent = generated_evidence
    archive = _collect_zip(workspace, WORKFLOW.read_text())
    _assert_package(archive, workspace, sent)


def test_missing_request_declaration_fails_after_collecting_incomplete_zip(generated_evidence):
    workspace, sent = generated_evidence
    workflow = yaml.safe_load(WORKFLOW.read_text())
    settings = _upload_settings(yaml.safe_dump(workflow))
    settings["path"] = "\n".join(path for path in settings["path"].splitlines()
                                  if path != "artifacts/companion-ai/requests.jsonl")
    # Update the actual parsed workflow node, not an independent expected string.
    for step in workflow["jobs"]["paid"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            step["with"] = settings
    archive = _collect_zip(workspace, yaml.safe_dump(workflow))
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert "requests.jsonl" not in bundle.namelist()
    with pytest.raises(AssertionError, match="exactly four evidence files"):
        _assert_package(archive, workspace, sent)


@pytest.mark.parametrize("extra", [
    "artifacts/companion-ai", "artifacts/companion-ai/**", "artifacts/companion-ai/*.jsonl",
    "artifacts/companion-ai/.env", "artifacts/companion-ai/headers.json",
    "artifacts/companion-ai/provider-config.json", "artifacts/companion-ai/../provider-secret.txt",
    "/tmp/secrets.json", "${{ runner.temp }}/private",
])
def test_artifact_declarations_cannot_expand_to_directories_globs_or_sensitive_files(extra):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    modified = deepcopy(workflow)
    for step in modified["jobs"]["paid"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            step["with"]["path"] = step["with"]["path"].rstrip("\n") + "\n" + extra
    with pytest.raises(AssertionError, match="outside explicit allowlist"):
        _declared_paths(yaml.safe_dump(modified))
