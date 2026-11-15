"""Admission, environment isolation, and full fake broker/product integration."""
import json
import math
from pathlib import Path

import pytest

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import run
from scripts.companion_ai_validation import continuation


@pytest.fixture
def admission():
    now = gate.PRICE_VALID_UNTIL - 3600
    monotonic = gate.time.monotonic()
    record = {**continuation.lineage_fields(), "schema": 2, "source_sha": "b" * 40, "helper_sha": "a" * 40,
              "run_id": "123", "run_attempt": "1", "checked_at": int(now),
              "checked_at_monotonic_ns": math.floor(monotonic * 1e9),
              "quote_deadline_monotonic_ns": math.floor((monotonic + 3600) * 1e9)}
    metadata = {"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "offercontext/offerPilot",
                "GITHUB_REF": "refs/heads/feat/20261021-companion-life-v1",
                "GITHUB_WORKFLOW_SHA": "b" * 40,
                "GITHUB_WORKFLOW_REF": ("offercontext/offerPilot/.github/workflows/"
                                        "companion-real-ai.yml@refs/heads/"
                                        "feat/20261021-companion-life-v1")}
    return record, metadata, now


def test_admission_is_exact_current_run_and_fresh(admission):
    record, metadata, now = admission
    assert run.check_admission(record, metadata, now) == "b" * 40


@pytest.mark.parametrize("key,value", [
    ("GITHUB_SHA", "c" * 40), ("GITHUB_RUN_ID", "124"), ("GITHUB_RUN_ATTEMPT", "2"),
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "someone/fork"),
    ("GITHUB_REF", "refs/pull/123/merge"),
    ("GITHUB_WORKFLOW_SHA", "c" * 40), ("GITHUB_WORKFLOW_REF", "wrong-workflow"),
])
def test_bad_run_identity_never_admits(admission, key, value):
    record, metadata, now = admission
    metadata[key] = value
    with pytest.raises(gate.GateError, match="admission_identity"):
        run.check_admission(record, metadata, now)


@pytest.mark.parametrize("age", [-1, 121])
def test_stale_or_future_admission_is_not_reusable(admission, age):
    record, metadata, now = admission
    with pytest.raises(gate.GateError, match="admission_expired"):
        run.check_admission(record, metadata, now + age)


@pytest.mark.parametrize("key", sorted(continuation.LINEAGE_KEYS))
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_every_lineage_field_is_mandatory_and_exact(admission, key, damage):
    record, metadata, now = admission
    if damage == "missing":
        del record[key]
    else:
        record[key] = False if type(record[key]) is int else "changed"
    with pytest.raises(gate.GateError, match="admission_required|admission_lineage"):
        run.check_admission(record, metadata, now)


@pytest.mark.parametrize("key,value", [
    ("schema", 1), ("schema", True), ("schema", 2.0),
    ("run_attempt", "2"), ("run_attempt", 1), ("run_attempt", True),
    ("run_id", 123), ("run_id", "0"), ("run_id", "01"), ("run_id", "１２３"),
    ("run_id", "123\n"), ("checked_at", True),
    ("checked_at_monotonic_ns", True), ("checked_at_monotonic_ns", -1),
    ("checked_at_monotonic_ns", 1.0), ("quote_deadline_monotonic_ns", True),
    ("quote_deadline_monotonic_ns", -1), ("quote_deadline_monotonic_ns", 1.0),
    ("prior_settled_micro_cny", 0), ("prior_settled_micro_cny", False),
    ("prior_settled_micro_cny", -1), ("prior_settled_micro_cny", 315652.0),
    ("prior_settled_micro_cny", 103154), ("prior_settled_micro_cny", 105362),
    ("prior_settled_micro_cny", 311670), ("prior_request_count", 12),
    ("correction_ordinal", 1), ("correction_ordinal", 2),
    ("prior_reserved_micro_cny", 1), ("prior_request_count", 0),
    ("source_sha", continuation.PREDECESSOR_SOURCE_SHA),
    ("helper_sha", continuation.PREDECESSOR_HELPER_SHA),
])
def test_old_or_coerced_admission_values_cannot_reset_continuation(admission, key, value):
    record, metadata, now = admission
    record[key] = value
    with pytest.raises(gate.GateError):
        run.check_admission(record, metadata, now)


