"""Bounded, offline Windows control for the Story cleanup diagnostic assertion.

Run the reviewed test/helper parent with runtime files unchanged from the
failing revision. The original node and four existing cleanup race cases run
once each. Instrumentation never edits the product script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET


PRODUCT_SHA = "6781e90070511625697b9df5aea84d916e2c0a5e"
NODE_ID = (
    "tests/test_interview_story_browser_harness.py::"
    "test_story_browser_harness_fails_closed_when_failed_chromium_cleanup_is_uncertain"
)
HARNESS_RELATIVE = "scripts/interview-story-real-ai-browser-harness.ps1"
TEST_RELATIVE = "tests/test_interview_story_browser_harness.py"
RACE_NODE_IDS = [
    f"{TEST_RELATIVE}::test_stop_tree_still_stops_parent_when_child_cleanup_races[{fault}]"
    for fault in ("disappeared", "enumeration", "query", "stubborn")
]
ALLOWED_SOURCE_PATHS = frozenset({
    ".github/workflows/desktop-windows.yml",
    ".github/workflows/desktop-browser-cleanup-diagnostic.yml",
    "desktop/browser-cleanup-diagnostic/run.py",
    "desktop/browser-cleanup-diagnostic/test_diagnostic.py",
    "desktop/browser-cleanup-diagnostic/test-routing.mjs",
    "desktop/browser-cleanup-diagnostic/request.json",
    "desktop/installed-ui/test/routing.test.mjs",
    "desktop/real-ai-validation/test/full-gate-routing.test.mjs",
    TEST_RELATIVE,
})
PYTHON_ROOT_LABELS = frozenset({
    "provider proxy", "isolated service", "forced chromium startup",
    "isolated python story-context-seed", "isolated python forbidden-domain-snapshot",
})


def replace_once(source: str, original: str, replacement: str) -> str:
    if source.count(original) != 1:
        raise ValueError(f"Expected one instrumentation anchor: {original!r}")
    return source.replace(original, replacement, 1)


def instrument(source: str) -> str:
    """Add append-only stage records without changing cleanup decisions."""
    trace = r"""
