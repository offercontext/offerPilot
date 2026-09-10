"""Execute the real gate scripts with isolated native-command and HTTP fixtures.

The expensive tools are replaced, not the orchestration under test. In particular,
Windows fixtures return real native exit codes, including across child PowerShells.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["bash", "powershell"])
def gate(request, tmp_path):
    shell = request.param
    if shell == "powershell" and os.name != "nt":
        pytest.skip("Windows PowerShell/native cmd regression requires Windows")
    executable = shutil.which(shell)
    if shell == "bash" and os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "usr/bin/bash.exe" if git else Path("")
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        pytest.skip(f"{shell} is unavailable")

    repo = tmp_path / "gate workspace"
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    (repo / "web/dist").mkdir(parents=True)
    (repo / "web/dist/index.html").write_text('<div id="root"></div>')
    bin_dir = repo / "bin"
    bin_dir.mkdir()
    log = repo / "commands.log"
    log.touch()
    env = os.environ.copy()
    env.update(GATE_LOG=str(log), GATE_FAIL="", OFFERPILOT_DATA="caller-data")
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    if shell == "bash":
        if os.name == "nt":
            env["PATH"] = (
                str(bin_dir) + os.pathsep + str(Path(executable).parent) + os.pathsep + env["PATH"]
            )
        for command in ("uv", "npm", "curl", "docker"):
            stub = bin_dir / command
            stub.write_text(
                "#!/usr/bin/env bash\n"
                f'command="{command} $*"\n'
                'printf "%s\\n" "$command" >> "$GATE_LOG"\n'
                'if [[ "$command" == "$GATE_FAIL" ]]; then exit 7; fi\n'
                'if [[ "$command" == "curl "* ]]; then '
                "printf '%s\\n' '{\"status\":\"ok\"}' '<div id=\"root\"></div>'; fi\n"
                'if [[ "$command" == "npm run build" && "${GATE_NO_ARTIFACT:-}" != 1 ]]; '
                "then mkdir -p dist; printf '<div id=\"root\"></div>' > dist/index.html; fi\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
        for script in (repo / "scripts").glob("*.sh"):
            script.chmod(0o755)
    else:
        for command in ("uv", "npm", "docker", "oc"):
            (bin_dir / f"{command}.cmd").write_text(
                "@echo off\n"
                f'echo {command} %*>>"%GATE_LOG%"\n'
                f'if "{command} %*"=="%GATE_FAIL%" exit /b 7\n'
                + (
                    'if "%*"=="run build" if not "%GATE_NO_ARTIFACT%"=="1" '
                    "(if not exist dist mkdir dist)\n"
                    'if "%*"=="run build" if not "%GATE_NO_ARTIFACT%"=="1" '
                    "echo root>dist\\index.html\n"
                    if command == "npm"
                    else ""
                )
                + (
                    'if "%*"=="tool install --force ." copy /y '
                    '"%~dp0oc.cmd" "%UV_TOOL_BIN_DIR%\\oc.cmd">nul\n'
                    if command == "uv"
                    else ""
                )
                + "exit /b 0\n",
                encoding="utf-8",
            )
        launcher = repo / "launch.ps1"
        launcher.write_text(
            r"""param([string]$Target, [int]$Port = 18765, [switch]$SkipBuild,
    [switch]$RealAi, [switch]$Install, [switch]$Docker)