@pytest.mark.parametrize("field", ["source_sha", "helper_sha"])
@pytest.mark.parametrize("historic_sha", [
    "3f139ada435b2398e52b65162208a8cbb818b8ef",
    "6329657d841b1bc4d8e3a3cdb7618df92c5a140a",
    "64bcd7ab737650415b1d987b1e56681aca6f9fcf",
    "e6cbb73e02863d79332855fadfe75f0e699312ae",
    "744fb0f6a716cce3e75e14eb8bf9dfe585aff4c2",
    "5b4d7a75ff40a3f8f7cbee9b3e6300bfa6896a10",
])
def test_neither_historical_helper_nor_request_can_be_reused(admission, field, historic_sha):
    record, metadata, now = admission
    record[field] = historic_sha
    if field == "source_sha":
        metadata["GITHUB_SHA"] = metadata["GITHUB_WORKFLOW_SHA"] = historic_sha
    with pytest.raises(gate.GateError, match="admission_identity"):
        run.check_admission(record, metadata, now)


def test_new_fixed_window_agrees_without_rewriting_historical_quote():
    from datetime import datetime, timezone
    assert gate.PRICE_VALID_UNTIL == continuation.PRICE_VALID_UNTIL
    assert gate.PRICE_VALID_UNTIL == datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()


def test_admission_rejects_extra_fields(admission):
    record, metadata, now = admission
    record["initial_settled_micro_cny"] = 0
    with pytest.raises(gate.GateError, match="admission_required"):
        run.check_admission(record, metadata, now)


def test_admission_cannot_extend_reviewed_price_window(admission):
    record, metadata, _ = admission
    record["checked_at"] = int(gate.PRICE_VALID_UNTIL - 89)
    with pytest.raises(gate.GateError, match="price_review_expired"):
        run.check_admission(record, metadata, record["checked_at"])


@pytest.mark.parametrize("seconds_left", [1200, 1199])
def test_admission_quote_must_cover_full_session(admission, seconds_left):
    record, metadata, _ = admission
    record["checked_at"] = int(gate.PRICE_VALID_UNTIL - seconds_left)
    if seconds_left == 1200:
        assert run.check_admission(record, metadata, record["checked_at"]) == record["source_sha"]
    else:
        with pytest.raises(gate.GateError, match="price_review_expired"):
            run.check_admission(record, metadata, record["checked_at"])


def test_clean_environment_drops_every_inherited_key(monkeypatch):
    fake = {"SSLKEYLOGFILE": "private", "SSL_CERT_FILE": "private", "SSL_CERT_DIR": "private",
            "HTTPS_PROXY": "private", run.KEY_NAME: "private", "GH_TOKEN": "private",
            "UNEXPECTED_CREDENTIAL": "private"}
    monkeypatch.setattr(run.os, "environ", fake)
    run.clean_environment()
    assert "private" not in json.dumps(fake)
    assert set(fake) == {"PATH", "LANG", "LITELLM_LOCAL_MODEL_COST_MAP", "PYTHONNOUSERSITE",
                         "PYTHONDONTWRITEBYTECODE", "DO_NOT_TRACK"}


def test_temporary_config_contains_only_loopback_credential_and_no_fallback(tmp_path):
    run.write_config(tmp_path, "http://127.0.0.1:12345/v1", "synthetic-loopback-only")
    config = json.loads((tmp_path / "config.json").read_bytes())
    assert config["fallback_provider_ids"] == []
    assert config["chat_auto_approve_writes"] is False
    profile, = config["providers"]
    assert profile["api_key"] == "synthetic-loopback-only"
    assert profile["max_output_tokens"] == 512
    assert profile["supports_json_schema"] is False
    assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600