function Write-CleanupDiagnostic([string]$stage, [int]$targetId = 0, [string]$label = '', [object]$details = $null) {
  try {
    $record = [ordered]@{
      utc = [DateTime]::UtcNow.ToString('o')
      monotonic_ticks = [Diagnostics.Stopwatch]::GetTimestamp()
      monotonic_frequency = [Diagnostics.Stopwatch]::Frequency
      powershell_pid = $PID
      stage = $stage
      target_id = $targetId
      label = $label
      details = $details
    } | ConvertTo-Json -Compress -Depth 5
    [IO.File]::AppendAllText($env:STORY_CLEANUP_TRACE, $record + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  } catch { }
}

"""
    source = replace_once(source, "function Stop-Tree(", trace + "function Stop-Tree(")
    source = replace_once(
        source,
        "  $processId = [int]$process.Id\n",
        "  $processId = [int]$process.Id\n"
        "  Write-CleanupDiagnostic 'tree.enter' $processId $label\n",
    )
    enumeration = (
        "    $children = @(Get-CimInstance Win32_Process -ErrorAction Stop | "
        "Where-Object { $_.ParentProcessId -eq $processId })"
    )
    source = replace_once(
        source,
        enumeration,
        "    Write-CleanupDiagnostic 'cim.begin' $processId $label\n"
        + enumeration
        + "\n    Write-CleanupDiagnostic ('cim.end children=' + (($children | "
        "ForEach-Object { $_.ProcessId }) -join ',')) $processId $label "
        "@($children | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CreationDate)",
    )
    source = replace_once(
        source,
        "  try { Stop-Process -Id $processId -Force -ErrorAction Stop }",
        "  Write-CleanupDiagnostic 'stop.begin' $processId $label\n"
        "  try { Stop-Process -Id $processId -Force -ErrorAction Stop }",
    )
    source = replace_once(
        source,
        "  $deadline = [DateTime]::UtcNow.AddSeconds(15)",
        "  Write-CleanupDiagnostic 'stop.end' $processId $label\n"
        "  $deadline = [DateTime]::UtcNow.AddSeconds(15)",
    )
    source = replace_once(
        source,
        "    try { $running = Get-TrackedProcess $processId }",
        "    Write-CleanupDiagnostic 'verify.begin' $processId $label\n"
        "    try { $running = Get-TrackedProcess $processId }",
    )
    source = replace_once(
        source,
        "    if ($null -eq $running) { break }",
        "    Write-CleanupDiagnostic ('verify.end running=' + ($null -ne $running)) $processId $label\n"
        "    if ($null -eq $running) { break }",
    )
    source = replace_once(
        source,
        '  if ($cleanupFailures.Count -gt 0) { throw "$label cleanup could not be verified:',
        "  Write-CleanupDiagnostic ('tree.exit failures=' + $cleanupFailures.Count) $processId $label\n"
        '  if ($cleanupFailures.Count -gt 0) { throw "$label cleanup could not be verified:',
    )
    source = replace_once(
        source,
        "finally {\n  $cleanupErrors =",
        "finally {\n  Write-CleanupDiagnostic 'finally.enter'\n  $cleanupErrors =",
    )
    source = replace_once(
        source,
        "    try { Stop-Tree $item.Process $item.Label }",
        "    Write-CleanupDiagnostic 'finally.process.begin' ([int]$processId) $item.Label\n"
        "    try { Stop-Tree $item.Process $item.Label }",
    )
    source = replace_once(
        source,
        "    $cleanupProcesses.Add([pscustomobject]@{",
        "    Write-CleanupDiagnostic ('finally.process.end exited=' + $exited) ([int]$processId) $item.Label\n"
        "    $cleanupProcesses.Add([pscustomobject]@{",
    )
    source = replace_once(
        source,
        "  try { Remove-IsolatedTempData }",
        "  Write-CleanupDiagnostic 'temp-data.begin'\n  try { Remove-IsolatedTempData }",
    )
    source = replace_once(
        source,
        "  if (-not [string]::IsNullOrWhiteSpace($CleanupAuditPath)) {",
        "  Write-CleanupDiagnostic 'temp-data.end'\n"
        "  if (-not [string]::IsNullOrWhiteSpace($CleanupAuditPath)) {",
    )
    source = replace_once(
        source,
        "  if ($null -ne $primaryFailure) {",
        "  Write-CleanupDiagnostic ('finally.exit failures=' + $cleanupErrors.Count)\n"
        "  if ($null -ne $primaryFailure) {",
    )
    source = replace_once(
        source, "$primaryFailure = $null\n", "$primaryFailure = $null\nWrite-CleanupDiagnostic 'harness.begin'\n",
    )
    for stage, statement in (
        ("health", '  Wait-ForHttpReady $server "$baseUrl/api/health" \'Isolated service\' | Out-Null'),
        ("seed", "  $seed = Seed-StoryContext"),
        ("baseline", "  $baseline = Get-ForbiddenDomainSnapshot"),
        ("chromium", "  $chromiumHandle = Start-DedicatedChromium $chromium @($port, $proxyPort)"),
    ):
        source = replace_once(
            source, statement,
            f"  Write-CleanupDiagnostic '{stage}.begin'\n{statement}\n"
            f"  Write-CleanupDiagnostic '{stage}.end'",
        )
    for statement, expression, label, executable in (
        (
            "    $process = Start-Process -FilePath $projectPython -WorkingDirectory $repo -ArgumentList @($scriptPath) -PassThru -Wait -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath",
            "$process.Id", "('isolated python ' + $label)", "$projectPython",
        ),
        (
            "  $proxy = Start-Process -FilePath $projectPython -WorkingDirectory $repo -WindowStyle Hidden -PassThru -ArgumentList @('scripts/provider-egress-proxy.py', '--port', $proxyPort, '--audit', $providerAudit, '--expected-endpoints-file', $providerAllowlist)",
            "$proxy.Id", "'provider proxy'", "$projectPython",
        ),
        (
            "  $server = Start-Process -FilePath $projectOc -WorkingDirectory $repo -WindowStyle Hidden -PassThru -ArgumentList @('start', '--port', $port)",
            "$server.Id", "'isolated service'", "$projectOc",
        ),
        (
            "      $script:browserStartupAttempt = $process",
            "$process.Id", "'forced chromium startup'", "$projectPython",
        ),
    ):
        source = replace_once(
            source, statement,
            statement + f"\n  Write-CleanupDiagnostic 'python.root' {expression} {label} @{{ executable = {executable} }}",
        )
    return source


def process_probe(source: str, *, cycle: bool) -> str:
    """Exercise actual PowerShell functions with entirely synthetic OS calls."""
    functions = source[
        source.index("function Write-CleanupDiagnostic("):
        source.index("function Remove-IsolatedTempData")
    ]
    rows = "[pscustomobject]@{ ProcessId = 424202; ParentProcessId = 424201 }\n"
    if cycle:
        rows += "[pscustomobject]@{ ProcessId = 424201; ParentProcessId = 424202 }\n"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$script:alive = @{ 424201 = $true; 424202 = $true }\n"
        "$script:visits = [System.Collections.Generic.List[int]]::new()\n"
        "$script:stopped = [System.Collections.Generic.List[int]]::new()\n"
        "function Get-CimInstance {\n"
        "  $script:visits.Add($processId)\n"
        "  if ($script:visits.Count -gt 8) { throw 'Synthetic graph visit limit reached' }\n"
        + rows
        + "}\n"
        "function Get-Process { param([int]$Id)\n"
        "  if ($script:alive[$Id]) { [pscustomobject]@{ Id = $Id } }\n"
        "}\n"
        "function Stop-Process { param([int]$Id, [switch]$Force)\n"
        "  if ($Id -notin @(424201, 424202)) { throw 'Unexpected synthetic process ID' }\n"
        "  $script:stopped.Add($Id)\n"
        "  $script:alive[$Id] = $false\n"
        "}\n"
        + functions
        + "\n$failure = ''\n"
        "try { Stop-Tree ([pscustomobject]@{ Id = 424201 }) 'synthetic tree' } "
        "catch { $failure = $_.Exception.Message }\n"
        "[ordered]@{ visits = @($script:visits); stopped = @($script:stopped); "
        "failure = $failure } | ConvertTo-Json -Compress\n"
    )


def bounded_run(
    command: list[str], *, cwd: Path, env: dict[str, str], evidence: Path, name: str, timeout: int,
) -> dict[str, object]:
    started = time.monotonic()
    timed_out = False
    cleanup: dict[str, object] | None = None
    stdout_path = evidence / f"{name}.stdout.log"
    stderr_path = evidence / f"{name}.stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only the newly launched PID and its descendants are eligible.
            try:
                with (evidence / f"{name}.taskkill.log").open("w", encoding="utf-8") as output:
                    killed = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=output, stderr=subprocess.STDOUT, timeout=15, check=False,
                    )
                cleanup = {"taskkill_returncode": killed.returncode}
            except (OSError, subprocess.SubprocessError) as error:
                cleanup = {"error": str(error)}
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup["exit_unconfirmed"] = True
    return {
        "name": name,
        "pid": process.pid,
        "ppid": os.getpid(),
        "executable": command[0],
        "returncode": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": timeout,
        "cleanup": cleanup,
    }


def prepare_offline_environment(repo: Path, evidence: Path, harness: Path) -> dict[str, str]:
    bootstrap = evidence / "bootstrap"
    bootstrap.mkdir()
    guard_directory = evidence / "offline-guards"
    guard_directory.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "import ctypes, json, os, sys, tempfile\n"
        "try:\n"
        "    import tests._offline_network_guard as guard\n"
        "    if not guard._EARLY_INSTALL:\n"
        "        raise RuntimeError('Offline guard did not load before product modules')\n"
        "    if os.name == 'nt':\n"
        "        image = ctypes.create_unicode_buffer(32768)\n"
        "        count = ctypes.windll.kernel32.GetModuleFileNameW(None, image, len(image))\n"
        "        if not count or count >= len(image):\n"
        "            raise RuntimeError('Cannot identify this Python process image')\n"
        "        executable = image.value\n"
        "    else:\n"
        "        executable = os.readlink('/proc/self/exe')\n"
        "    descriptor, path = tempfile.mkstemp(prefix='guard-' + str(os.getpid()) + '-', suffix='.json', dir=os.environ['STORY_GUARD_DIRECTORY'])\n"
        "    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:\n"
        "        json.dump({'pid': os.getpid(), 'ppid': os.getppid(), 'early': True, 'executable': executable}, output)\n"
        "except BaseException as error:\n"
        "    sys.stderr.write('Diagnostic offline guard startup failed: ' + str(error) + '\\n')\n"
        "    os._exit(86)\n",
        encoding="utf-8",
    )
    (bootstrap / "story_cleanup_probe_plugin.py").write_text(
        "from pathlib import Path\n"
        "import os\n"
        f"TARGET = {NODE_ID!r}\n"
        f"RACES = {RACE_NODE_IDS!r}\n"
        "def pytest_collection_modifyitems(items):\n"
        "    mode = os.environ['STORY_DIAGNOSTIC_MODE']\n"
        "    if mode not in ('target', 'races'):\n"
        "        raise RuntimeError('Unknown bounded control group')\n"
        "    expected = [TARGET] if mode == 'target' else RACES\n"
        "    if sorted(item.nodeid for item in items) != sorted(expected):\n"
        "        raise RuntimeError('Control must execute exactly its approved test cases')\n"
        "    if mode == 'target':\n"
        "        items[0].module._HARNESS_PATH = Path(os.environ['STORY_DIAGNOSTIC_HARNESS'])\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update({
        "PYTHONPATH": os.pathsep.join(map(str, (bootstrap, repo, repo / "src"))),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "STORY_GUARD_DIRECTORY": str(guard_directory),
        "STORY_DIAGNOSTIC_HARNESS": str(harness),
        "STORY_DIAGNOSTIC_MODE": "target",
        "STORY_CLEANUP_TRACE": str(evidence / "harness-cleanup.jsonl"),
    })
    return environment


def read_guard_records(evidence: Path) -> list[dict]:
    files = sorted((evidence / "offline-guards").iterdir())
    records = []
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or set(record) != {"pid", "ppid", "early", "executable"}
            or any(type(record[key]) is not int or record[key] <= 0 for key in ("pid", "ppid"))
            or record["early"] is not True
            or not isinstance(record["executable"], str)
            or not (PureWindowsPath(record["executable"]).is_absolute() or Path(record["executable"]).is_absolute())
            or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", PureWindowsPath(record["executable"]).name, re.I) is None
            or not path.name.startswith(f"guard-{record['pid']}-") or path.suffix != ".json"
        ):
            raise ValueError("Invalid early-guard process identity record")
        records.append(record)
    if not records or len({record["pid"] for record in records}) != len(records):
        raise ValueError("Missing or duplicate early-guard process identities")
    return records


def verify_guard_coverage(records: list[dict], trace: list[dict], results: list[dict]) -> None:
    launches = [event for event in trace if event["stage"] == "python.root"]
    if len(launches) != 5 or {event["label"] for event in launches} != PYTHON_ROOT_LABELS:
        raise ValueError("Missing or duplicate owned Python launch roots")
    roots = [row["pid"] for row in results] + [event["target_id"] for event in launches]
    if (
        len(roots) != 7 or any(type(pid) is not int or pid <= 0 for pid in roots)
        or len(set(roots)) != 7 or len(records) != 7
    ):
        raise ValueError("Every one of the seven owned Python launches requires a distinct guard record")
    for label, executable in (
        [("pytest", row["executable"]) for row in results]
        + [(event["label"], event["details"]["executable"]) for event in launches]
    ):
        expected_name = "oc.exe" if label == "isolated service" else "python.exe"
        if (
            not isinstance(executable, str) or not PureWindowsPath(executable).is_absolute()
            or PureWindowsPath(executable).name.casefold() != expected_name
        ):
            raise ValueError("Owned launch root has an invalid executable identity")
    children: dict[int, set[int]] = {}
    parents: dict[int, int] = {}
    images: dict[int, str] = {}

    def add_identity(pid: int, ppid: int, executable: str | None) -> None:
        if any(type(value) is not int or value <= 0 for value in (pid, ppid)) or pid == ppid:
            raise ValueError("Invalid process parent identity")
        if pid in parents and parents[pid] != ppid:
            raise ValueError("Process has conflicting parent identities")
        parents[pid] = ppid
        children.setdefault(ppid, set()).add(pid)
        if executable:
            if not isinstance(executable, str):
                raise ValueError("Invalid process executable identity")
            image = str(PureWindowsPath(executable)).casefold()
            if pid in images and images[pid] != image:
                raise ValueError("Process has conflicting executable identities")
            images[pid] = image

    for row in results:
        add_identity(row["pid"], row["ppid"], row["executable"])
    for event in launches:
        add_identity(event["target_id"], event["powershell_pid"], event["details"]["executable"])
    for event in trace:
        if event["stage"].startswith("cim.end"):
            for child in event["details"] or []:
                if child["ParentProcessId"] != event["target_id"]:
                    raise ValueError("CIM child has an inconsistent parent identity")
                add_identity(child["ProcessId"], child["ParentProcessId"], child["ExecutablePath"])
    for record in records:
        add_identity(record["pid"], record["ppid"], record["executable"])
    for pid in parents:
        ancestors = set()
        while pid in parents:
            if pid in ancestors:
                raise ValueError("Process identity chain contains a cycle")
            ancestors.add(pid)
            pid = parents[pid]
    covered = set()
    for root in roots:
        descendants = set()
        pending = [root]
        while pending:
            process_id = pending.pop()
            if process_id in descendants:
                continue
            descendants.add(process_id)
            pending.extend(children.get(process_id, ()))
        matches = {record["pid"] for record in records if record["pid"] in descendants}
        if len(matches) != 1 or covered & matches:
            raise ValueError("Missing, ambiguous, or duplicated guard coverage for an owned launch root")
        covered.update(matches)


def evidence_errors(evidence: Path, results: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    if len(results) != 2 or any(row["timed_out"] for row in results):
        errors.append("A bounded child did not finish")
    if any(row["returncode"] != 0 for row in results):
        errors.append("A control test group failed; this is not a passing verification")
    try:
        races = ET.parse(evidence / "stop-tree-races.xml").findall(".//testcase")
        if sorted(case.attrib.get("name", "") for case in races) != sorted(
            node.split("::")[1] for node in RACE_NODE_IDS
        ):
            errors.append("JUnit did not identify exactly the four approved cleanup races")
        cases = ET.parse(evidence / "failing-node.xml").findall(".//testcase")
        if len(cases) != 1 or cases[0].attrib.get("name") != NODE_ID.split("::")[1]:
            errors.append("JUnit did not identify exactly the requested case")
        if any(case.find(tag) is not None for case in races + cases for tag in ("failure", "error", "skipped")):
            errors.append("An approved case failed, errored, or skipped")
        trace = [
            json.loads(line)
            for line in (evidence / "harness-cleanup.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        stages = [event["stage"] for event in trace]
        if not {"harness.begin", "chromium.begin", "finally.exit failures=0"}.issubset(stages):
            errors.append("Requested case did not complete the fail-closed cleanup path")
        verify_guard_coverage(read_guard_records(evidence), trace, results)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
        errors.append(f"Incomplete diagnostic evidence: {error}")
    return errors


def verify_source(repo: Path, expected_source_sha: str) -> tuple[str, list[str]]:
    if re.fullmatch(r"[0-9a-f]{40}", expected_source_sha) is None:
        raise RuntimeError("Expected source must be the verified helper parent SHA")
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=repo, check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    revision = git("rev-parse", "HEAD")
    if revision != expected_source_sha:
        raise RuntimeError("Source checkout does not match the verified helper parent")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Reviewed source checkout has tracked changes")
    # Disable rename collapsing so a runtime file moved to a helper path cannot
    # conceal the removed runtime path from this exact allowlist.
    changed = git("diff", "--no-renames", "--name-only", PRODUCT_SHA, revision, "--").splitlines()
    if not changed or set(changed) - ALLOWED_SOURCE_PATHS or TEST_RELATIVE not in changed:
        raise RuntimeError("Only the reviewed test and exact diagnostic helper paths may differ from 6781")
    return revision, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--verify-source-only", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    revision, changed_paths = verify_source(repo, args.expected_source_sha)
    if args.verify_source_only:
        print(json.dumps({"source_sha": revision, "runtime_baseline_sha": PRODUCT_SHA, "allowed_changed_paths": changed_paths}))
        return 0
    if os.name != "nt":
        parser.error("Native diagnosis requires Windows; generation tests can run on any host")
    if args.evidence is None:
        parser.error("Native control requires --evidence")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    original = repo / HARNESS_RELATIVE
    original_bytes = original.read_bytes()
    source = original.read_text(encoding="utf-8-sig")
    instrumented = instrument(source)
    instrumented_artifact = evidence / "instrumented-harness.ps1"
    instrumented_artifact.write_text(instrumented, encoding="utf-8-sig")
    instrumented_bytes = instrumented_artifact.read_bytes()
    temporary = original.with_name(f".story-cleanup-diagnostic-{uuid.uuid4().hex}.ps1")
    results: list[dict[str, object]] = []
    try:
        temporary.write_text(instrumented, encoding="utf-8-sig")
        if temporary.read_bytes() != instrumented_bytes:
            raise RuntimeError("Executed diagnostic copy differs from the artifact")
        environment = prepare_offline_environment(repo, evidence, temporary)
        results.append(bounded_run(
            [
                str(repo / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
                "-p", "story_cleanup_probe_plugin", "-vv", "-s", *RACE_NODE_IDS,
                "--junitxml", str(evidence / "stop-tree-races.xml"),
            ],
            cwd=repo, env=dict(environment, STORY_DIAGNOSTIC_MODE="races"),
            evidence=evidence, name="stop-tree-races", timeout=60,
        ))
        results.append(bounded_run(
            [
                str(repo / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
                "-p", "story_cleanup_probe_plugin", "-vv", "-s", NODE_ID,
                "--basetemp", str(evidence / "pytest-temp"),
                "--junitxml", str(evidence / "failing-node.xml"),
            ],
            cwd=repo, env=environment, evidence=evidence, name="failing-node", timeout=240,
        ))
    finally:
        temporary.unlink(missing_ok=True)
        unchanged = original.read_bytes() == original_bytes
        errors = evidence_errors(evidence, results)
        report = {
            "mode": "bounded-warning-assertion-control",
            "product_sha": PRODUCT_SHA,
            "source_sha": revision,
            "source_changed_paths": changed_paths,
            "test_file_sha256": hashlib.sha256((repo / TEST_RELATIVE).read_bytes()).hexdigest(),
            "harness_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "instrumented_harness_sha256": hashlib.sha256(instrumented_bytes).hexdigest(),
            "product_harness_unchanged": unchanged,
            "node_id": NODE_ID,
            "race_node_ids": RACE_NODE_IDS,
            "scope": "one target plus four cleanup races; runtime unchanged from6781; no release gate, installer, browser, or provider",
            "results": results,
            "evidence_errors": errors,
        }
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    # Unlike the first diagnostic collection, this control requires every one
    # of the five approved tests to pass. Any failure keeps the artifact and fails.
    return 0 if unchanged and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
