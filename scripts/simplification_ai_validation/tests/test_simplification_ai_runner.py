"""Admission, environment isolation, and full fake broker/product integration."""
import json
from pathlib import Path

import pytest

from scripts.simplification_ai_validation import broker as gate
from scripts.simplification_ai_validation import run
from scripts.simplification_ai_validation.tests.test_simplification_ai_driver import (
    fake_completion,
)


@pytest.fixture
def admission(monkeypatch):
    now = gate.PRICE_VALID_UNTIL - 3600
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 1000.0)
    record = {"schema": 2, "source_sha": "b" * 40, "helper_sha": "a" * 40,
              "run_id": "123", "checked_at": now, "checked_monotonic": 1000.0,
              "boot_id": gate.boot_identity(), "price_window": gate.PriceWindow.capture().record()}
    metadata = {"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "offercontext/offerPilot",
                "GITHUB_REF": "refs/heads/feat/20261021-product-simplification"}
    return record, metadata, now


def test_admission_is_exact_current_run_and_fresh(admission):
    record, metadata, now = admission
    assert run.check_admission(record, metadata, gate.PriceWindow.capture()) [0] == "b" * 40


@pytest.mark.parametrize("key,value", [
    ("GITHUB_SHA", "c" * 40), ("GITHUB_RUN_ID", "124"), ("GITHUB_RUN_ATTEMPT", "2"),
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "someone/fork"),
    ("GITHUB_REF", "refs/pull/123/merge"),
])
def test_bad_run_identity_never_admits(admission, key, value):
    record, metadata, now = admission
    metadata[key] = value
    with pytest.raises(gate.GateError, match="admission_identity"):
        run.check_admission(record, metadata, gate.PriceWindow.capture())


@pytest.mark.parametrize("age", [-1, 121])
def test_stale_or_future_admission_is_not_reusable(admission, age, monkeypatch):
    record, metadata, now = admission
    monkeypatch.setattr(gate.time, "time", lambda: now + age)
    with pytest.raises(gate.GateError, match="admission_expired"):
        run.check_admission(record, metadata, gate.PriceWindow.capture())


def test_admission_cannot_extend_reviewed_price_window(admission, monkeypatch):
    record, metadata, _ = admission
    record["checked_at"] = int(gate.PRICE_VALID_UNTIL - 89)
    monkeypatch.setattr(gate.time, "time", lambda: record["checked_at"])
    with pytest.raises(gate.GateError, match="price_review_expired"):
        run.check_admission(record, metadata, gate.PriceWindow.capture())


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
    assert profile["max_output_tokens"] == 4096
    assert profile["supports_json_schema"] is False
    assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("format_repairs", [False, True])
def test_genuine_cli_and_http_matrix_through_actual_budget_broker(tmp_path, monkeypatch,
                                                               format_repairs):
    # Do not make the offline integration depend on the live price quote's date.
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
    calls = []
    completed = 0

    def fake(raw):
        nonlocal completed
        operation = gate.OPERATIONS[completed]
        body = gate.decode(raw)
        calls.append(operation)
        reply = fake_completion(body, operation)
        if (format_repairs and operation.startswith(("quick_", "event_"))
                and calls.count(operation) == 1):
            reply["choices"][0]["message"]["content"] = "not JSON"
        else:
            completed += 1
        return 200, gate.encode(reply)

    report = run.execute(fake, "b" * 40, tmp_path / "evidence")
    assert report["matrix"]["passed"], report
    expected = [operation for operation in gate.OPERATIONS
                for _ in range(2 if format_repairs and
                               operation.startswith(("quick_", "event_")) else 1)]
    assert calls == expected
    assert report["budget"]["request_count"] == len(expected)
    assert report["budget"]["settled_micro_cny"] == len(expected) * 1000
    assert report["budget"]["reserved_micro_cny"] == 0
    assert report["budget"]["stages_complete"] == 12
    assert {p.name for p in (tmp_path / "evidence").iterdir()} == {"summary.json", "budget.jsonl"}
    text = (tmp_path / "evidence" / "budget.jsonl").read_text()
    assert "messages" not in text and "evidence_catalog" not in text and "api_key" not in text
    assert sum(json.loads(line)["event"] == "reserved" for line in text.splitlines()) == len(expected)


def test_workflow_has_one_secret_step_and_no_pr_or_write_path():
    path = Path(__file__).resolve().parents[3] / ".github/workflows/simplification-real-ai.yml"
    workflow = path.read_text()
    assert workflow.count("${{ secrets.OFFERPILOT_SIMPLIFICATION_DEEPSEEK_20261021 }}") == 1
    assert "  workflow_dispatch:" not in workflow
    assert "  pull_request:" not in workflow
    assert "  pull_request_target:" not in workflow
    assert ": write" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "continue-on-error" not in workflow
    assert "environment: offerpilot-simplification-ai-20261021" in workflow
    assert workflow.count("github.sha == vars.OFFERPILOT_SIMPLIFICATION_AI_REQUEST_SHA") == 2
    assert "persist-credentials: true" not in workflow
    command = "python -m scripts.simplification_ai_validation.preflight"
    assert f"{command} --phase before" in workflow
    assert f"{command} --phase paid" in workflow
    assert "python scripts/simplification_ai_validation/preflight.py" not in workflow
    assert "artifacts/simplification-ai/summary.json" in workflow
    assert "artifacts/simplification-ai/budget.jsonl" in workflow