def test_run_matrix_has_exact_one_completion_per_stage(tmp_path, monkeypatch):
    import scripts.companion_ai_validation.driver as driver
    import urllib.request
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import payload, response

    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
    calls = []

    def fake(raw):
        calls.append(gate.decode(raw))
        return 200, gate.encode(response())

    def matrix(data_dir, stage, *, record_response):
        for operation in gate.OPERATIONS:
            with stage(operation):
                config = json.loads((data_dir / "config.json").read_bytes())["providers"][0]
                request = urllib.request.Request(
                    config["base_url"] + "/chat/completions", gate.encode(payload(operation)),
                    {"Authorization": "Bearer " + config["api_key"],
                     "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as result:
                    assert result.status == 200
        return {"passed": True, "responses": ["Synthetic response"]}

    monkeypatch.setattr(driver, "run_matrix", matrix)
    report = run.execute(fake, "b" * 40, tmp_path / "evidence")
    assert report["matrix"]["passed"], report
    assert len(calls) == 12
    assert report["budget"]["request_count"] == 12
    assert report["budget"]["settled_micro_cny"] == continuation.PRIOR_SETTLED + 12 * 360
    assert report["schema"] == 2
    assert report["budget"]["prior_settled_micro_cny"] == 315652
    assert report["budget"]["batch_settled_micro_cny"] == 12 * 360
    assert report["budget"]["lifetime_settled_micro_cny"] == 315652 + 12 * 360
    assert report["budget"]["prior_request_count"] == 36
    assert report["budget"]["batch_request_count"] == 12
    assert report["budget"]["lifetime_request_count"] == 48
    assert report["budget"]["accounting_basis"] == "conservative_usage_not_provider_invoice"
    assert report["budget"]["reserved_micro_cny"] == 0
    assert report["budget"]["stages_complete"] == 12
    assert {p.name for p in (tmp_path / "evidence").iterdir()} == {"summary.json", "budget.jsonl", "responses.jsonl", "requests.jsonl"}
    import hashlib
    requests_path = tmp_path / "evidence" / "requests.jsonl"
    assert requests_path.stat().st_mode & 0o777 == 0o600
    captured = [json.loads(line) for line in requests_path.read_text().splitlines()]
    journal = [json.loads(line) for line in (tmp_path / "evidence/budget.jsonl").read_text().splitlines()]
    reserves = [entry for entry in journal if entry["event"] == "reserved"]
    assert len(captured) == 12
    for ordinal, (entry, sent, reserve) in enumerate(zip(captured, calls, reserves, strict=True), 1):
        assert set(entry) == {"operation", "ordinal", "body_sha256", "raw_body"}
        assert entry["ordinal"] == ordinal and entry["operation"] == gate.OPERATIONS[ordinal - 1]
        assert json.loads(entry["raw_body"]) == sent
        assert entry["body_sha256"] == hashlib.sha256(entry["raw_body"].encode()).hexdigest()
        assert entry["body_sha256"] == reserve["body_sha256"]
    assert "Authorization" not in requests_path.read_text()
    assert "Bearer " not in requests_path.read_text() and "api_key" not in requests_path.read_text()
    text = (tmp_path / "evidence" / "budget.jsonl").read_text()
    assert "messages" not in text and "api_key" not in text
    assert "Synthetic response" in (tmp_path / "evidence" / "summary.json").read_text()


def test_workflow_has_one_secret_step_and_no_pr_or_write_path():
    path = Path(__file__).resolve().parents[3] / ".github/workflows/companion-real-ai.yml"
    workflow = path.read_text()
    assert workflow.count("${{ secrets.OFFERPILOT_COMPANION_DEEPSEEK_20261021 }}") == 1
    assert "  workflow_dispatch:" not in workflow
    assert "  pull_request:" not in workflow
    assert "  pull_request_target:" not in workflow
    assert ": write" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "environment: offerpilot-companion-ai-20261021" in workflow
    assert workflow.count("github.sha == vars.OFFERPILOT_COMPANION_AI_REQUEST_SHA") == 2
    assert "persist-credentials: true" not in workflow
    for phase in ("inspect", "before", "paid"):
        assert f"python -m scripts.companion_ai_validation.preflight --phase {phase}" in workflow
    assert "artifacts/companion-ai/summary.json" in workflow
    assert "artifacts/companion-ai/budget.jsonl" in workflow


def test_main_never_inherits_provider_secret_into_git_or_driver(tmp_path, monkeypatch, admission):
    import subprocess
    import sys
    record, metadata, now = admission
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(record))
    environment = {**metadata, run.KEY_NAME: "synthetic-secret-do-not-inherit",
                   run.MODEL_NAME: gate.MODEL,
                   "SSLKEYLOGFILE": "synthetic-untrusted-file", "GH_TOKEN": "synthetic-gh-token"}
    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--admission-file", str(path),
                                     "--evidence-dir", str(tmp_path / "evidence")])
    commands = []

    def git(command, **kwargs):
        commands.append(command)
        assert set(kwargs["env"]) == {
            "PATH", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_NO_REPLACE_OBJECTS",
        }
        assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert "synthetic" not in json.dumps(kwargs["env"])
        if command[1] == "cat-file":
            output = ("tree " + "c" * 40 + "\nparent " + record["helper_sha"]
                      + "\n\nsynthetic correction marker").encode()
        else:
            output = record["source_sha"].encode() if len(commands) == 1 else b""
        return subprocess.CompletedProcess(command, 0, output)

    class Transport:
        def __init__(self, key, *, window):
            assert isinstance(window, gate.RunWindow)
            self.window = window
            assert key == "synthetic-secret-do-not-inherit"
            assert "synthetic" not in json.dumps(environment)

    def execute(transport, sha, evidence, *, window):
        assert window is transport.window
        assert isinstance(transport, Transport)
        assert sha == record["source_sha"]
        assert "synthetic" not in json.dumps(environment)
        return {"matrix": {"passed": True}, "budget": {}}

    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "HTTPSUpstream", Transport)
    monkeypatch.setattr(run, "execute", execute)
    assert run.main() == 0
    assert len(commands) == 3


