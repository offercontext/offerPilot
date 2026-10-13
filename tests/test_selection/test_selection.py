from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import os
import time

import pytest

from scripts.test_selection.core import (
    SelectionError, changed_paths, impact, load_mapping, plan, select, snapshot, validate_safety,
)
from scripts.test_selection.execution import check_plan, execute, reconcile_frontend, cli_command
from scripts.test_selection.core import reconcile_backend

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("mutation", ["missing", "downgraded", "unclassified", "duplicate", "none"])
def test_fixed_safety_baseline_rejects_missing_or_downgraded_identity(tmp_path, monkeypatch, mutation):
    from scripts.test_selection import core
    (tmp_path / "tests").mkdir()
    baseline = dict(schema_version=1, baseline_id="selection-safety-v1", tests=[dict(id="critical", reason="auth/HITL")])
    if mutation == "duplicate":
        baseline["tests"] *= 2
    monkeypatch.setattr(core, "SAFETY_BASELINE_HASH", core.digest(baseline))
    (tmp_path / "tests/selection-safety-v1.json").write_text(json.dumps(baseline), encoding="utf-8")
    catalog = [] if mutation == "missing" else [dict(id="critical", priority="p2" if mutation == "downgraded" else "p0", modules=[] if mutation == "unclassified" else ["authority"])]
    mapping = dict(schema_version=2, safety_baseline="tests/selection-safety-v1.json")
    if mutation == "none":
        assert validate_safety(tmp_path, mapping, catalog)["required_ids"] == ["critical"]
    else:
        with pytest.raises(SelectionError):
            validate_safety(tmp_path, mapping, catalog)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_fixed_safety_identity_cannot_be_removed_from_same_baseline_version(tmp_path, version):
    (tmp_path / "tests").mkdir()
    relative = f"tests/selection-safety-{version}.json"
    baseline = json.loads((ROOT / relative).read_bytes())
    catalog = [dict(id=r["id"], priority="p0", modules=["authority"]) for r in baseline["tests"]]
    baseline["tests"] = baseline["tests"][1:]
    (tmp_path / relative).write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(SelectionError, match="fixed safety baseline changed"):
        validate_safety(tmp_path, dict(schema_version=2, safety_baseline=relative), catalog)


def test_latest_public_safety_baseline_preserves_v1_and_adds_status_races():
    from scripts.test_selection import core
    previous = json.loads((ROOT / "tests/selection-safety-v1.json").read_bytes())
    latest = json.loads((ROOT / "tests/selection-safety-v2.json").read_bytes())
    assert core.digest(previous) == core.SAFETY_BASELINE_HASH
    assert all(entry in latest["tests"] for entry in previous["tests"])
    assert {entry["id"] for entry in latest["tests"]} - {entry["id"] for entry in previous["tests"]} == {
        "tests/test_managed_runtime_api.py::test_runtime_status_rereads_durable_terminal_after_manager_transition",
        "tests/test_managed_runtime_api.py::test_runtime_status_rechecks_source_visibility_after_manager_read",
    }
    mapping = load_mapping(ROOT)
    assert mapping["safety_baseline"] == "tests/selection-safety-v2.json"
    catalog = [dict(id=entry["id"], priority="p0", modules=["authority"]) for entry in latest["tests"]]
    assert validate_safety(ROOT, mapping, catalog)["baseline_id"] == "selection-safety-v2"