def test_main_never_inherits_provider_secret_into_git_or_driver(tmp_path, monkeypatch, admission):
    import subprocess
    import sys
    record, metadata, now = admission
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(record))
    environment = {**metadata, run.KEY_NAME: "synthetic-secret-do-not-inherit",
                   run.BASE_NAME: gate.BASE_URL, run.MODEL_NAME: gate.MODEL,
                   "SSLKEYLOGFILE": "synthetic-untrusted-file", "GH_TOKEN": "synthetic-gh-token"}
    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--admission-file", str(path),
                                     "--evidence-dir", str(tmp_path / "evidence")])
    commands = []

    def git(command, **kwargs):
        commands.append(command)
        assert set(kwargs["env"]) == {"PATH", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"}
        assert "synthetic" not in json.dumps(kwargs["env"])
        return subprocess.CompletedProcess(command, 0,
                                           record["source_sha"].encode() if len(commands) == 1 else b"")

    class Transport:
        def __init__(self, key, price_window):
            self.price_window = price_window
            assert key == "synthetic-secret-do-not-inherit"
            assert "synthetic" not in json.dumps(environment)

    def execute(transport, sha, evidence, price_window):
        assert isinstance(transport, Transport)
        assert transport.price_window is price_window
        assert sha == record["source_sha"]
        assert "synthetic" not in json.dumps(environment)
        return {"matrix": {"passed": True}}

    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "HTTPSUpstream", Transport)
    monkeypatch.setattr(run, "execute", execute)
    assert run.main() == 0
    assert len(commands) == 2


@pytest.mark.parametrize("remaining", [-1, 89], ids=["expired", "under-90-seconds"])
def test_expired_price_refuses_before_provider_key_git_or_transport(
        tmp_path, monkeypatch, capsys, admission, remaining):
    import sys

    record, metadata, _ = admission
    now = gate.PRICE_VALID_UNTIL - remaining
    record["checked_at"] = int(now)
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(record))

    def forbidden(*args, **kwargs):
        pytest.fail("Expired quote must not access the provider key, git or transport")

    class GuardedEnvironment(dict):
        pop = forbidden

        def get(self, key, default=None):
            if key == run.KEY_NAME:
                forbidden()
            return super().get(key, default)

    monkeypatch.setattr(run.os, "environ", GuardedEnvironment(metadata))
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(run.subprocess, "run", forbidden)
    monkeypatch.setattr(run, "HTTPSUpstream", forbidden)
    monkeypatch.setattr(run, "execute", forbidden)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--admission-file", str(path),
                                     "--evidence-dir", str(tmp_path / "evidence")])
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "passed": False, "error_code": "price_review_expired",
    }
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("boundary", ["between_steps", "admission_read", "checkout", "metadata"])
@pytest.mark.parametrize("failure", ["stale", "elapsed", "quote"])
def test_paid_handoff_stall_refuses_before_key_and_execute(
        tmp_path, monkeypatch, capsys, admission, boundary, failure):
    """Use the actual writer, but only fake metadata, clocks, git and key."""
    import sys
    from types import SimpleNamespace
    from scripts.simplification_ai_validation import preflight

    _, metadata, _ = admission
    initial = gate.PRICE_VALID_UNTIL - (1201 if failure == "quote" else 3600)
    wall, mono = [initial], [1000.0]
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    monkeypatch.setattr(gate.time, "monotonic", lambda: mono[0])
    path = tmp_path / "admission.json"
    preflight.write_admission(str(path), preflight.Context("a" * 40, "b" * 40, 123, 77))
    observed = {"key": 0, "execute": 0, "git": 0}

    def stall():
        # Wall rollback hides both expired freshness and an incomplete session.
        mono[0] += 2 if failure == "quote" else 121
        wall[0] += 121 if failure == "elapsed" else 0

    class Environment(dict):
        def pop(self, key, *args):
            if key == run.KEY_NAME:
                observed["key"] += 1
            return super().pop(key, *args)

        def get(self, key, default=None):
            if key == run.MODEL_NAME and boundary == "metadata":
                stall()
            return super().get(key, default)

    environment = Environment({**metadata, run.KEY_NAME: "synthetic-only",
                               run.BASE_NAME: gate.BASE_URL, run.MODEL_NAME: gate.MODEL})
    read = Path.read_bytes

    def delayed_read(target):
        result = read(target)
        if target == path and boundary == "admission_read":
            stall()
        return result

    def git(*args, **kwargs):
        observed["git"] += 1
        if observed["git"] == 1:
            if boundary == "checkout":
                stall()
            return SimpleNamespace(stdout=b"b" * 40)
        return SimpleNamespace(stdout=b"")

    def execute(*args, **kwargs):
        observed["execute"] += 1
        return {"matrix": {"passed": True}}

    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(Path, "read_bytes", delayed_read)
    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--admission-file", str(path),
                                     "--evidence-dir", str(tmp_path / "evidence")])
    if boundary == "between_steps":
        stall()
    assert run.main() == 1
    assert observed["key"] == observed["execute"] == 0
    assert json.loads(capsys.readouterr().out)["error_code"] in {
        "admission_expired", "price_review_expired",
    }
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("damage", [
    "legacy", "bool_schema", "float_schema", "extra", "missing", "other_boot",
    "checked_nan", "checked_inf", "checked_bool", "checked_text", "checked_negative",
    "mono_nan", "mono_bool", "mono_future", "mono_before_anchor", "mono_huge",
    "window_extra", "window_missing", "window_bool", "window_inf", "window_nan",
    "window_negative", "deadline_extended", "deadline_text", "wall_bool", "wall_future",
])
def test_untrusted_handoff_clock_is_rejected(admission, damage):
    record, metadata, _ = admission
    if damage == "legacy":
        record["schema"] = 1
    elif damage == "bool_schema":
        record["schema"] = True
    elif damage == "float_schema":
        record["schema"] = 2.0
    elif damage == "extra":
        record["other"] = 1
    elif damage == "missing":
        del record["checked_monotonic"]
    elif damage == "other_boot":
        record["boot_id"] = "00000000-0000-0000-0000-000000000000"
    elif damage.startswith("checked_"):
        record["checked_at"] = {"nan": float("nan"), "inf": float("inf"), "bool": True,
                                "text": str(record["checked_at"]), "negative": -1}[
                                    damage.removeprefix("checked_")]
    elif damage.startswith("mono_"):
        record["checked_monotonic"] = {"nan": float("nan"), "bool": True,
                                       "future": 1001, "before_anchor": 999,
                                       "huge": 10 ** 1000}[damage.removeprefix("mono_")]
    elif damage == "window_extra":
        record["price_window"]["other"] = 1
    elif damage == "window_missing":
        del record["price_window"]["monotonic"]
    elif damage.startswith("window_"):
        record["price_window"]["monotonic"] = {
            "bool": True, "inf": float("inf"), "nan": float("nan"), "negative": -1,
        }[damage.removeprefix("window_")]
    elif damage == "deadline_extended":
        record["price_window"]["deadline"] += 1
    elif damage == "deadline_text":
        record["price_window"]["deadline"] = str(record["price_window"]["deadline"])
    elif damage == "wall_bool":
        record["price_window"]["wall"] = True
    else:
        record["price_window"]["wall"] += 1
        record["price_window"]["deadline"] -= 1
    with pytest.raises(gate.GateError, match="admission_required|invalid_clock"):
        run.check_admission(record, metadata, gate.PriceWindow.capture())


