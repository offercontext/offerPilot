from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PATH = Path(__file__).with_name("run.py")
_SPEC = importlib.util.spec_from_file_location("story_cleanup_diagnostic", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_REPO = _PATH.parents[2]


def test_trace_instrumentation_preserves_every_original_control_flow_line() -> None:
    source = (_REPO / _MODULE.HARNESS_RELATIVE).read_text(encoding="utf-8-sig")
    instrumented = _MODULE.instrument(source)
    start = instrumented.index("\nfunction Write-CleanupDiagnostic(")
    end = instrumented.index("function Stop-Tree(")
    restored = instrumented[:start] + instrumented[end:]
    restored = "\n".join(
        line for line in restored.split("\n")
        if not line.lstrip().startswith("Write-CleanupDiagnostic ")
    )
    assert restored == source
    assert instrumented.count("'finally.enter'") == 1
    assert instrumented.count("'cim.begin'") == 1
    assert instrumented.count("'verify.begin'") == 1


def test_changed_or_duplicate_instrumentation_anchor_is_rejected() -> None:
    with pytest.raises(ValueError, match="one instrumentation anchor"):
        _MODULE.replace_once("anchor anchor", "anchor", "replacement")


def test_synthetic_graph_never_uses_os_process_operations() -> None:
    source = (_REPO / _MODULE.HARNESS_RELATIVE).read_text(encoding="utf-8-sig")
    probe = _MODULE.process_probe(_MODULE.instrument(source), cycle=True)
    for function in ("Get-CimInstance", "Get-Process", "Stop-Process"):
        assert f"function {function} {{" in probe
    assert "Synthetic graph visit limit reached" in probe
    assert "Start-Process" not in probe
    assert "Win32_Process" in probe  # The real Stop-Tree body is retained.


def test_outer_timeout_only_kills_the_created_process_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class Process:
        pid = 76543
        returncode: int | None = None

        def wait(self, timeout: int) -> int:
            if self.returncode is None:
                raise subprocess.TimeoutExpired("owned child", timeout)
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    child = Process()
    cleanup_calls: list[list[str]] = []

    def popen(*_args, **kwargs):
        assert kwargs["stdout"] != subprocess.PIPE
        assert kwargs["stderr"] != subprocess.PIPE
        return child

    def run(command, **kwargs):
        cleanup_calls.append(command)
        assert kwargs["timeout"] == 15
        child.returncode = 1
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_MODULE.subprocess, "Popen", popen)
    monkeypatch.setattr(_MODULE.subprocess, "run", run)
    result = _MODULE.bounded_run(
        ["powershell"], cwd=tmp_path, env={}, evidence=tmp_path, name="probe", timeout=20,
    )
    assert result["timed_out"] is True
    assert cleanup_calls == [["taskkill", "/PID", "76543", "/T", "/F"]]
    assert result["returncode"] == 1