$ErrorActionPreference = 'Stop'
function Start-Process {
    Add-Content $env:GATE_LOG 'server start'
    $process = [pscustomobject]@{ HasExited = $false; Id = 424242 }
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($milliseconds) $true }
    return $process
}
function Stop-Process { Add-Content $env:GATE_CLEANUP_LOG 'parent only' }
function taskkill.exe {
    Add-Content $env:GATE_CLEANUP_LOG ($args -join ' ')
    $exitCode = if ($env:GATE_CLEANUP_FAIL -eq '1') { 7 } else { 0 }
    & $env:ComSpec /d /c exit $exitCode
    $global:LASTEXITCODE = $LASTEXITCODE
}
function Invoke-WebRequest { return @{ Content = '{"status":"ok","root":true}' } }
function powershell {
    param([switch]$NoProfile, [string]$ExecutionPolicy, [string]$File,
        [int]$Port = 18765, [switch]$SkipBuild)
    $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $env:GATE_LAUNCHER, '-Target', $File)
    if ($File -like '*local-smoke*') {
        $childArgs += @('-Port', $Port)
        if ($SkipBuild) { $childArgs += '-SkipBuild' }
    }
    & (Join-Path $PSHOME 'powershell.exe') @childArgs
    $global:LASTEXITCODE = $LASTEXITCODE
}
$targetArgs = @{}
foreach ($key in $PSBoundParameters.Keys) {
    if ($key -ne 'Target') { $targetArgs[$key] = $PSBoundParameters[$key] }
}
try { & $Target @targetArgs }
finally { Set-Content $env:GATE_RESTORED_DATA $env:OFFERPILOT_DATA }
""",
            encoding="utf-8",
        )
        env["GATE_LAUNCHER"] = str(launcher)
        env["GATE_CLEANUP_LOG"] = str(repo / "cleanup.log")
        env["GATE_RESTORED_DATA"] = str(repo / "restored-data.txt")

    def run(script="release-gate", *, fail="", args=(), artifact=True, cleanup_fail=False):
        env["GATE_FAIL"] = fail
        env["GATE_CLEANUP_FAIL"] = "1" if cleanup_fail else "0"
        if not artifact:
            (repo / "web/dist/index.html").unlink(missing_ok=True)
        suffix = "sh" if shell == "bash" else "ps1"
        target = f"scripts/{script}.{suffix}"
        command = (
            [executable, target, *args]
            if shell == "bash"
            else [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-Target",
                target,
                *args,
            ]
        )
        # File-backed output cannot hang draining pipes inherited by grandchildren.
        with (repo / "output.log").open("w") as output:
            result = subprocess.run(
                command,
                cwd=repo,
                env=env,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        result.stdout = (repo / "output.log").read_text(errors="replace")
        result.stderr = ""
        return result, log.read_text().splitlines()

    return shell, run, repo


def test_release_builds_once_and_retains_cli_smoke_and_local_verify(gate):
    _, run, _ = gate
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("npm run build") == 1
    assert "uv run oc smoke --static-dir web/dist" in calls
    assert "uv run oc verify --profile local --static-dir web/dist" in calls
    assert not any("real-ai" in call or call.startswith("docker ") for call in calls)
    assert "Release gate passed" in result.stdout


@pytest.mark.parametrize(
    "step",
    [
        "uv run pytest -q",
        "uv run ruff check .",
        "uv run mypy src",
        "npm test",
        "npm run build",
        "uv run oc smoke --static-dir web/dist",
        "uv run oc verify --profile local --static-dir web/dist",
    ],
)
def test_release_stops_on_each_native_failure(gate, step):
    _, run, _ = gate
    result, calls = run(fail=step)
    assert result.returncode != 0, result.stdout + result.stderr
    assert calls[-1] == step
    assert "Release gate passed" not in result.stdout


def test_standalone_smoke_builds_even_when_dist_exists(gate):
    _, run, _ = gate
    result, calls = run("local-smoke")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("npm run build") == 1


def test_smoke_skip_build_reuses_artifact_and_preserves_port(gate):
    shell, run, _ = gate
    args = ["--skip-build", "18799"] if shell == "bash" else ["-SkipBuild", "-Port", "18799"]
    result, calls = run("local-smoke", args=args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "npm run build" not in calls
    assert "18799" in result.stdout


def test_smoke_skip_build_requires_index_before_starting(gate):
    shell, run, _ = gate
    args = ["--skip-build"] if shell == "bash" else ["-SkipBuild"]
    result, calls = run("local-smoke", args=args, artifact=False)
    assert result.returncode != 0
    assert not calls
    assert "index.html" in result.stdout + result.stderr


@pytest.mark.parametrize("args", [["--unknown"], ["18799", "18800"]])
def test_bash_smoke_rejects_invalid_arguments_before_build(gate, args):
    shell, run, _ = gate
    if shell != "bash":
        pytest.skip("Bash positional argument parser")
    result, calls = run("local-smoke", args=args)
    assert result.returncode != 0
    assert not calls


@pytest.mark.parametrize("step", ["npm run build", "uv run oc smoke --static-dir web/dist"])
def test_standalone_smoke_stops_on_native_failure(gate, step):
    _, run, _ = gate
    result, calls = run("local-smoke", fail=step)
    assert result.returncode != 0, result.stdout + result.stderr
    assert calls[-1] == step
    assert "Local smoke passed" not in result.stdout


@pytest.mark.parametrize("fail", ["", "uv run oc smoke --static-dir web/dist"])
def test_powershell_smoke_cleans_owned_process_tree_and_restores_data(gate, fail):
    shell, run, repo = gate
    if shell != "powershell":
        pytest.skip("Windows process tree cleanup")
    result, _ = run("local-smoke", fail=fail)
    assert (result.returncode == 0) == (not fail), result.stdout
    assert (repo / "cleanup.log").read_text().strip() == "/PID 424242 /T /F"
    assert (repo / "restored-data.txt").read_text().strip() == "caller-data"


def test_powershell_smoke_cleanup_failure_cannot_report_success(gate):
    shell, run, repo = gate
    if shell != "powershell":
        pytest.skip("Windows process tree cleanup")
    result, _ = run("local-smoke", cleanup_fail=True)
    assert result.returncode != 0, result.stdout
    assert "Local smoke passed" not in result.stdout
    assert (repo / "restored-data.txt").read_text().strip() == "caller-data"


@pytest.mark.parametrize(
    ("flag", "step"),
    [
        ("RealAi", "uv run oc verify --profile real-ai --static-dir web/dist"),
        ("Install", "uv run oc --help"),
        ("Install", "uv tool install --force ."),
        ("Install", "oc --help"),
        ("Docker", "docker build -t offerpilot:smoke ."),
        (
            "Docker",
            "docker run --rm -e OFFERPILOT_DATA=/tmp/offerpilot-smoke offerpilot:smoke smoke --static-dir /app/web/dist",
        ),
    ],
)
def test_powershell_optional_gate_failures_reach_parent(gate, flag, step):
    shell, run, _ = gate
    if shell != "powershell":
        pytest.skip("Windows nested native exit-code regression")
    result, calls = run(fail=step, args=[f"-{flag}"])
    assert result.returncode != 0, result.stdout + result.stderr
    assert calls[-1] == step
    assert "Release gate passed" not in result.stdout
