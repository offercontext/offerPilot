"""Fake six-review runner -> actual workflow allowlist -> inspected ZIP bytes.

No upload service or real provider is exercised. A structurally valid fake reply
is deliberately not a claim about semantic accuracy or product improvement.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import shlex
import zipfile

import pytest
import yaml

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import run, shadow_projection_continuation, shadow_projection_driver
from scripts.companion_ai_validation.tests import test_companion_ai_artifact_contract as package
from scripts.companion_ai_validation.tests.test_companion_ai_broker import response


def _review_response(raw):
    data = json.loads(json.loads(raw)["messages"][1]["content"])
    review = {"protocol": "shadow-quote-v2", "target_id": data["target_id"], "verdict": "unknown",
              "evidence": [{"source_id": "user", "quote": data["sources"][0]["text"]}],
              "rationale": "仅合成结构验收，不是语义判定。"}
    if data["target_id"] in {"s03", "s04"}:
        from experiments.shadow_input_projection.projection import LONG
        review["evidence"].append({"source_id": "reference", "quote": LONG})
    return gate.encode(response(content=json.dumps(review, ensure_ascii=False)))


def _produce(workspace, fail_at=None, failure="invalid_json"):
    directory = workspace / str(package.EVIDENCE_DIRECTORY)
    sent, received = [], []

    def fake(raw):
        # This is the genuine final transport boundary. Request evidence must
        # already be durable before it is allowed to see a request.
        sent.append(raw)
        requests = [json.loads(line) for line in (directory / "requests.jsonl").read_bytes().splitlines()]
        assert len(requests) == len(sent)
        assert requests[-1]["raw_body"].encode() == raw
        if len(sent) == fail_at and failure == "unknown_network":
            raise TimeoutError("synthetic unknown outcome")
        reply = _review_response(raw)
        if len(sent) == fail_at:
            if failure == "invalid_json":
                reply = b'{"invalid":'
            elif failure == "missing_usage":
                envelope = json.loads(reply)
                envelope.pop("usage")
                reply = gate.encode(envelope)
        received.append(reply)
        return 200, reply

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gate.time, "time", lambda: shadow_projection_continuation.PRICE_VALID_UNTIL - 3600)
        report = run.execute(fake, "b" * 40, directory, shadow_projection=True)
    assert {path.name for path in directory.iterdir()} == package.MEMBERS
    return report, sent, received


def _unpack(raw, workspace):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len(archive.namelist()) == 4 and set(archive.namelist()) == package.MEMBERS, (
            "artifact must retain exactly four evidence files")
        bodies = {name: archive.read(name) for name in archive.namelist()}
    for name, body in bodies.items():
        assert body == (workspace / str(package.EVIDENCE_DIRECTORY) / name).read_bytes()
        assert package.SENTINEL not in body
    return bodies


def _assert_evidence(bodies, sent, received):
    requests = [json.loads(line) for line in bodies["requests.jsonl"].splitlines()]
    replies = [json.loads(line) for line in bodies["responses.jsonl"].splitlines()]
    ledger = [json.loads(line) for line in bodies["budget.jsonl"].splitlines()]
    reservations = [row for row in ledger if row["event"] == "reserved"]
    assert len(requests) == len(reservations) == len(sent)
    assert len(replies) == len(received)
    assert len(received) in (len(sent), len(sent) - 1)
    assert ledger[0]["prior_request_count"] == 52
    assert ledger[0]["prior_settled_micro_cny"] == 419388
    assert ledger[0]["lifetime_request_count"] == 52
    assert ledger[0]["reserved_micro_cny"] == 0
    for index, (request, reserve, actual) in enumerate(
            zip(requests, reservations, sent, strict=True)):
        operation = f"s{index + 1:02d}"
        assert request["ordinal"] == index + 1
        assert request["operation"] == reserve["operation"] == operation
        assert reserve["lifetime_request_count"] == 53 + index
        assert reserve["reserved_micro_cny"] == 3_000_000
        assert reserve["budget_micro_cny"] == 20_000_000
        assert actual == shadow_projection_driver.frozen_request(operation)
        assert request["raw_body"].encode() == actual
        digest = hashlib.sha256(actual).hexdigest()
        assert request["body_sha256"] == reserve["body_sha256"] == digest
        assert shadow_projection_driver.REQUEST_HASHES[operation] == digest
        if index < len(received):
            reply, returned = replies[index], received[index]
            assert reply["operation"] == operation
            assert base64.b64decode(reply["raw_base64"], validate=True) == returned
            assert reply["body_sha256"] == hashlib.sha256(returned).hexdigest()
            assert reply["status"] == 200
        payload = json.loads(actual)
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["max_tokens"] == 768 and payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "disabled"}
        assert json.loads(payload["messages"][1]["content"])["target_id"] == operation
    settlements = [row for row in ledger if row["event"] == "settled"]
    for index, settled in enumerate(settlements):
        envelope = json.loads(received[index])
        usage = envelope["usage"]
        assert settled["prompt_tokens"] == usage["prompt_tokens"] == 100
        assert settled["completion_tokens"] == usage["completion_tokens"] == 20
        assert settled["cost_micro_cny"] == 360
        assert settled["lifetime_settled_micro_cny"] == 419388 + (index + 1) * 360
        assert settled["reserved_micro_cny"] == 0


@pytest.fixture(scope="module")
def shadow_quote_package(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("shadow-quote-artifact-workspace")
    report, sent, received = _produce(workspace)
    assert report["matrix"]["passed"] and len(sent) == 6
    for relative in (".env", "config.json", "artifacts/provider-secret.txt",
                     "artifacts/companion-ai/.env", "artifacts/companion-ai/headers.json",
                     "artifacts/companion-ai/provider-config.json",
                     "artifacts/companion-ai/nested/env.txt"):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(package.SENTINEL)
    return workspace, report, sent, received


def test_six_review_runner_actual_workflow_zip_retains_exact_bytes(shadow_quote_package):
    workspace, report, sent, received = shadow_quote_package
    workflow = yaml.safe_load(package.WORKFLOW.read_text())
    paid_commands = [step.get("run", "") for step in workflow["jobs"]["paid"]["steps"]]
    commands = [shlex.split(command) for command in paid_commands]
    assert any(tokens[:3] == [".venv/bin/python", "-m",
                              "scripts.companion_ai_validation.run"]
               and {"--shadow-single-target", "--live"} <= set(tokens[3:]) for tokens in commands)
    assert any(tokens[:6] == ["uv", "run", "--frozen", "python", "-m", "scripts.companion_ai_validation.preflight"]
               and "--shadow-single-target" in tokens
               and ("--phase", "paid") in zip(tokens, tokens[1:]) for tokens in commands)
    raw = package._collect_zip(workspace, package.WORKFLOW.read_text())
    bodies = _unpack(raw, workspace)
    _assert_evidence(bodies, sent, received)
    assert json.loads(bodies["summary.json"]) == report
    assert report["matrix"]["pass_scope"] == "structure_only_not_semantic_correctness"
    assert report["matrix"]["product_modified"] is False
    assert len(report["matrix"]["cases"]) == 6
    assert report["budget"]["prior_request_count"] == 52
    assert report["budget"]["prior_settled_micro_cny"] == 419388
    assert report["budget"]["lifetime_request_count"] == 58
    assert report["budget"]["budget_micro_cny"] == 20_000_000
    assert report["budget"]["reserved_micro_cny"] == 0
    assert report["budget"]["lifetime_settled_micro_cny"] == 419388 + 6 * 360
    assert report["experiment"]["protocol"] == "shadow-quote-v2"
    assert report["experiment"]["projection_version"] == "input-only-projection-1"
    assert report["experiment"]["projection_mappings"] == shadow_projection_driver.projection_mappings()
    assert report["experiment"]["semantic_quality_established"] is False
    assert report["experiment"]["original_drafts_unchanged"] is True
    for state, raw_request, raw_response in zip(report["matrix"]["cases"], sent, received, strict=True):
        data = json.loads(json.loads(raw_request)["messages"][1]["content"])
        model_review = json.loads(json.loads(raw_response)["choices"][0]["message"]["content"])
        assert set(model_review) == {"protocol", "target_id", "verdict", "evidence", "rationale"}
        assert set(model_review["evidence"][0]) == {"source_id", "quote"}
        verified = state["review"]
        assert verified["target"] == data["target"]
        assert verified["target_id"] == data["target_id"]
        assert verified["evidence"][:1] == [{"source_id": "user", "start": 0,
                                         "end": len(data["sources"][0]["text"]),
                                         "quote": data["sources"][0]["text"]}]
        assert verified["scope"] == "structure_only_semantics_ungraded"
        if data["target_id"] in {"s03", "s04"}:
            from experiments.shadow_input_projection.projection import LONG
            mapping = report["experiment"]["projection_mappings"][data["target_id"]]
            kept = next(row for row in mapping["mappings"] if row["status"] == "kept")
            located, original = verified["evidence"][1], verified["original_evidence"][1]
            assert located["quote"] == original["quote"] == LONG
            assert located["start"] == kept["view"]["start"]
            assert original["start"] == kept["original"]["start"]
            assert original["start"] > located["start"]
            assert any(row["status"] == "merged" for row in mapping["mappings"])


@pytest.mark.parametrize("fail_at", [1, 3, 6])
@pytest.mark.parametrize("failure", ["invalid_json", "missing_usage", "unknown_network"])
def test_failures_preserve_available_raw_and_unknown_reservation(tmp_path, fail_at, failure):
    report, sent, received = _produce(tmp_path, fail_at=fail_at, failure=failure)
    assert not report["matrix"]["passed"] and len(sent) == fail_at
    raw = package._collect_zip(tmp_path, package.WORKFLOW.read_text())
    bodies = _unpack(raw, tmp_path)
    _assert_evidence(bodies, sent, received)
    if failure == "invalid_json":
        assert received[-1] == b'{"invalid":'
    if failure == "unknown_network":
        assert len(received) == fail_at - 1
    if failure == "missing_usage":
        assert "usage" not in json.loads(received[-1])
    assert report["budget"]["lifetime_request_count"] == 52 + fail_at
    assert report["budget"]["lifetime_settled_micro_cny"] == 419388 + (fail_at - 1) * 360
    assert report["budget"]["reserved_micro_cny"] == 3_000_000
    assert report["budget"]["halted"]
    assert report["matrix"]["cases"][fail_at - 1]["status"] == "failed"
    assert all(item["status"] == "not_attempted"
               for item in report["matrix"]["cases"][fail_at:])


def test_omitted_request_path_produces_detectably_incomplete_shadow_zip(shadow_quote_package):
    workspace, _, _, _ = shadow_quote_package
    workflow = yaml.safe_load(package.WORKFLOW.read_text())
    for step in workflow["jobs"]["paid"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            step["with"]["path"] = "\n".join(
                path for path in step["with"]["path"].splitlines()
                if path != "artifacts/companion-ai/requests.jsonl")
    raw = package._collect_zip(workspace, yaml.safe_dump(workflow))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert "requests.jsonl" not in archive.namelist()
    with pytest.raises(AssertionError, match="exactly four evidence files"):
        _unpack(raw, workspace)


@pytest.mark.parametrize("extra", [
    "artifacts/companion-ai/**", "artifacts/companion-ai", "artifacts/companion-ai/.env",
    "artifacts/companion-ai/headers.json", "artifacts/companion-ai/../provider-secret.txt",
    "artifacts/companion-ai/requests.jsonl",
])
def test_shadow_workflow_collection_rejects_expansion_or_duplicates(shadow_quote_package, extra):
    workspace, _, _, _ = shadow_quote_package
    workflow = yaml.safe_load(package.WORKFLOW.read_text())
    for step in workflow["jobs"]["paid"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact@"):
            step["with"]["path"] = step["with"]["path"].rstrip("\n") + "\n" + extra
    with pytest.raises(AssertionError, match="outside explicit allowlist|duplicate artifact path"):
        package._collect_zip(workspace, yaml.safe_dump(workflow))


@pytest.fixture(autouse=True)
def projection_clock(monkeypatch):
    monkeypatch.setattr(gate.time, "time", lambda: shadow_projection_continuation.PRICE_VALID_UNTIL - 3600)


@pytest.mark.parametrize("damage,code", [
    ("lineage", "admission_lineage"), ("old_helper", "admission_identity"),
    ("expiry", "price_review_expired"), ("dirty", "checkout_changed"),
    ("parent", "checkout_helper_mismatch"), ("late_lineage", "admission_lineage"),
    ("provider", "provider_metadata_mismatch"),
])
def test_main_projection_gates_before_any_secret_access(tmp_path, monkeypatch, capsys, damage, code):
    import subprocess
    import sys

    now, mono = gate.time.time(), gate.time.monotonic()
    record = {**shadow_projection_continuation.lineage_fields(), "schema": 2, "source_sha": "b" * 40,
              "helper_sha": "a" * 40, "run_id": "123", "run_attempt": "1", "checked_at": int(now),
              "checked_at_monotonic_ns": math.floor(mono * 1e9),
              "quote_deadline_monotonic_ns": math.floor((mono + 3600) * 1e9)}
    if damage == "lineage":
        record["prior_settled_micro_cny"] = 0
    if damage == "old_helper":
        record["helper_sha"] = shadow_projection_continuation.PREDECESSOR_HELPER_SHA
    if damage == "expiry":
        now = shadow_projection_continuation.PRICE_VALID_UNTIL - 600
        record["checked_at"] = int(now)
    reads = []

    class Environment(dict):
        def pop(self, key, *args):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().pop(key, *args)

        def get(self, key, *args):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().get(key, *args)

        def __getitem__(self, key):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().__getitem__(key)

    environment = Environment({"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": "offercontext/offerPilot",
        "GITHUB_REF": "refs/heads/feat/20261021-companion-life-v1",
        "GITHUB_WORKFLOW_SHA": "b" * 40,
        "GITHUB_WORKFLOW_REF": "offercontext/offerPilot/.github/workflows/"
        "companion-real-ai.yml@refs/heads/feat/20261021-companion-life-v1",
        run.MODEL_NAME: "wrong" if damage == "provider" else gate.MODEL,
        run.KEY_NAME: "synthetic-never-read"})
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(record))
    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--shadow-projection", "--admission-file",
                                     str(path), "--evidence-dir", str(tmp_path / "evidence")])

    def git(command, **kwargs):
        assert run.KEY_NAME not in kwargs["env"]
        if command[1] == "rev-parse":
            output = record["source_sha"].encode()
        elif command[1] == "cat-file":
            parent = "c" * 40 if damage == "parent" else record["helper_sha"]
            output = ("tree " + "d" * 40 + "\nparent " + parent + "\n\nmarker").encode()
            if damage == "late_lineage":
                monkeypatch.setattr(shadow_projection_continuation, "EVIDENCE_PATH", tmp_path / "absent.zip")
        else:
            output = b" M changed" if damage == "dirty" else b""
        return subprocess.CompletedProcess(command, 0, output)

    constructed = []

    def never_transport(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("No transport before gates")

    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "HTTPSUpstream", never_transport)
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == code
    assert not reads and not constructed and not (tmp_path / "evidence").exists()


def _admission():
    mono = gate.time.monotonic()
    return {**shadow_projection_continuation.lineage_fields(), "schema": 2,
            "source_sha": "b" * 40, "helper_sha": "a" * 40, "run_id": "123",
            "run_attempt": "1", "checked_at": int(gate.time.time()),
            "checked_at_monotonic_ns": math.floor(mono * 1e9),
            "quote_deadline_monotonic_ns": math.floor((mono + 3600) * 1e9)}


@pytest.mark.parametrize("remaining", [1200, 1199, 0, -1])
def test_admission_requires_full_twenty_minutes(remaining):
    record = _admission()
    now = shadow_projection_continuation.PRICE_VALID_UNTIL - remaining
    record["checked_at"] = int(now)
    if remaining == 1200:
        run.check_admission_time(record, now, shadow_projection=True)
    else:
        with pytest.raises(gate.GateError, match="price_review_expired"):
            run.check_admission_time(record, now, shadow_projection=True)


@pytest.mark.parametrize("old_flag", ["shadow_review", "shadow_quote_v2"])
def test_projection_profile_mutual_exclusion_and_old_admission_rejected(tmp_path, old_flag):
    old = gate.profile_lineage(**{old_flag: True})
    record = {**_admission(), **old.lineage_fields()}
    with pytest.raises(gate.GateError, match="admission_lineage"):
        run.check_admission(record, {}, gate.time.time(), shadow_projection=True)
    with pytest.raises(gate.GateError, match="experiment_mode"):
        run.execute(lambda raw: pytest.fail("no send"), "b" * 40, tmp_path / "run",
                    shadow_projection=True, **{old_flag: True})
    with pytest.raises(gate.GateError, match="continuation_contract_mismatch"):
        gate.Broker(tmp_path / "journal", lambda raw: pytest.fail("no send"), "b" * 40,
                    shadow_projection=True, window=gate.RunWindow(**{old_flag: True}))
    assert not (tmp_path / "run").exists()


def test_projection_preserves_old_profile_defaults():
    from scripts.companion_ai_validation import continuation, shadow_continuation, shadow_quote_continuation
    assert gate.profile_lineage() is continuation
    assert gate.profile_lineage(shadow_review=True) is shadow_continuation
    assert gate.profile_lineage(shadow_quote_v2=True) is shadow_quote_continuation
    assert continuation.lineage_fields()["prior_request_count"] == 36
    assert shadow_continuation.lineage_fields()["prior_request_count"] == 48
    assert shadow_quote_continuation.lineage_fields()["prior_request_count"] == 49
    assert gate.profile_lineage(shadow_projection=True).lineage_fields()["prior_request_count"] == 52


@pytest.mark.parametrize("old_flag", ["--shadow-review", "--shadow-quote-v2"])
def test_main_rejects_mixed_profile_flags_before_reading_admission(tmp_path, monkeypatch, capsys, old_flag):
    import sys
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--shadow-projection", old_flag,
                                    "--admission-file", str(tmp_path / "missing.json"),
                                    "--evidence-dir", str(tmp_path / "evidence")])
    def forbidden(*args, **kwargs):
        pytest.fail("Mixed profiles must not read admission, clean secrets or build transport")
    monkeypatch.setattr(run.Path, "open", forbidden)
    monkeypatch.setattr(run, "clean_environment", forbidden)
    monkeypatch.setattr(run, "HTTPSUpstream", forbidden)
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "validation_blocked"
    assert not (tmp_path / "evidence").exists()