@pytest.mark.parametrize("broken", (False, True))
def test_subprocess_offline_bootstrap_blocks_external_sockets_or_exits_closed(
    tmp_path: Path, broken: bool,
) -> None:
    environment = _MODULE.prepare_offline_environment(_REPO, tmp_path, _REPO / _MODULE.HARNESS_RELATIVE)
    if broken:
        bootstrap = tmp_path / "bootstrap" / "sitecustomize.py"
        source = bootstrap.read_text(encoding="utf-8")
        bootstrap.write_text(source.replace("import tests._offline_network_guard as guard", "raise RuntimeError('forced guard failure')"))
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import socket\n"
            "try:\n"
            "    socket.getaddrinfo('provider.example', 443)\n"
            "except PermissionError:\n"
            "    print('external socket blocked before DNS')\n"
            "else:\n"
            "    raise SystemExit('offline guard was bypassed')\n",
        ],
        cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=20,
    )
    if broken:
        assert result.returncode == 86
        assert "Diagnostic offline guard startup failed" in result.stderr
        assert not (tmp_path / "offline-guard.jsonl").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert "external socket blocked before DNS" in result.stdout
        records = [json.loads(line) for line in (tmp_path / "offline-guard.jsonl").read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["early"] is True
        assert records[0]["ppid"] == os.getpid()


@pytest.mark.parametrize("fault", (None, "collection-error", "assertion-failed", "no-stage", "wrong-case", "extra-case"))
def test_evidence_requires_exact_five_control_cases_to_pass(
    tmp_path: Path, fault: str | None,
) -> None:
    race_names = [node.split("::")[1] for node in _MODULE.RACE_NODE_IDS]
    if fault == "extra-case":
        race_names.append("unapproved_case")
    race_xml = ''.join(f'<testcase name="{name}" />' for name in race_names)
    (tmp_path / "stop-tree-races.xml").write_text(f'<testsuites><testsuite>{race_xml}</testsuite></testsuites>')
    case = "other_case" if fault == "wrong-case" else _MODULE.NODE_ID.split("::")[1]
    failure = '<failure message="sentinel missing" />' if fault == "assertion-failed" else ''
    (tmp_path / "failing-node.xml").write_text(
        f'<testsuites><testsuite tests="1"><testcase name="{case}">{failure}</testcase></testsuite></testsuites>'
    )
    stages = ["harness.begin"] if fault == "no-stage" else ["harness.begin", "chromium.begin", "finally.exit failures=0"]
    (tmp_path / "harness-cleanup.jsonl").write_text(
        "\n".join(json.dumps({"stage": stage}) for stage in stages)
    )
    (tmp_path / "offline-guard.jsonl").write_text(json.dumps({"early": True}))
    results = [{"returncode": 0, "timed_out": False} for _ in range(2)]
    if fault == "collection-error":
        results[-1]["returncode"] = 2
    if fault == "assertion-failed":
        results[-1]["returncode"] = 1
    assert bool(_MODULE.evidence_errors(tmp_path, results)) is (fault is not None)


@pytest.mark.parametrize("fault", (None, "wrong-source", "dirty", "runtime", "renamed-runtime", "extra-test"))
def test_control_source_rejects_changes_outside_exact_reviewed_test_and_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str | None,
) -> None:
    expected = "a" * 40
    paths = [_MODULE.TEST_RELATIVE, "desktop/browser-cleanup-diagnostic/run.py"]
    if fault in ("runtime", "renamed-runtime"):
        paths.append("src/offerpilot/api.py")
    if fault == "extra-test":
        paths.append("tests/test_other.py")
    def git(command, **kwargs):
        assert kwargs["timeout"] == 10
        if command[1] == "rev-parse":
            output = "b" * 40 if fault == "wrong-source" else expected
        elif command[1] == "status":
            output = " M tests/test_interview_story_browser_harness.py" if fault == "dirty" else ""
        else:
            assert command == ["git", "diff", "--no-renames", "--name-only", _MODULE.PRODUCT_SHA, expected, "--"]
            output = "\n".join(paths)
        return subprocess.CompletedProcess(command, 0, stdout=output)
    monkeypatch.setattr(_MODULE.subprocess, "run", git)
    if fault is None:
        assert _MODULE.verify_source(tmp_path, expected) == (expected, paths)
    else:
        with pytest.raises(RuntimeError):
            _MODULE.verify_source(tmp_path, expected)


@pytest.mark.parametrize("extra_case", (False, True))
def test_pytest_plugin_admits_only_its_exact_race_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_case: bool,
) -> None:
    from types import SimpleNamespace
    environment = _MODULE.prepare_offline_environment(_REPO, tmp_path, _REPO / _MODULE.HARNESS_RELATIVE)
    namespace: dict = {}
    exec((tmp_path / "bootstrap/story_cleanup_probe_plugin.py").read_text(), namespace)
    monkeypatch.setenv("STORY_DIAGNOSTIC_MODE", "races")
    monkeypatch.setenv("STORY_DIAGNOSTIC_HARNESS", environment["STORY_DIAGNOSTIC_HARNESS"])
    items = [SimpleNamespace(nodeid=node) for node in _MODULE.RACE_NODE_IDS]
    if extra_case:
        items.append(SimpleNamespace(nodeid="tests/test_other.py::test_unapproved"))
        with pytest.raises(RuntimeError, match="exactly its approved test cases"):
            namespace["pytest_collection_modifyitems"](items)
    else:
        namespace["pytest_collection_modifyitems"](items)