@pytest.mark.parametrize("damage,code", [
    ("lineage", "admission_lineage"), ("extra", "admission_required"),
    ("duplicate", "duplicate_json_key"), ("missing_predecessor", "admission_lineage"),
    ("damaged_predecessor", "admission_lineage"), ("late_damage", "admission_lineage"),
    ("checkout_sha", "checkout_changed"), ("checkout_dirty", "checkout_changed"),
    ("checkout_helper", "checkout_helper_mismatch"),
    ("checkout_merge", "checkout_helper_mismatch"),
    ("checkout_root", "checkout_helper_mismatch"),
    ("slow_checkout", "admission_expired"), ("provider", "provider_metadata_mismatch"),
    ("cli_seed", "validation_blocked"),
])
def test_main_blocks_bad_lineage_or_source_before_any_dedicated_key_access(
        tmp_path, monkeypatch, admission, capsys, damage, code):
    import subprocess
    import sys

    record, metadata, initial_now = admission
    key_reads = []

    class Environment(dict):
        def get(self, name, *args):
            if name == run.KEY_NAME:
                key_reads.append(name)
            return super().get(name, *args)

        def pop(self, name, *args):
            if name == run.KEY_NAME:
                key_reads.append(name)
            return super().pop(name, *args)

        def __getitem__(self, name):
            if name == run.KEY_NAME:
                key_reads.append(name)
            return super().__getitem__(name)

    environment = Environment({**metadata, run.MODEL_NAME: gate.MODEL,
                               run.KEY_NAME: "synthetic-never-read"})
    path = tmp_path / "admission.json"
    now = [initial_now]
    if damage == "lineage":
        record["prior_settled_micro_cny"] = 0
    elif damage == "extra":
        record["initial_settled_micro_cny"] = 0
    elif damage == "provider":
        environment[run.MODEL_NAME] = "wrong"
    path.write_text(json.dumps(record))
    if damage == "duplicate":
        path.write_text('{"schema":1,' + json.dumps(record)[1:])
    damaged = tmp_path / "changed.zip"
    if damage in {"damaged_predecessor", "late_damage"}:
        damaged.write_bytes(continuation.EVIDENCE_PATH.read_bytes() + b"altered")
    if damage in {"missing_predecessor", "damaged_predecessor"}:
        monkeypatch.setattr(continuation, "EVIDENCE_PATH", damaged)
    args = ["run", "--live", "--admission-file", str(path),
            "--evidence-dir", str(tmp_path / "evidence")]
    if damage == "cli_seed":
        args += ["--initial-settled-micro-cny", "0"]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(gate.time, "time", lambda: now[0])
    commands = []

    def git(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            sha = "c" * 40 if damage == "checkout_sha" else record["source_sha"]
            return subprocess.CompletedProcess(command, 0, sha.encode())
        if command[1] == "cat-file":
            parent = "c" * 40 if damage == "checkout_helper" else record["helper_sha"]
            header = "tree " + "d" * 40 + "\n"
            if damage != "checkout_root":
                header += "parent " + parent + "\n"
            if damage == "checkout_merge":
                header += "parent " + "e" * 40 + "\n"
            return subprocess.CompletedProcess(command, 0, (header + "\nmarker").encode())
        if damage == "slow_checkout":
            now[0] += 121
        if damage == "late_damage":
            monkeypatch.setattr(continuation, "EVIDENCE_PATH", damaged)
        dirty = b" M scripts/companion_ai_validation/run.py" if damage == "checkout_dirty" else b""
        return subprocess.CompletedProcess(command, 0, dirty)

    transport_calls = []

    def transport(key, *, window):
        transport_calls.append(key)
        raise AssertionError("No transport may be constructed")

    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "HTTPSUpstream", transport)
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == code
    assert key_reads == [] and transport_calls == []
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("stage", [
    "handoff", "admission_read", "first_lineage", "checkout", "last_lineage",
])
@pytest.mark.parametrize("elapsed,code", [(121, "admission_expired"), (2, "price_review_expired")])
def test_pre_key_io_stalls_and_wall_rollback_cannot_refresh_admission_or_quote(
        tmp_path, monkeypatch, admission, capsys, stage, elapsed, code):
    import io
    import subprocess
    import sys

    record, metadata, _ = admission
    wall = [gate.PRICE_VALID_UNTIL - 1201]
    monotonic = [100.0]
    record["checked_at"] = int(wall[0])
    record["checked_at_monotonic_ns"] = 100_000_000_000
    record["quote_deadline_monotonic_ns"] = 1_301_000_000_000
    if stage == "handoff":
        # The job spent time between processes, but wall time rolled back to
        # the original checked_at before main captured its own clock anchor.
        monotonic[0] += elapsed
    path = tmp_path / "admission.json"
    raw = gate.encode(record)
    path.write_bytes(raw)
    key_reads = []

    class Environment(dict):
        def pop(self, name, *args):
            if name == run.KEY_NAME:
                key_reads.append(name)
            return super().pop(name, *args)

        def get(self, name, *args):
            if name == run.KEY_NAME:
                key_reads.append(name)
            return super().get(name, *args)

    def stall():
        monotonic[0] += elapsed
        wall[0] -= elapsed

    original_open = Path.open

    class DelayedRead(io.BytesIO):
        def read(self, *args):
            value = super().read(*args)
            stall()
            return value

    def open_file(self, *args, **kwargs):
        if self == path and stage == "admission_read":
            return DelayedRead(raw)
        return original_open(self, *args, **kwargs)

    original_lineage = continuation.validate_lineage
    lineage_calls = []

    def lineage(value):
        original_lineage(value)
        lineage_calls.append(value)
        if ((stage == "first_lineage" and len(lineage_calls) == 1)
                or (stage == "last_lineage" and len(lineage_calls) == 2)):
            stall()

    def git(command, **kwargs):
        if command[1] == "rev-parse":
            output = record["source_sha"].encode()
        elif command[1] == "cat-file":
            output = ("tree " + "c" * 40 + "\nparent " + record["helper_sha"] + "\n\n").encode()
        else:
            output = b""
            if stage == "checkout":
                stall()
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(run.os, "environ", Environment({**metadata, run.MODEL_NAME: gate.MODEL,
                                                       run.KEY_NAME: "synthetic-never-read"}))
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(continuation, "validate_lineage", lineage)
    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--admission-file", str(path),
                                     "--evidence-dir", str(tmp_path / "evidence")])
    transport_calls = []

    def transport(key, *, window):
        transport_calls.append(key)
        raise AssertionError("The stale admission must block before transport")

    monkeypatch.setattr(run, "HTTPSUpstream", transport)
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == code
    assert key_reads == [] and transport_calls == []
    assert not (tmp_path / "evidence").exists()