def test_offerpilot_mapping_cannot_downgrade_latest_safety_baseline(tmp_path):
    mapping = json.loads((ROOT / "tests/selection-map.json").read_bytes())
    mapping["safety_baseline"] = "tests/selection-safety-v1.json"
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/offerpilot").mkdir(parents=True)
    (tmp_path / "tests/selection-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(SelectionError, match="unregistered safety baseline"):
        load_mapping(tmp_path)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def miniature(root: Path) -> dict:
    (root / "tests").mkdir()
    (root / "src").mkdir()
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nartifacts/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["p0", "p1", "p2"]\n', encoding="utf-8")
    (root / "tests/conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT)!r})\npytest_plugins=['scripts.test_selection.pytest_plugin']\n", encoding="utf-8")
    (root / "src/widget.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tests/test_widget.py").write_text("def test_value():\n    assert 1 == 1\n", encoding="utf-8")
    mapping = {"schema_version": 1, "modules": [
        {"id": "widget", "production": ["src/widget.py"], "tests": ["tests/test_widget.py"],
         "surfaces": ["backend"], "depends_on": []}], "risk_rules": [], "test_metadata": {
        "tests/test_widget.py": {"priority": "p2", "modules": ["widget"], "surface": "backend", "requires": []}}}
    (root / "tests/selection-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    git(root, "init", "-b", "master")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic")
    git(root, "add", ".")
    git(root, "commit", "-m", "test: 初始化选测夹具")
    return mapping


def test_git_paths_staged_unstaged_untracked_rename_and_unknown_base(tmp_path: Path) -> None:
    miniature(tmp_path)
    old = tmp_path / "src/widget.py"
    old.rename(tmp_path / "src/中文 文件.py")
    git(tmp_path, "add", "src")
    (tmp_path / "src/中文 文件.py").write_text("value=2\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("new", encoding="utf-8")
    _, _, changes = changed_paths(tmp_path, "HEAD")
    paths = {p for c in changes for p in c["paths"]}
    assert {"src/widget.py", "src/中文 文件.py", "untracked.txt"} <= paths
    assert {"staged", "worktree", "untracked"} <= {c["source"] for c in changes}
    with pytest.raises(SelectionError):
        changed_paths(tmp_path, "missing-ref")


def test_dependency_closure_cycles_and_p2_direct() -> None:
    modules = [{"id": name, "production": [f"src/{name}.py"], "tests": [],
                "surfaces": ["backend"], "depends_on": dependencies}
               for name, dependencies in (("a", ["c"]), ("b", ["a"]), ("c", ["b"]))]
    affected, reasons, surfaces = impact(["src/a.py"], [{"modules": modules}])
    assert affected == {"a", "b", "c"}
    assert any(r.get("chain") == ["a", "b", "c"] for r in reasons)
    catalog = [{"id": "direct", "file": "tests/a.py", "surface": "backend", "modules": ["a"], "priority": "p2"},
               {"id": "unrelated", "file": "tests/z.py", "surface": "backend", "modules": ["z"], "priority": "p1"}]
    selected, _ = select(catalog, affected, surfaces, reasons, [], "dev")
    assert [s["id"] for s in selected] == ["direct"]


def test_unknown_surface_and_priority_are_conservative() -> None:
    affected, reasons, surfaces = impact(["web/src/new.ts"], [{"modules": []}])
    catalog = [{"id": "front", "file": "web/x.test.ts", "surface": "frontend", "modules": [], "priority": "unknown"},
               {"id": "back", "file": "tests/x.py", "surface": "backend", "modules": ["x"], "priority": "p0"}]
    selected, unknowns = select(catalog, affected, surfaces, reasons, [], "dev")
    assert [t["id"] for t in selected] == ["front"] and unknowns
    selected, _ = select(catalog, affected, surfaces, reasons, [], "merge")
    assert {t["id"] for t in selected} == {"front", "back"}


def test_document_no_changes_and_snapshot_invalidation(tmp_path: Path) -> None:
    miniature(tmp_path)
    first = plan(tmp_path, "HEAD", "dev", tmp_path / "artifacts/test-plans/empty")
    assert first["selection_status"] == "no_changes"
    assert first["selected_tests"] == [] and first["release_eligible"] is False
    (tmp_path / "docs/architecture").mkdir(parents=True)
    (tmp_path / "docs/architecture/a.md").write_text("文档", encoding="utf-8")
    value = plan(tmp_path, "HEAD", "dev", tmp_path / "artifacts/test-plans/docs")
    assert value["selection_status"] == "checks_only"
    check_plan(tmp_path, value)
    git(tmp_path, "add", "docs/architecture/a.md")
    with pytest.raises(SelectionError, match="regenerate"):
        check_plan(tmp_path, value)
    with pytest.raises(SelectionError, match="overwritten"):
        plan(tmp_path, "HEAD", "dev", tmp_path / "artifacts/test-plans/docs")
    before = snapshot(tmp_path)
    (tmp_path / "artifacts/test-plans/docs/extra.log").write_text("log", encoding="utf-8")
    assert snapshot(tmp_path) == before


def test_real_plan_execute_catches_regression(tmp_path: Path) -> None:
    miniature(tmp_path)
    (tmp_path / "tests/test_widget.py").write_text("def test_value():\n    assert 2 == 1\n", encoding="utf-8")
    output = tmp_path / "artifacts/test-plans/regression"
    value = plan(tmp_path, "HEAD", "dev", output)
    assert len(value["selected_tests"]) == 1 and value["selected_tests"][0]["priority"] == "p2"
    report = execute(tmp_path, output / "selection-plan.json")
    assert report["exit_code"] == 1
    assert any(c["check"] == "backend_reconciliation" and c["code"] == 1 for c in report["checks"])


def test_deleted_test_blocks_merge_and_stale_mapping(tmp_path: Path) -> None:
    miniature(tmp_path)
    (tmp_path / "tests/test_widget.py").unlink()
    with pytest.raises(SelectionError, match="stale"):
        load_mapping(tmp_path)


def test_frontend_reconciliation_missing_duplicate_and_skip(tmp_path: Path) -> None:
    file = tmp_path / "web/src/a.test.ts"
    file.parent.mkdir(parents=True)
    file.touch()
    report = {"testResults": [{"name": str(file), "assertionResults": [
        {"fullName": "real assertion", "status": "passed"}]}]}
    assert reconcile_frontend(report, ["web/src/a.test.ts"], tmp_path)[0] == 0
    report["testResults"][0]["assertionResults"].append({"fullName": "real assertion", "status": "pending"})
    assert reconcile_frontend(report, ["web/src/a.test.ts"], tmp_path)[0] == 2


@pytest.mark.parametrize("surface,expand", [("unknown", "full"), ("backend", "none")])
def test_invalid_mapping_cannot_remove_required_execution(tmp_path: Path, surface: str, expand: str) -> None:
    mapping = miniature(tmp_path)
    mapping["test_metadata"]["tests/test_widget.py"]["surface"] = surface
    mapping["risk_rules"] = [{"id": "shared", "paths": ["src/*"], "expand": expand}]
    (tmp_path / "tests/selection-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(SelectionError):
        load_mapping(tmp_path)


def test_backend_reconciliation_failure_dominates_skip_and_missing() -> None:
    report = {"reports": [{"id": "a", "when": "setup", "outcome": "failed"},
                          {"id": "b", "when": "setup", "outcome": "skipped"}]}
    code, details = reconcile_backend(report, ["a", "b", "c"])
    assert code == 1 and details["missing"] == ["a", "b", "c"] and details["skips"] == ["b"]


@pytest.mark.parametrize("scenario,expected", [("missing", 1), ("corrupt", 1), ("cleanup", 1),
                                               ("timeout", 3), ("mutate", 2)])
def test_execute_preserves_failures_and_rejects_incomplete_or_changed_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, expected: int,
) -> None:
    miniature(tmp_path)
    (tmp_path / "src/widget.py").write_text("value=2\n", encoding="utf-8")
    output = tmp_path / "artifacts/test-plans/controlled"
    value = plan(tmp_path, "HEAD", "dev", output)
    # Freeze a focused execution plan; registered optional checks are covered
    # elsewhere and would obscure the process/report failure under test.
    value["required_checks"] = []
    from scripts.test_selection.core import digest
    value["plan_hash"] = digest({k: v for k, v in value.items() if k != "plan_hash"})
    (output / "selection-plan.json").write_text(json.dumps(value), encoding="utf-8")

    class FaultProcess:
        def __init__(self, argv, **kwargs):
            self.report = Path(argv[argv.index("--selection-report") + 1])
        def wait(self, timeout):
            if scenario == "timeout":
                raise subprocess.TimeoutExpired("controlled", timeout)
            if scenario == "corrupt":
                self.report.write_text("{", encoding="utf-8")
            if scenario == "mutate":
                (tmp_path / "src/widget.py").write_text("value=3\n", encoding="utf-8")
                self.report.write_text(json.dumps({"reports": [{"id": value["selected_tests"][0]["id"],
                    "when": "call", "outcome": "passed"}]}), encoding="utf-8")
                return 0
            return 1
        def kill(self):
            raise OSError("kill unavailable")
        def close(self):
            if scenario == "cleanup":
                raise OSError("close unavailable")

    monkeypatch.setattr("scripts.test_selection.execution.ProcessTree", FaultProcess)
    report = execute(tmp_path, output / "selection-plan.json")
    assert report["exit_code"] == expected
    assert (output / "execution/summary.json").is_file()


def test_real_skip_is_not_success(tmp_path: Path) -> None:
    miniature(tmp_path)
    (tmp_path / "tests/test_widget.py").write_text(
        "import pytest\n@pytest.mark.skip(reason='controlled')\ndef test_value():\n    assert True\n", encoding="utf-8")
    output = tmp_path / "artifacts/test-plans/skipped"
    plan(tmp_path, "HEAD", "dev", output)
    report = execute(tmp_path, output / "selection-plan.json")
    assert report["exit_code"] == 2
    assert any(c.get("skips") for c in report["checks"])


@pytest.mark.parametrize("moved_out", [True, False])
def test_test_rename_out_of_catalog_blocks_merge(tmp_path: Path, moved_out: bool) -> None:
    mapping = miniature(tmp_path)
    destination = "docs/widget.txt" if moved_out else "tests/test_renamed.py"
    (tmp_path / destination).parent.mkdir(exist_ok=True)
    (tmp_path / "tests/test_widget.py").rename(tmp_path / destination)
    (tmp_path / "tests/test_anchor.py").write_text("def test_anchor():\n    assert True\n", encoding="utf-8")
    entry = mapping["test_metadata"].pop("tests/test_widget.py")
    mapping["test_metadata"]["tests/test_anchor.py"] = entry
    mapping["modules"][0]["tests"] = ["tests/test_anchor.py"]
    if not moved_out:
        mapping["test_metadata"][destination] = entry
        mapping["modules"][0]["tests"].append(destination)
    (tmp_path / "tests/selection-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    git(tmp_path, "add", ".")
    value = plan(tmp_path, "HEAD", "merge", tmp_path / "artifacts/test-plans/rename")
    assert any(c["status"].startswith("R") for c in value["changes"])
    assert (value["selection_status"] == "blocked") is moved_out
    assert ("tests/test_widget.py" in value["deleted_tests"]) is moved_out


def test_harness_candidate_cannot_point_away_from_selected_root(tmp_path: Path) -> None:
    miniature(tmp_path)
    config = tmp_path / "variants.json"
    config.write_text(json.dumps([{ "variant_id": name, "checkout": str(tmp_path), "interpreter": "unused"}
                                  for name in ("baseline", "candidate")]), encoding="utf-8")
    with pytest.raises(SelectionError, match="distinct"):
        plan(tmp_path, "HEAD", "dev", tmp_path / "artifacts/test-plans/shared", config)


@pytest.mark.parametrize("paths", [[], ["src/typo*"], ["../outside"], [42]])
def test_invalid_risk_paths_are_configuration_errors(tmp_path: Path, paths: list) -> None:
    mapping = miniature(tmp_path)
    mapping["risk_rules"] = [{"id": "shared", "paths": paths, "expand": "full"}]
    (tmp_path / "tests/selection-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(SelectionError):
        load_mapping(tmp_path)


def test_runtime_markdown_and_broken_document_links_do_not_pass(tmp_path: Path) -> None:
    from scripts.test_selection.core import content_only
    from scripts.test_selection.execution import documentation_links
    assert not content_only("docs/runtime/prompt.md")
    assert not content_only("docs/architecture/prompt-template.md")
    (tmp_path / "docs/architecture").mkdir(parents=True)
    (tmp_path / "docs/architecture/a.md").write_text("[坏链接](missing.md)", encoding="utf-8")
    assert documentation_links(tmp_path, ["docs/architecture/a.md"]) == ["docs/architecture/a.md: missing.md"]


def test_owned_process_exits_when_controller_crashes(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "controller.py"
    script.write_text(
        f"import os,sys,time\nsys.path.insert(0,{str(ROOT)!r})\n"
        "from scripts.evaluation_subject.process_tree import ProcessTree\n"
        "tree=ProcessTree([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r},'w').write(str(tree.process.pid))\n"
        "time.sleep(.3)\nos._exit(23)\n", encoding="utf-8")
    assert subprocess.run([sys.executable, str(script)], timeout=10).returncode == 23
    pid = int(pidfile.read_text())
    def alive() -> bool:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            code = wintypes.DWORD()
            try:
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259
            finally:
                kernel.CloseHandle(handle)
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    deadline = time.monotonic() + 5
    while alive() and time.monotonic() < deadline:
        time.sleep(.05)
    assert not alive(), "owned writer survived controller death"


@pytest.mark.parametrize("marker", ["p0", "unknown_priority"])
def test_real_collection_rejects_conflicting_or_unknown_markers(tmp_path: Path, marker: str) -> None:
    miniature(tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text(config.read_text() + 'addopts="--strict-markers"\n', encoding="utf-8")
    (tmp_path / "tests/test_widget.py").write_text(
        f"import pytest\n@pytest.mark.{marker}\ndef test_value():\n    assert True\n", encoding="utf-8")
    with pytest.raises(SelectionError, match="collection failed"):
        plan(tmp_path, "HEAD", "dev", tmp_path / "artifacts/test-plans/marker-error")


def test_registered_cli_really_invokes_validate() -> None:
    result = subprocess.run(cli_command("eval", "validate", "--suite",
        str(ROOT / "evals/suites/harness-p0-v1.json")), cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0 and "3 required deterministic execution items" in result.stdout


@pytest.mark.parametrize("harness_exit", [0, 3])
def test_zero_exit_without_smoke_or_harness_evidence_cannot_pass(tmp_path: Path, monkeypatch, harness_exit) -> None:
    root, baseline = tmp_path / "candidate", tmp_path / "baseline"
    root.mkdir()
    baseline.mkdir()
    miniature(root)
    miniature(baseline)
    ignore = root / ".gitignore"
    ignore.write_text(ignore.read_text() + "web/dist/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "test: 忽略静态资源")
    (root / "web/dist").mkdir(parents=True)
    (root / "web/dist/index.html").write_text("fixture", encoding="utf-8")
    config = tmp_path / "variants.json"
    config.write_text(json.dumps([{"variant_id": name, "checkout": str(path), "interpreter": sys.executable}
                                  for name, path in (("baseline", baseline), ("candidate", root))]), encoding="utf-8")
    output = root / "artifacts/test-plans/noop-cli"
    value = plan(root, "HEAD", "dev", output, config)
    value["required_checks"] = ["harness_p0", "local_smoke"]
    from scripts.test_selection.core import digest
    value["plan_hash"] = digest({k: v for k, v in value.items() if k != "plan_hash"})
    (output / "selection-plan.json").write_text(json.dumps(value), encoding="utf-8")
    class NoopProcess:
        def __init__(self, argv, **kwargs):
            self.is_harness = "eval" in argv
        def wait(self, timeout):
            return harness_exit if self.is_harness else 0
        def close(self):
            pass
    monkeypatch.setattr("scripts.test_selection.execution.ProcessTree", NoopProcess)
    report = execute(root, output / "selection-plan.json")
    assert report["exit_code"] == 3
    assert {c.get("reason") for c in report["checks"]} >= {"missing_harness_report", "smoke_completion_missing"}
