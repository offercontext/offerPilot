"""Portable contracts for the explicitly Linux-only paid-validation helper.

The 287 Linux helper cases live in its mandatory companion gate, not in the
Windows product manifest. These tests run unchanged on Windows and Linux.
"""
import copy
from pathlib import Path
import socket

import pytest

from scripts.simplification_ai_validation import broker, offline, preflight, run


@pytest.mark.parametrize("platform", ["win32", "darwin", "freebsd13"])
@pytest.mark.parametrize("entrypoint", ["broker", "transport", "execute", "main", "offline"])
def test_unsupported_platform_refuses_before_files_network_or_secret(
        monkeypatch, capsys, platform, entrypoint):
    def forbidden(*args, **kwargs):
        raise AssertionError("side_effect_before_platform_rejection")

    class UnreadableEnvironment(dict):
        get = pop = __getitem__ = __contains__ = forbidden

    class UnreadableKey:
        __bool__ = forbidden

    path = Path("must-not-be-created-by-platform-test")
    with monkeypatch.context() as patch:
        patch.setattr(broker.sys, "platform", platform)
        patch.setattr(broker.os, "environ", UnreadableEnvironment())
        patch.setattr(broker.os, "open", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(run.subprocess, "run", forbidden)
        if entrypoint == "main":
            assert run.main() == 1
        else:
            with pytest.raises(broker.GateError, match="^linux_only_helper$"):
                if entrypoint == "broker":
                    broker.Broker(path, forbidden, "a" * 40)
                elif entrypoint == "transport":
                    broker.HTTPSUpstream(UnreadableKey())
                elif entrypoint == "offline":
                    with pytest.raises(broker.GateError, match="^linux_only_helper$"):
                        preflight.preflight(phase="paid", env={}, event={}, read=forbidden)
                    with pytest.raises(broker.GateError, match="^linux_only_helper$"):
                        preflight.write_admission(str(path), preflight.Context("", "", 1, 1))
                    assert preflight.main(["--phase", "paid"]) == 1
                    assert '"error_code": "linux_only_helper"' in capsys.readouterr().out
                    offline.main()
                else:
                    run.execute(forbidden, "a" * 40, path)
    if entrypoint == "main":
        assert '"error_code": "linux_only_helper"' in capsys.readouterr().out


def test_linux_manifest_preserves_all_203_cases_outside_product_testpaths():
    from scripts.test_shards.gate import validate_node_id

    nodes = offline.expected_nodes()
    # All 244 existing nodes plus 43 same-boot paid-handoff clock regressions.
    assert len(nodes) == 287
    for node in nodes:
        validate_node_id(node)
    assert {node.split("::")[0].rsplit("/", 1)[-1] for node in nodes} == {
        "test_simplification_ai_broker.py", "test_simplification_ai_driver.py",
        "test_simplification_ai_preflight.py", "test_simplification_ai_runner.py",
    }
    root = Path(__file__).resolve().parents[1]
    assert 'testpaths = ["tests"]' in (root / "pyproject.toml").read_text(encoding="utf-8")
    for node in nodes:
        assert not (root / "tests" / node.split("::")[0].rsplit("/", 1)[-1]).exists()


@pytest.mark.parametrize("damage", [
    "none", "missing_node", "extra_node", "duplicate_node", "failed", "skipped",
    "xfail", "missing_phase", "duplicate_phase", "missing_report", "extra_report",
    "collect_error", "deselected", "exit_nonzero",
])
def test_linux_aggregate_requires_exact_passed_body_and_all_phases(damage):
    expected = ["test_a", "test_b"]
    collected = expected.copy()
    reports = {node: [{"when": phase, "outcome": "passed", "xfail": False}
                      for phase in ("setup", "call", "teardown")] for node in expected}
    if damage == "missing_node":
        collected.pop()
    elif damage == "extra_node":
        collected.append("test_c")
    elif damage == "duplicate_node":
        collected.append(collected[0])
    elif damage in {"failed", "skipped"}:
        reports["test_a"][1]["outcome"] = damage
    elif damage == "xfail":
        reports["test_a"][1]["xfail"] = True
    elif damage == "missing_phase":
        reports["test_a"].pop()
    elif damage == "duplicate_phase":
        reports["test_a"].append(copy.copy(reports["test_a"][0]))
    elif damage == "missing_report":
        reports.pop("test_b")
    elif damage == "extra_report":
        reports["test_c"] = []
    assert offline.aggregate(expected, collected, reports,
                             1 if damage == "exit_nonzero" else 0,
                             damage == "collect_error", damage == "deselected") is (damage == "none")


def test_both_workflows_require_identical_exact_linux_safety_command():
    root = Path(__file__).resolve().parents[1]
    paid = (root / ".github/workflows/simplification-real-ai.yml").read_text(encoding="utf-8")
    desktop = (root / ".github/workflows/desktop-windows.yml").read_text(encoding="utf-8")
    command = ("uv run --frozen python -m scripts.simplification_ai_validation.offline "
               "--output artifacts/simplification-offline/results.json")
    paid_offline = paid.split("  offline:\n", 1)[1].split("  preflight:\n", 1)[0]
    helper = desktop.split("  simplification-helper-safety:\n", 1)[1].split(
        "  pytest-manifest:\n", 1)[0]
    full = desktop.split("  full-regression:\n", 1)[1].split("  validation-status:\n", 1)[0]
    assert command in paid_offline and command in helper
    assert "runs-on: ubuntu-latest" in paid_offline and "runs-on: ubuntu-latest" in helper
    assert "secrets." not in helper and "secrets." not in paid_offline
    assert "shell: bash" in helper
    assert "needs: [offline, preflight]" in paid
    assert "needs: [pr-feedback, pytest-manifest, pytest-shards, simplification-helper-safety]" in full
    assert "HELPER_SAFETY_RESULT: ${{ needs.simplification-helper-safety.result }}" in full
    assert "if ($env:HELPER_SAFETY_RESULT -ne 'success')" in full
    assert full.index("if ($env:HELPER_SAFETY_RESULT") < full.index("scripts/release-gate.ps1")
    assert "Linux helper safety must succeed" in full


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "skip", "deselected"])
def test_exact_collection_mismatch_refuses_before_test_bodies(damage):
    from types import SimpleNamespace
    evidence = offline.Evidence(["test_a"])
    nodes = {"missing": [], "extra": ["test_a", "test_b"],
             "duplicate": ["test_a", "test_a"]}.get(damage, ["test_a"])
    if damage == "skip":
        evidence.pytest_collectreport(SimpleNamespace(failed=False, skipped=True))
    if damage == "deselected":
        evidence.pytest_deselected(["test_b"])
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes])
    with pytest.raises(pytest.UsageError, match=f"offline_collection_not_exact_{offline.EXPECTED_COUNT}"):
        evidence.pytest_collection_finish(session)
