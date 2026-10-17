"""Full coverage and failure propagation of the real isolated pytest gate."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test_shards/gate.py"
spec = importlib.util.spec_from_file_location("complete_pytest_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_deterministic_complete_balanced_partitions():
    nodes = [f"tests/test_suite_{i % 284}.py::test_{i}[Unicode 空格 {i}]" for i in range(6384)]
    shards = gate.partition(nodes, 12)
    assert shards == gate.partition(list(reversed(nodes)), 12)
    assert sorted(sum(shards, [])) == sorted(nodes)
    assert len(set(sum(shards, []))) == len(nodes)
    assert max(map(len, shards)) - min(map(len, shards)) <= 23


def test_measured_cost_balances_slow_files_instead_of_equal_node_counts(monkeypatch):
    costs = {"tests/test_a.py::test_slow": 100_000, "tests/test_b.py::test_fast": 1_000,
             "tests/test_c.py::test_slow": 90_000, "tests/test_d.py::test_fast": 1_000}
    monkeypatch.setattr(gate, "load_duration_history", lambda: {
        "duration_ms": costs, "fallback_ms": 100_000,
    }, raising=False)
    shards = gate.partition(list(costs), 2)
    assert max(sum(costs[node] for node in shard) for shard in shards) == 100_000
    assert shards == gate.partition(list(reversed(costs)), 2)
    assert sorted(sum(shards, [])) == sorted(costs)


@pytest.mark.parametrize("nodes,count", [([], 1), (["a", "a"], 1), (["a"], 2), (["a"], 0)])
def test_invalid_partition_rejected(nodes, count):
    with pytest.raises(ValueError):
        gate.partition(nodes, count)


def evidence(tmp_path):
    nodes = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    manifest = {"version": 4, "partition": gate.partition_policy(),
                "identity": {"commit": "abc"}, "nodes": nodes,
                "shards": gate.partition(nodes, 2), "allowed_skips": None}
    results = []
    for index, shard in enumerate(manifest["shards"]):
        result = {"identity": manifest["identity"], "index": index, "exit_code": 0,
                  "manifest": gate.digest(manifest), "selected": shard,
                  "reports": {node: [
                      {"when": phase, "outcome": "passed", "reason": "", "xfail": False}
                      for phase in ["setup", "call", "teardown"]] for node in shard}}
        gate.write(tmp_path / f"shard-{index}.json", result)
        results.append(result)
    return manifest, results


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "exit", "failed", "xfail",
                                      "selection", "identity", "digest", "no_call", "extra"])
def test_aggregate_fails_closed(tmp_path, mutation):
    manifest, results = evidence(tmp_path)
    result = copy.deepcopy(results[0])
    node = manifest["nodes"][0]
    phases = result["reports"][node]
    if mutation == "missing":
        del result["reports"][node]
    elif mutation == "duplicate":
        phases.append(phases[1])
    elif mutation == "exit":
        result["exit_code"] = 1
    elif mutation == "failed":
        phases[2]["outcome"] = "failed"
    elif mutation == "xfail":
        phases[1]["xfail"] = True
    elif mutation == "selection":
        result["selected"] = []
    elif mutation == "identity":
        result["identity"] = {"commit": "old"}
    elif mutation == "digest":
        result["manifest"] = "stale"
    elif mutation == "no_call":
        phases.pop(1)
    elif mutation == "extra":
        gate.write(tmp_path / "shard-999.json", result)
    gate.write(tmp_path / "shard-0.json", result)
    with pytest.raises(ValueError):
        gate.aggregate(manifest, tmp_path)


def test_missing_shard_and_manifest_duplicates_fail(tmp_path):
    manifest, _ = evidence(tmp_path)
    (tmp_path / "shard-1.json").unlink()
    with pytest.raises(ValueError):
        gate.aggregate(manifest, tmp_path)
    manifest["nodes"].append(manifest["nodes"][0])
    with pytest.raises(ValueError):
        gate.validate_manifest(manifest)


def test_skips_disclosed_without_counting_as_passed(tmp_path):
    manifest, results = evidence(tmp_path)
    node = manifest["nodes"][0]
    phases = results[0]["reports"][node]
    phases.pop(1)
    phases[0].update(outcome="skipped", reason="Platform unavailable")
    gate.write(tmp_path / "shard-0.json", results[0])
    summary = gate.aggregate(manifest, tmp_path)
    assert summary["skipped"] == [{"node": node, "reason": "Platform unavailable"}]
    assert summary["tests"] == 2
    manifest["allowed_skips"] = {}
    results[0]["manifest"] = gate.digest(manifest)
    with pytest.raises(ValueError, match="unexpected skip"):
        gate.validate_result(manifest, results[0], 0)


@pytest.fixture
def tiny_repo(tmp_path):
    (tmp_path / "uv.lock").write_text("test lock")
    # Every synthetic child pytest also starts offline, without process-global
    # PYTEST_ADDOPTS/PYTEST_PLUGINS that would alter nested selection semantics.
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npythonpath = .\naddopts = -p offline_guard\n")
    shutil.copyfile(ROOT / "tests/_offline_network_guard.py", tmp_path / "offline_guard.py")
    (tmp_path / "tests").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp_path, check=True)
    return tmp_path


def invoke(repo, command, *args):
    result = subprocess.run([sys.executable, str(SCRIPT), command, "--manifest", "manifest.json",
                             "--output", "evidence", *args], cwd=repo, capture_output=True,
                            text=True, timeout=45)
    return result


def test_real_subprocess_complete_and_nested_pytest_unchanged(tiny_repo):
    (tiny_repo / "tests/test_sample.py").write_text('''import os, subprocess, sys
from pathlib import Path
import pytest
@pytest.fixture(scope="module", autouse=True)
def module_setup():
    marker = Path("module-setups.txt")
    marker.write_text(marker.read_text() + "setup\\n" if marker.exists() else "setup\\n")
@pytest.mark.parametrize("value", ["空格 a", "b"])
def test_ok(value):
    assert value

def test_nested(tmp_path):
    (tmp_path / "test_inner.py").write_text("def test_inner(): pass")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "offline_guard", "--collect-only", "-q", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "test_inner.py::test_inner" in result.stdout
    assert "PYTEST_ADDOPTS" not in os.environ
    assert "PYTEST_PLUGINS" not in os.environ

def test_skip():
    pytest.skip("Known platform condition")
''', encoding="utf-8")
    (tiny_repo / "tests/test_other.py").write_text("def test_other(): pass")
    collected = invoke(tiny_repo, "collect", "--count", "2")
    assert collected.returncode == 0, collected.stdout + collected.stderr
    for index in range(2):
        ran = invoke(tiny_repo, "run", "--index", str(index))
        assert ran.returncode == 0, ran.stdout + ran.stderr
        evidence = json.loads((tiny_repo / f"evidence/shard-{index}.json").read_text())
        for phases in evidence["reports"].values():
            for phase in phases:
                assert isinstance(phase["duration_seconds"], (int, float))
                assert phase["duration_seconds"] >= 0
    assert (tiny_repo / "module-setups.txt").read_text().splitlines() == ["setup"]
    result = invoke(tiny_repo, "aggregate")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["skipped"][0]["reason"] == "Known platform condition"
    (tiny_repo / "evidence/shard-1.json").unlink()
    assert invoke(tiny_repo, "aggregate").returncode != 0


@pytest.mark.parametrize("body", [
    "def test_bad(): assert False",
    "import pytest\n@pytest.fixture\ndef bad(): raise RuntimeError('setup')\n"
    "def test_bad(bad): pass",
    "import pytest\n@pytest.fixture\ndef bad():\n yield\n raise RuntimeError('teardown')\n"
    "def test_bad(bad): pass",
    "import pytest\n@pytest.mark.xfail\ndef test_bad(): assert False",
])
def test_real_failed_shard_never_aggregates(tiny_repo, body):
    (tiny_repo / "tests/test_bad.py").write_text(body)
    assert invoke(tiny_repo, "collect", "--count", "1").returncode == 0
    assert invoke(tiny_repo, "run").returncode != 0
    assert invoke(tiny_repo, "aggregate").returncode != 0


def test_collection_failure_removes_stale_manifest(tiny_repo):
    (tiny_repo / "manifest.json").write_text("stale")
    (tiny_repo / "tests/test_bad.py").write_text("raise RuntimeError('collection')")
    assert invoke(tiny_repo, "collect", "--count", "1").returncode != 0
    assert not (tiny_repo / "manifest.json").exists()


def test_workflow_keeps_complete_gate_and_bounded_non_cancelling_shards():
    workflow = (ROOT / ".github/workflows/desktop-windows.yml").read_text()
    assert "max-parallel: 2" in workflow
    assert "fail-fast: false" in workflow
    assert "cancel-in-progress:" not in workflow
    assert "timeout-minutes: 90" in workflow
    assert "needs: [pytest-manifest, pytest-shards]" in workflow
    assert "-Install -PytestEvidence pytest-evidence" in workflow
    assert "  pull_request:\n    branches: [master]" in workflow
    assert "paths-ignore:" not in workflow
    release = (ROOT / "scripts/release-gate.ps1").read_text()
    assert release.index("gate.py aggregate") < release.index("uv run ruff check .")
    assert "Full pytest evidence verification failed" in release
    for command in ["uv run pytest -q", "uv run mypy src", "npm.cmd test",
                    "npm.cmd run build", "local-smoke.ps1", "oc verify --profile local",
                    "install-gate.ps1"]:
        assert command in release


def test_untracked_same_node_source_change_rejects_stale_manifest(tiny_repo):
    test_file = tiny_repo / "tests/test_sample.py"
    test_file.write_text("def test_same_node(): pass")
    assert invoke(tiny_repo, "collect", "--count", "1").returncode == 0
    test_file.write_text("def test_same_node(): assert False")
    result = invoke(tiny_repo, "run")
    assert result.returncode != 0
    assert "another source/environment/run" in result.stderr


def test_collection_skips_are_disclosed_and_drift_rejected(tiny_repo):
    (tiny_repo / "tests/test_ok.py").write_text("def test_ok(): pass")
    (tiny_repo / "tests/test_skip.py").write_text(
        "import pytest\npytest.skip('Optional platform', allow_module_level=True)")
    assert invoke(tiny_repo, "collect", "--count", "1").returncode == 0
    assert invoke(tiny_repo, "run").returncode == 0
    result = invoke(tiny_repo, "aggregate")
    assert result.returncode == 0, result.stderr
    skipped = json.loads(result.stdout)["collection_skips"]
    assert len(skipped) == 1
    assert "Optional platform" in skipped[0]["reason"]


def test_partition_preserves_module_fixture_scope_and_handles_large_files():
    nodes = [f"tests/test_big.py::test_{i}" for i in range(100)]
    nodes += ["tests/test_small.py::test_one", "tests/test_other.py::test_one"]
    shards = gate.partition(nodes, 2)
    assert shards == gate.partition(nodes[::-1], 2)
    assert sorted(sum(shards, [])) == sorted(nodes)
    for filename in {node.split("::")[0] for node in nodes}:
        assert sum(any(node.split("::")[0] == filename for node in shard)
                   for shard in shards) == 1
    assert sorted(map(len, shards)) == [2, 100]
    with pytest.raises(ValueError, match="preserve fixture scope"):
        gate.partition(nodes, 4)


def test_reviewed_chat_module_spreads_without_splitting_other_modules():
    chat = [f"tests/test_chat_api.py::test_case[{i}]" for i in range(390)]
    other = [f"tests/test_other_{i % 24}.py::test_case_{i}" for i in range(6039)]
    nodes = chat + other
    shards = gate.partition(nodes, 12)
    assert gate.NODE_SPLIT_FILES == {"tests/test_chat_api.py"}
    assert shards == gate.partition(nodes[::-1], 12)
    assert sorted(sum(shards, [])) == sorted(nodes)
    assert len(set(sum(shards, []))) == len(nodes)
    chat_counts = [sum(node in set(chat) for node in shard) for shard in shards]
    assert min(chat_counts) > 0
    costs = gate.estimated_costs(nodes, gate.load_duration_history())
    loads = [sum(costs[node] for node in shard) for shard in shards]
    assert max(loads) - min(loads) <= max(costs[node] for node in chat)
    for filename in {node.split("::")[0] for node in other}:
        assert sum(any(node.split("::")[0] == filename for node in shard)
                   for shard in shards) == 1


def test_node_split_source_change_requires_a_new_isolation_review():
    # This is an audit gate, not runtime caching. Review globals, dynamic fixture
    # acquisition and fixture scopes again before updating these digests.
    # Re-reviewed after 0256ba70: ProviderGapClock, models and diagnostic lists
    # belong to each test; only its provider worker advances that clock, before
    # the completed HTTP/SSE response is inspected. Database/key paths are local.
    # Existing catalog/golden globals are read-only; the legacy spy resets its
    # slots per invocation and exposes its callback only via function monkeypatch.
    # Neither module has xunit/shared fixture state or dynamic fixture requests;
    # conftest's app_client remains function-scoped. Keep the runtime scope guards.
    # Re-reviewed timeout probes: each call owns its Events, real Future callback
    # and executor subclass; function monkeypatch restores the transport after
    # the changed tests release and observe their Agent worker's completion.
    # The remaining confirmation probes now share that real-Future helper;
    # retries restore only the executor after the timed-out worker has exited.
    # Re-reviewed short provider waits: perf_counter adds no shared state. The
    # coarse-clock regression owns its clock/iterator/list and function monkeypatch
    # restores only this module's time binding; process-wide time is untouched.
    # Independently re-reviewed CAS/timeout probes: all counters are per-test
    # closures; function monkeypatch restores the model, delivery and control
    # methods. The provider exits via Future.result before timeout recovery;
    # the busy SQLite writer is closed after finally rolling back its lock.
    # Include the shared helper, since its state is part of chat node isolation.
    # Normalize only checkout CRLF so the same reviewed source works on Windows.
    reviewed = {
        "tests/test_chat_api.py":
            "ad0021f743be5322e9ab5ea1fc4aae288411e572875f19171c806153cdf46fc9",
        "tests/_agent_timeout.py":
            "5bb1054018aeb6153bd6f0a0bb8e5fe3ace2203df1e8fa375c43d3dac9edd735",
        "tests/conftest.py":
            "3e37887773fd017242ddc6ecc514218c381da2bbb66f2379e97c3f2153e8c675",
    }
    for name, expected in reviewed.items():
        source = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(source).hexdigest() == expected, (
            f"{name}: review node-split state/fixture isolation before updating the digest"
        )


def test_real_reviewed_split_keeps_function_state_and_exact_execution(tiny_repo):
    (tiny_repo / "tests/test_chat_api.py").write_text('''import pytest
@pytest.mark.parametrize("value", range(4))
def test_isolated(tmp_path, value):
    marker = tmp_path / "isolated.txt"
    assert not marker.exists()
    marker.write_text(str(value))
''')
    assert invoke(tiny_repo, "collect", "--count", "2").returncode == 0
    manifest = json.loads((tiny_repo / "manifest.json").read_text())
    assert manifest["version"] == 4
    assert manifest["partition"] == gate.partition_policy()
    assert list(map(len, manifest["shards"])) == [2, 2]
    for index in range(2):
        ran = invoke(tiny_repo, "run", "--index", str(index))
        assert ran.returncode == 0, ran.stdout + ran.stderr
    summary = invoke(tiny_repo, "aggregate")
    assert summary.returncode == 0, summary.stderr
    assert json.loads(summary.stdout)["passed"] == 4


@pytest.mark.parametrize("scope", ["module", "class", "package", "session"])
def test_new_custom_shared_fixture_prevents_reviewed_node_split(tiny_repo, scope):
    (tiny_repo / "tests/conftest.py").write_text(
        f"import pytest\n@pytest.fixture(scope={scope!r})\ndef shared(): return 1\n")
    (tiny_repo / "tests/test_chat_api.py").write_text(
        "def test_one(shared): assert shared\ndef test_two(shared): assert shared\n")
    result = invoke(tiny_repo, "collect", "--count", "2")
    assert result.returncode != 0
    assert "forbids node splitting" in result.stdout + result.stderr
    assert not (tiny_repo / "manifest.json").exists()


def test_xunit_module_setup_prevents_reviewed_node_split(tiny_repo):
    (tiny_repo / "tests/test_chat_api.py").write_text(
        "def setup_module(): pass\ndef test_one(): pass\ndef test_two(): pass\n")
    result = invoke(tiny_repo, "collect", "--count", "2")
    assert result.returncode != 0
    assert "forbids node splitting" in result.stdout + result.stderr


def test_indirect_parametrization_cannot_broaden_split_fixture_scope(tiny_repo):
    (tiny_repo / "tests/test_chat_api.py").write_text('''import pytest
@pytest.fixture
def shared(request): return request.param
@pytest.mark.parametrize("shared", [1, 2], indirect=True, scope="module")
def test_one(shared): assert shared
''')
    result = invoke(tiny_repo, "collect", "--count", "2")
    assert result.returncode != 0
    assert "shared parameter shared forbids node splitting" in result.stdout + result.stderr


def test_direct_shared_parameter_scope_prevents_node_split(tiny_repo):
    (tiny_repo / "tests/test_chat_api.py").write_text('''import pytest
@pytest.mark.parametrize("shared", [1, 2], scope="module")
def test_one(shared): assert shared
''')
    result = invoke(tiny_repo, "collect", "--count", "2")
    assert result.returncode != 0
    assert "shared parameter shared forbids node splitting" in result.stdout + result.stderr


@pytest.mark.parametrize("scope", ["module", "session"])
def test_dynamic_or_cached_shared_fixture_prevents_split_manifest(tiny_repo, scope):
    (tiny_repo / "tests/conftest.py").write_text(
        f"import pytest\n@pytest.fixture(scope={scope!r})\ndef shared(): return 1\n")
    (tiny_repo / "tests/test_chat_api.py").write_text(
        "def test_one(request): assert request.getfixturevalue('shared')\n"
        "def test_two(request): assert request.getfixturevalue('shared')\n")
    # An unsplit file could initialize a session fixture before a dynamic chat
    # request; reject its applicability before any cache can be populated.
    (tiny_repo / "tests/test_aaa.py").write_text("def test_prime(shared): assert shared\n")
    result = invoke(tiny_repo, "collect", "--count", "2")
    assert result.returncode != 0
    assert "shared fixture shared forbids node splitting" in result.stdout + result.stderr
    assert not (tiny_repo / "manifest.json").exists()


def test_previous_whole_file_manifest_cannot_use_new_partition_rule(tmp_path):
    manifest, _ = evidence(tmp_path)
    manifest["version"] = 2
    with pytest.raises(ValueError, match="Unsupported manifest"):
        gate.validate_manifest(manifest)


def test_previous_count_balanced_manifest_cannot_use_duration_policy(tmp_path):
    manifest, _ = evidence(tmp_path)
    manifest["version"] = 3
    with pytest.raises(ValueError, match="Unsupported manifest"):
        gate.validate_manifest(manifest)


def test_frozen_history_provenance_and_all_6773_nodes_keep_exact_coverage():
    history = gate.load_duration_history()
    assert {key: value for key, value in history["source"]["baseline"].items()
            if key != "history_sha256"} == {
        "run_id": 38500952016, "artifact_id": 11881026619,
        "artifact_sha256": "7c84e85cf0b7106a5d0a452ea50ca79a49295bb6a2cb601f68d80d1627222826",
        "commit": "9c245314e570f1d6de034e4d52deb46979874416",
        "manifest_sha256": "4718beecb6018522d28719526e61778a1f2a1aa68326c4880f3aca0e89f8349d",
        "nodes_sha256": "8fec4f9f84fdaa5ec102cdab343b8efd97ab3fcff58409e03fb6fd8f6aadc01d",
        "lock_sha256": "456ac277ffbb355ca96fc68b54d05b104f42fd96b5a0b258decab35ff30456f8",
        "platform": "Windows", "python": "3.12.10",
    }
    assert history["source"]["baseline"]["history_sha256"] == (
        "f1be4a8e1d53de9aab21b8a9a3fa23276f4326c56788a4350e23d772c1132cbe"
    )
    refresh = history["source"]["refresh"]
    assert refresh["policy"] == "max-old-and-complete-passed-total-phases-v1"
    assert refresh["run_id"] == 38922100985 and refresh["run_attempt"] == 1
    assert refresh["commit"] == "9c1a5909f3a1cc3790a85bf02dde7072f9cccd3a"
    assert refresh["tree"] == "bd1c54e887c4dcc6fe1f7ccc2eb4005032b6153b"
    assert refresh["lock_sha256"] == history["source"]["baseline"]["lock_sha256"]
    assert (refresh["platform"], refresh["python"]) == ("Windows", "3.12.10")
    assert refresh["manifest_nodes"] == 7127
    assert refresh["manifest_artifact_id"] == 12054832212
    assert refresh["manifest_artifact_sha256"] == (
        "6975c982338ee9342339bcfe18272dad5c8e382ca258598118cd1178418149ee"
    )
    sources = refresh["result_artifacts"]
    assert [(source["shard"], source["artifact_id"], source["complete_passed_nodes"])
            for source in sources] == [
        (0, 12055067112, 80), (1, 12055592757, 56), (2, 12055148733, 45),
        (3, 12056261114, 39), (4, 12059455916, 734),
    ]
    updates = history["source"]["node_updates"]
    assert (refresh["added_nodes"], refresh["raised_nodes"]) == (241, 145)
    assert len(updates) == 386
    assert sum(update["previous_ms"] is None for update in updates.values()) == 241
    for node, update in updates.items():
        assert update["artifact_id"] in {source["artifact_id"] for source in sources}
        previous = update["previous_ms"]
        assert previous is None or (type(previous) is int and previous >= 1)
        assert type(update["observed_ms"]) is int and update["observed_ms"] >= 1
        assert previous is None or update["observed_ms"] > previous
        assert history["duration_ms"][node] == max(previous or 0, update["observed_ms"])
    # Retain the original 6,773-node baseline and add only complete passed observations.
    assert len(history["duration_ms"]) == 6760 + 241
    assert len(history["historical_skips"]) == 13
    assert history["fallback_ms"] == 257834 == max(history["duration_ms"].values())
    nodes = sorted(history["duration_ms"] | history["historical_skips"])
    assert len(nodes) == 6773 + 241
    shards = gate.partition(nodes, 12)
    assert shards == gate.partition(nodes[::-1], 12)
    assert sorted(sum(shards, [])) == nodes
    assert len(set(sum(shards, []))) == 6773 + 241
    for filename in {node.split("::", 1)[0] for node in nodes} - gate.NODE_SPLIT_FILES:
        assert sum(any(node.split("::", 1)[0] == filename for node in shard)
                   for shard in shards) == 1
    manifest = {"version": 4, "partition": gate.partition_policy(), "nodes": nodes,
                "shards": shards, "allowed_skips": None}
    gate.validate_manifest(manifest)
    repeated = {**manifest, "shards": gate.partition(nodes[::-1], 12)}
    assert gate.digest(manifest) == gate.digest(repeated)

    collection = refresh["collection"]
    known = set(nodes)
    extra = collection["not_in_history"]
    absent = collection["history_not_collected"]
    assert len(extra) == len(set(extra)) and not set(extra) & known
    assert len(absent) == len(set(absent)) and set(absent) <= known
    actual = sorted((known - set(absent)) | set(extra))
    assert len(actual) == 7127
    assert gate.digest(actual) == collection["nodes_sha256"] == (
        "ea0873e835206e2cf6af845a139e78bdf4cd0a337e45d2998c4cc9b4d4d90d25"
    )
    predicted = gate.partition(actual, 12)
    assert predicted == gate.partition(actual[::-1], 12)
    assert sorted(sum(predicted, [])) == actual
    assert len(set(sum(predicted, []))) == 7127
    for filename in {node.split("::", 1)[0] for node in actual} - gate.NODE_SPLIT_FILES:
        assert sum(any(node.split("::", 1)[0] == filename for node in shard)
                   for shard in predicted) == 1
    costs = gate.estimated_costs(actual, history)
    loads = [sum(costs[node] for node in shard) for shard in predicted]
    assert list(map(len, predicted)) == refresh["prediction"]["node_counts"]
    assert loads == refresh["prediction"]["phase_ms"]
    assert max(loads) == 5_316_970 and max(loads) - min(loads) == 258
    assert refresh["prediction"]["scope"] == "scheduling-only; not completed execution evidence"

    # The historical twelve-shard prediction above stays immutable. The current
    # twenty-shard layout must preserve the same exact collection and fixtures.
    current = gate.partition(actual, 20)
    assert len(current) == 20 and all(current)
    assert current == gate.partition(actual[::-1], 20)
    assert sorted(sum(current, [])) == actual
    assert len(set(sum(current, []))) == len(actual)
    for filename in {node.split("::", 1)[0] for node in actual} - gate.NODE_SPLIT_FILES:
        assert sum(any(node.split("::", 1)[0] == filename for node in shard)
                   for shard in current) == 1
    current_manifest = {**manifest, "nodes": actual, "shards": current}
    gate.validate_manifest(current_manifest)
    assert gate.digest(current_manifest) == gate.digest({
        **current_manifest, "shards": gate.partition(actual[::-1], 20),
    })


def test_unknown_and_historically_skipped_nodes_use_conservative_frozen_cost(monkeypatch):
    costs = {"tests/test_a.py::test_known": 1, "tests/test_b.py::test_known": 1000}
    unknown = "tests/test_a.py::test_new"
    skipped = "tests/test_skipped.py::test_platform"
    history = {"duration_ms": costs, "historical_skips": {skipped: "Other platform"},
               "fallback_ms": 1000}
    monkeypatch.setattr(gate, "load_duration_history", lambda: history)
    shards = gate.partition([*costs, unknown, skipped], 2)
    assert shards[0] == sorted([*costs, unknown])
    assert shards[1] == [skipped]
    assert history["duration_ms"] == costs  # No learned state or runtime timing feedback.


@pytest.mark.parametrize("filename,skipped,expected", [
    ("tests/test_known.py", False, 20),  # New node in a measured file.
    ("tests/test_known.py", True, 20),  # Previously skipped body was never measured.
    ("tests/test_new.py", False, 1000),  # Entirely new file.
    ("tests/test_all_skipped.py", True, 1000),  # No complete passed body in this file.
])
def test_missing_cost_uses_file_passed_max_then_global_max(filename, skipped, expected):
    node = f"{filename}::test_missing"
    history = {
        "duration_ms": {"tests/test_known.py::test_fast": 1,
                        "tests/test_known.py::test_slow": 20,
                        "tests/test_other.py::test_slow": 1000},
        "historical_skips": {node: "Other platform"} if skipped else {},
        "fallback_ms": 1000,
    }
    before = copy.deepcopy(history)
    # Historical siblings need not be selected to supply the conservative max.
    assert gate.estimated_costs([node], history) == {node: expected}
    assert history == before


def test_equal_cost_file_and_shard_ties_are_stable(monkeypatch):
    nodes = [f"tests/test_{name}.py::test_one" for name in "dcba"]
    history = {"duration_ms": dict.fromkeys(nodes, 10), "fallback_ms": 10}
    monkeypatch.setattr(gate, "load_duration_history", lambda: history)
    expected = [[nodes[3], nodes[1]], [nodes[2], nodes[0]]]
    assert gate.partition(nodes, 2) == expected
    assert gate.partition(nodes[::-1], 2) == expected


@pytest.mark.parametrize("field,value", [
    ("strategy", "count-v3"), ("history_version", 2), ("history_sha256", "0" * 64),
    ("fallback_ms", 0), ("fallback_strategy", "global-max-only"),
])
def test_manifest_duration_policy_is_bound_and_cannot_be_overridden(tmp_path, field, value):
    manifest, _ = evidence(tmp_path)
    manifest["partition"][field] = value
    with pytest.raises(ValueError, match="duration policy"):
        gate.validate_manifest(manifest)


def test_manifest_without_policy_or_with_a_different_assignment_fails_closed(tmp_path):
    manifest, _ = evidence(tmp_path)
    policy = manifest.pop("partition")
    with pytest.raises(ValueError, match="duration policy"):
        gate.validate_manifest(manifest)
    manifest["partition"] = policy
    manifest["shards"].reverse()
    with pytest.raises(ValueError, match="partition"):
        gate.validate_manifest(manifest)


def test_frozen_history_digest_rejects_edits_but_allows_checkout_crlf(tmp_path, monkeypatch):
    original = gate.DURATION_HISTORY_PATH.read_bytes().replace(b"\r\n", b"\n")
    path = tmp_path / "history.json"
    monkeypatch.setattr(gate, "DURATION_HISTORY_PATH", path)
    path.write_bytes(original.replace(b"\n", b"\r\n"))
    assert gate.load_duration_history()["fallback_ms"] == 257834
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="digest mismatch"):
        gate.load_duration_history()
    path.unlink()
    with pytest.raises(FileNotFoundError):
        gate.load_duration_history()


@pytest.mark.parametrize("bad_cost", [0, -1, True, 1.5, float("nan"), "10"])
def test_invalid_frozen_duration_cost_cannot_silently_fall_back(tmp_path, monkeypatch, bad_cost):
    history = gate.load_duration_history()
    history["duration_ms"][next(iter(history["duration_ms"]))] = bad_cost
    raw = json.dumps(history).encode()
    path = tmp_path / "history.json"
    path.write_bytes(raw)
    monkeypatch.setattr(gate, "DURATION_HISTORY_PATH", path)
    monkeypatch.setattr(gate, "DURATION_HISTORY_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="Invalid duration history"):
        gate.load_duration_history()


def test_workflow_preserves_120_minute_shard_budget():
    workflow = (ROOT / ".github/workflows/desktop-windows.yml").read_text()
    shard_job = workflow.split("  pytest-shards:\n", 1)[1].split("  full-regression:\n", 1)[0]
    assert "timeout-minutes: 120" in shard_job
    assert "max-parallel: 2" in shard_job
    assert "shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]" in shard_job