@pytest.mark.parametrize("boundary", ["admission", "key", "transport", "broker"])
def test_monotonic_rollback_is_never_reanchored(tmp_path, monkeypatch, admission, boundary):
    record, metadata, _ = admission
    entry = gate.PriceWindow.capture()
    _, window = run.check_admission(record, metadata, entry)
    transport = gate.HTTPSUpstream("synthetic-only", window)
    broker = gate.Broker(tmp_path / "budget.jsonl", lambda body: (200, b"{}"), "b" * 40, window)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 999.0)
    try:
        with pytest.raises(gate.GateError, match="invalid_clock"):
            if boundary == "admission":
                run.check_admission(record, metadata, entry)
            elif boundary == "key":
                run.check_freshness(record, window)
            elif boundary == "transport":
                transport._check_window(2000)
            else:
                broker._check_window()
    finally:
        broker.close()


def test_valid_cross_process_handoff_shares_earliest_quote_until_last_boundary(
        tmp_path, monkeypatch, admission):
    record, metadata, now = admission
    # The next process starts 20 seconds later while wall time rolls back by 20.
    mono = [1020.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: mono[0])
    entry = gate.PriceWindow.capture()
    _, window = run.check_admission(record, metadata, entry)
    assert window.deadline == record["price_window"]["deadline"] < entry.deadline
    transport = gate.HTTPSUpstream("synthetic-only", window)
    mono[0] += 10
    broker = gate.Broker(tmp_path / "budget.jsonl", lambda body: (200, b"{}"), "b" * 40, window)
    try:
        assert transport._price_window is broker._price_window is window
        assert transport._price_deadline == broker._price_deadline == 4600
        # All constructors have run, but no network/provider request has occurred.
        mono[0] = window.deadline - gate.REQUEST_SECONDS + 1
        for check in (broker._check_window, lambda: transport._check_window(mono[0] + 90)):
            with pytest.raises(gate.GateError, match="price_review_expired"):
                check()
        assert broker.summary()["request_count"] == 0
        assert record["boot_id"] not in json.dumps(broker.summary())
        assert "boot_id" not in (tmp_path / "budget.jsonl").read_text()
    finally:
        broker.close()