def synthetic_entry(operation):
    return {'operation': operation, 'user_input': 'Synthetic user',
            'assistant_text': 'Synthetic successful reply', 'expected_context': None,
            'source_facts_before_projection': None, 'snapshot': {}, 'provenance': {},
            'ordinary_turn_read_only': True, 'source_verified': True}


@pytest.mark.parametrize("failure_slot", range(12))
def test_every_failure_slot_preserves_known_cost_unknown_reservation_and_samples(
        tmp_path, monkeypatch, failure_slot):
    import urllib.request
    import scripts.companion_ai_validation.driver as driver
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import payload, response

    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
    calls = []

    def fake(raw):
        calls.append(raw)
        result = response() if len(calls) <= failure_slot else {"unknown_usage": True}
        return 200, gate.encode(result)

    def matrix(data_dir, stage, *, record_response):
        for operation in gate.OPERATIONS:
            with stage(operation):
                config = json.loads((data_dir / "config.json").read_bytes())["providers"][0]
                request = urllib.request.Request(
                    config["base_url"] + "/chat/completions", gate.encode(payload(operation)),
                    {"Authorization": "Bearer " + config["api_key"],
                     "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as result:
                    assert result.status == 200
                record_response(synthetic_entry(operation))
        pytest.fail("The unknown response must halt without any later request")

    monkeypatch.setattr(driver, "run_matrix", matrix)
    report = run.execute(fake, "b" * 40, tmp_path / "evidence")
    assert report["matrix"]["passed"] is False and len(calls) == failure_slot + 1
    budget = report["budget"]
    assert budget["halted"] is True
    assert budget["prior_settled_micro_cny"] == 315652
    assert budget["batch_settled_micro_cny"] == failure_slot * 360
    assert budget["batch_reserved_micro_cny"] == 3_000_000
    assert budget["lifetime_liability_micro_cny"] == 3_315_652 + failure_slot * 360
    assert budget["batch_request_count"] == failure_slot + 1
    assert budget["lifetime_request_count"] == 37 + failure_slot
    assert budget["stages_complete"] == failure_slot
    samples = (tmp_path / "evidence/responses.jsonl").read_text().splitlines()
    assert [json.loads(sample) for sample in samples] == [
        synthetic_entry(operation) for operation in gate.OPERATIONS[:failure_slot]
    ]


def test_successful_samples_persist_without_replacing_or_exposing_config(tmp_path):
    path = tmp_path / 'responses.jsonl'
    evidence = run.ResponseEvidence(path)
    evidence.record(synthetic_entry(gate.OPERATIONS[0]))
    assert json.loads(path.read_text()) == synthetic_entry(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match='synthetic_evidence_shape'):
        evidence.record(synthetic_entry(gate.OPERATIONS[0]))
    evidence.close()
    with pytest.raises(FileExistsError):
        run.ResponseEvidence(path)
    assert len(path.read_text().splitlines()) == 1


@pytest.mark.parametrize('change', [
    lambda e: e.update(api_key='must-not-log'),
    lambda e: e.update(source_verified=False),
    lambda e: e.update(assistant_text='x' * 8193),
    lambda e: e.update(provenance={'too_large': 'x' * 40000}),
])
def test_unsafe_or_unbounded_sample_is_never_written(tmp_path, change):
    path = tmp_path / 'responses.jsonl'
    evidence = run.ResponseEvidence(path)
    entry = synthetic_entry(gate.OPERATIONS[0])
    change(entry)
    with pytest.raises(gate.GateError):
        evidence.record(entry)
    evidence.close()
    assert path.read_bytes() == b''


@pytest.mark.parametrize('damage', ['skip', 'missing', 'duplicate', 'deselected', 'failed'])
def test_mandatory_linux_aggregate_rejects_incomplete_nodes(damage):
    from types import SimpleNamespace
    from scripts.companion_ai_validation.test_gate import CompletionLedger
    ledger = CompletionLedger()
    ledger.collected = ['test_one']
    for phase in ['setup', 'call', 'teardown']:
        ledger.pytest_runtest_logreport(SimpleNamespace(nodeid='test_one', when=phase, outcome='passed'))
    assert ledger.report(0)['passed']
    if damage == 'skip':
        ledger.finished['test_one']['call'] = 'skipped'
    elif damage == 'missing':
        ledger.finished.clear()
    elif damage == 'duplicate':
        ledger.pytest_runtest_logreport(SimpleNamespace(nodeid='test_one', when='call', outcome='passed'))
    elif damage == 'deselected':
        ledger.pytest_deselected([SimpleNamespace(nodeid='test_two')])
    else:
        ledger.finished['test_one']['teardown'] = 'failed'
    assert not ledger.report(0)['passed']


def test_actual_product_chat_matrix_through_strict_budget_and_sse_boundary(tmp_path, monkeypatch):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import response
    monkeypatch.setattr(gate.time, 'time', lambda: gate.PRICE_VALID_UNTIL - 3600)
    calls = []

    def fake(raw):
        body = gate.decode(raw)
        assert body['model'] == gate.MODEL and body['max_tokens'] == 512
        assert body['thinking'] == {'type': 'disabled'} and body['stream'] is False
        calls.append(body)
        return 200, gate.encode(response(content='合成验收回复，仅用于验证真实 API 与账本边界。'))

    report = run.execute(fake, 'b' * 40, tmp_path / 'evidence')
    assert report['matrix']['passed'], report
    assert report['matrix']['pass_scope'] == 'mechanism_only'
    assert report['matrix']['tone_review_required'] is True
    assert len(calls) == 12
    assert report['budget']['request_count'] == 12
    assert report['budget']['settled_micro_cny'] == continuation.PRIOR_SETTLED + 12 * 360
    assert report['budget']['reserved_micro_cny'] == 0
    entries = [json.loads(line) for line in (tmp_path / 'evidence/responses.jsonl').read_text().splitlines()]
    assert [entry['operation'] for entry in entries] == list(gate.OPERATIONS)
    assert all(entry['source_verified'] for entry in entries)
    assert all(call['tools'] and call['tool_choice'] == 'auto' for call in calls)


def test_request_evidence_has_fixed_path_exclusive_creation_and_twelve_slot_bound(tmp_path):
    import hashlib
    evidence = run.RequestEvidence(tmp_path)
    body = b'{"messages":[{"role":"user","content":"synthetic"}]}'
    for _ in gate.OPERATIONS:
        evidence.record(body)
    with pytest.raises(gate.GateError, match='request_evidence_shape'):
        evidence.record(body)
    evidence.close()
    with pytest.raises(FileExistsError):
        run.RequestEvidence(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / 'requests.jsonl').read_text().splitlines()]
    assert [row['operation'] for row in rows] == list(gate.OPERATIONS)
    assert [row['ordinal'] for row in rows] == list(range(1, 13))
    assert all(row['body_sha256'] == hashlib.sha256(body).hexdigest() for row in rows)


@pytest.mark.parametrize('body', [b'', b'x' * (gate.MAX_BYTES + 1), b'\xff', 'not bytes'])
def test_request_evidence_rejects_bad_bodies_without_advancing(tmp_path, body):
    evidence = run.RequestEvidence(tmp_path)
    with pytest.raises(gate.GateError, match='request_evidence_shape'):
        evidence.record(body)
    evidence.close()
    assert (tmp_path / 'requests.jsonl').read_bytes() == b''


@pytest.mark.parametrize('failure', ['fsync', 'short_write', 'write_error', 'deadline'])
def test_request_capture_failure_stops_before_send_retaining_reservation(tmp_path, monkeypatch, failure):
    import os
    import urllib.request
    import scripts.companion_ai_validation.driver as driver
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import payload, response

    wall, monotonic = [gate.PRICE_VALID_UNTIL - 3600], [1000.0]
    monkeypatch.setattr(gate.time, 'time', lambda: wall[0])
    monkeypatch.setattr(gate.time, 'monotonic', lambda: monotonic[0])
    real_record, real_fsync, real_write = run.RequestEvidence.record, os.fsync, os.write

    recorded_instances = []
    def bad_record(self, body):
        recorded_instances.append(self)
        def bad_fsync(fd):
            if fd == self._fd:
                if failure == 'fsync':
                    raise OSError('synthetic write failure - must not leak')
                if failure == 'deadline':
                    monotonic[0] += gate.SESSION_SECONDS + 1
            return real_fsync(fd)
        def bad_write(fd, value):
            if fd == self._fd and failure == 'write_error':
                raise OSError('synthetic write error - must not leak')
            return 0 if fd == self._fd and failure == 'short_write' else real_write(fd, value)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fsync', bad_fsync)
            patch.setattr(os, 'write', bad_write)
            real_record(self, body)

    monkeypatch.setattr(run.RequestEvidence, 'record', bad_record)
    def matrix(data_dir, stage, *, record_response):
        with stage(gate.OPERATIONS[0]):
            config = json.loads((data_dir / 'config.json').read_bytes())['providers'][0]
            request = urllib.request.Request(
                config['base_url'] + '/chat/completions', gate.encode(payload(gate.OPERATIONS[0])),
                {'Authorization': 'Bearer ' + config['api_key'], 'Content-Type': 'application/json'})
            urllib.request.urlopen(request, timeout=5)
        pytest.fail('Capture failure must block the request')
    monkeypatch.setattr(driver, 'run_matrix', matrix)
    calls = []
    def fake(body):
        calls.append(body)
        return 200, gate.encode(response())
    report = run.execute(fake, 'b' * 40, tmp_path / 'evidence')
    assert report['matrix']['passed'] is False and calls == []
    assert report['budget']['halted'] is True
    assert report['budget']['batch_reserved_micro_cny'] == 3_000_000
    assert report['budget']['batch_settled_micro_cny'] == 0
    assert report['budget']['lifetime_liability_micro_cny'] == continuation.PRIOR_SETTLED + 3_000_000
    assert 'synthetic write failure' not in json.dumps(report)
    assert 'synthetic write error' not in json.dumps(report)
    assert recorded_instances and recorded_instances[0]._fd == -1


def test_request_evidence_partial_writes_are_completed_before_success(tmp_path, monkeypatch):
    import os
    evidence = run.RequestEvidence(tmp_path)
    real_write, real_fsync = os.write, os.fsync
    calls = []
    def partial(fd, body):
        calls.append('write')
        return real_write(fd, body[:7])
    def synced(fd):
        calls.append('fsync')
        return real_fsync(fd)
    monkeypatch.setattr(os, 'write', partial)
    monkeypatch.setattr(os, 'fsync', synced)
    body = b'{"messages":[]}'
    evidence.record(body)
    evidence.close()
    assert calls[-1] == 'fsync' and calls.count('write') > 1
    assert json.loads((tmp_path / 'requests.jsonl').read_text())['raw_body'].encode() == body
