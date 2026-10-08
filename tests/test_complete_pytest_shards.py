"""Full coverage and failure propagation of the real isolated pytest gate."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
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


@pytest.mark.parametrize("nodes,count", [([], 1), (["a", "a"], 1), (["a"], 2), (["a"], 0)])
def test_invalid_partition_rejected(nodes, count):
    with pytest.raises(ValueError):
        gate.partition(nodes, count)


def evidence(tmp_path):
    nodes = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    manifest = {"version": 2, "identity": {"commit": "abc"}, "nodes": nodes,
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
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
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
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", str(tmp_path)], capture_output=True, text=True)
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
    assert "desktop/installed-ui/**" in workflow
    assert "desktop/layout-retry/**" in workflow
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
