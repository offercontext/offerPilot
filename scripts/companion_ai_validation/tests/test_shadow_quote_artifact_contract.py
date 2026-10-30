"""Fake six-review runner -> actual workflow allowlist -> inspected ZIP bytes.

No upload service or real provider is exercised. A structurally valid fake reply
is deliberately not a claim about semantic accuracy or product improvement.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shlex
import zipfile

import pytest
import yaml

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import run, shadow_quote_continuation, shadow_quote_driver
from scripts.companion_ai_validation.tests import test_companion_ai_artifact_contract as package
from scripts.companion_ai_validation.tests.test_companion_ai_broker import response


def _review_response(raw):
    data = json.loads(json.loads(raw)["messages"][1]["content"])
    review = {"protocol": "shadow-quote-v2", "target_id": data["target_id"], "verdict": "unknown",
              "evidence": [{"source_id": "user", "quote": data["sources"][0]["text"]}],
              "rationale": "仅合成结构验收，不是语义判定。"}
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
        patch.setattr(gate.time, "time", lambda: shadow_quote_continuation.PRICE_VALID_UNTIL - 3600)
        report = run.execute(fake, "b" * 40, directory, shadow_quote_v2=True)
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
    assert ledger[0]["prior_request_count"] == 49
    assert ledger[0]["prior_settled_micro_cny"] == 414284
    assert ledger[0]["lifetime_request_count"] == 49
    assert ledger[0]["reserved_micro_cny"] == 0
    for index, (request, reserve, actual) in enumerate(
            zip(requests, reservations, sent, strict=True)):
        operation = f"s{index + 1:02d}"
        assert request["ordinal"] == index + 1
        assert request["operation"] == reserve["operation"] == operation
        assert reserve["lifetime_request_count"] == 50 + index
        assert reserve["reserved_micro_cny"] == 3_000_000
        assert reserve["budget_micro_cny"] == 20_000_000
        assert actual == shadow_quote_driver.frozen_request(operation)
        assert request["raw_body"].encode() == actual
        digest = hashlib.sha256(actual).hexdigest()
        assert request["body_sha256"] == reserve["body_sha256"] == digest
        assert shadow_quote_driver.REQUEST_HASHES[operation] == digest
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
        assert settled["lifetime_settled_micro_cny"] == 414284 + (index + 1) * 360
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
               and {"--shadow-quote-v2", "--live"} <= set(tokens[3:]) for tokens in commands)
    assert any(tokens[:3] == ["python", "-m", "scripts.companion_ai_validation.preflight"]
               and "--shadow-quote-v2" in tokens
               and ("--phase", "paid") in zip(tokens, tokens[1:]) for tokens in commands)
    raw = package._collect_zip(workspace, package.WORKFLOW.read_text())
    bodies = _unpack(raw, workspace)
    _assert_evidence(bodies, sent, received)
    assert json.loads(bodies["summary.json"]) == report
    assert report["matrix"]["pass_scope"] == "structure_only_not_semantic_correctness"
    assert report["matrix"]["product_modified"] is False
    assert len(report["matrix"]["cases"]) == 6
    assert report["budget"]["prior_request_count"] == 49
    assert report["budget"]["prior_settled_micro_cny"] == 414284
    assert report["budget"]["lifetime_request_count"] == 55
    assert report["budget"]["budget_micro_cny"] == 20_000_000
    assert report["budget"]["reserved_micro_cny"] == 0
    assert report["budget"]["lifetime_settled_micro_cny"] == 414284 + 6 * 360
    assert report["experiment"]["protocol"] == "shadow-quote-v2"
    assert report["experiment"]["original_drafts_unchanged"] is True
    for state, raw_request, raw_response in zip(report["matrix"]["cases"], sent, received, strict=True):
        data = json.loads(json.loads(raw_request)["messages"][1]["content"])
        model_review = json.loads(json.loads(raw_response)["choices"][0]["message"]["content"])
        assert set(model_review) == {"protocol", "target_id", "verdict", "evidence", "rationale"}
        assert set(model_review["evidence"][0]) == {"source_id", "quote"}
        verified = state["review"]
        assert verified["target"] == data["target"]
        assert verified["target_id"] == data["target_id"]
        assert verified["evidence"] == [{"source_id": "user", "start": 0,
                                         "end": len(data["sources"][0]["text"]),
                                         "quote": data["sources"][0]["text"]}]
        assert verified["scope"] == "structure_only_semantics_ungraded"


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
    assert report["budget"]["lifetime_request_count"] == 49 + fail_at
    assert report["budget"]["lifetime_settled_micro_cny"] == 414284 + (fail_at - 1) * 360
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
