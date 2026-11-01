"""Contract tests for the narrow, fail-closed Windows workflow routing.

GitHub startsWith and string equality are case-insensitive:
https://docs.github.com/en/actions/reference/workflows-and-actions/expressions
The test helpers accept only the workflow's deliberately bounded expression
shape, then exercise its operands using those documented GitHub semantics.
They are not a general GitHub Actions or PowerShell interpreter.
"""
from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/desktop-windows.yml").read_text(
    encoding="utf-8"
)
PREFIX = "build: AI [windows-package-only] "


def _job(name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)", WORKFLOW, re.M | re.S)
    assert match, f"Missing job: {name}"
    return match.group(1)


def _activation() -> str:
    match = re.search(r'^  WINDOWS_PACKAGE_ONLY: "\$\{\{ (.+) \}\}"$', WORKFLOW, re.M)
    assert match, "Package-only mode must have one explicit activation expression"
    return match.group(1)


def _package_only(event_name: str, payload: dict, ref: str = "refs/heads/feat/20261005-windows-desktop-validation") -> bool:
    # Fail closed if the production expression is widened or changes shape.
    match = re.fullmatch(
        r"github\.event_name == '([^']+)' && github\.ref == '([^']+)' && "
        r"startsWith\(github\.event\.head_commit\.message, '([^']+)'\)",
        _activation(),
    )
    assert match
    required_event, required_ref, prefix = match.groups()
    assert required_ref == "refs/heads/feat/20261005-windows-desktop-validation"
    assert required_event == "push"
    assert prefix == PREFIX
    commit = payload.get("head_commit") or {}
    message = commit.get("message") or ""  # Missing properties/null cast to empty strings.
    return (event_name.casefold() == required_event.casefold()
            and ref.casefold() == required_ref.casefold()
            and message.casefold().startswith(prefix.casefold()))


def _regression_enabled(job: str, event_name: str, payload: dict) -> bool:
    match = re.search(r'^    if: "\$\{\{ (.+) \}\}"$', _job(job), re.M)
    assert match, f"Missing explicit mode guard for {job}"
    expected = f"!({_activation()})"
    if job == "full-regression":
        # Aggregation still runs after failed/skipped dependencies in full mode.
        expected = f"always() && {expected}"
    expected = "needs.pr-feedback.outputs.mode != 'selected' && " + expected
    assert match.group(1) == expected
    return not _package_only(event_name, payload)


@pytest.mark.parametrize(
    ("event_name", "payload", "package_only"),
    [
        ("push", {"head_commit": {"message": PREFIX + "new installer"}}, True),
        ("push", {"head_commit": {"message": PREFIX.upper() + "new installer"}}, True),
        ("push", {"head_commit": {"message": PREFIX.lower() + "new installer"}}, True),
        ("push", {"head_commit": {"message": PREFIX + "new installer\n\nbody"}}, True),
        ("push", {}, False),
        ("push", {"head_commit": None}, False),
        ("push", {"head_commit": {}}, False),
        ("push", {"head_commit": {"message": None}}, False),
        ("push", {"head_commit": {"message": ""}}, False),
        ("push", {"head_commit": {"message": "fix: AI ordinary product change"}}, False),
        ("push", {"head_commit": {"message": PREFIX.rstrip()}}, False),
        ("push", {"head_commit": {"message": " " + PREFIX + "near miss"}}, False),
        ("push", {"head_commit": {"message": "fix: AI " + PREFIX + "near miss"}}, False),
        ("push", {"head_commit": {"message": "build: AI [windows-package-only-extra] x"}}, False),
        ("push", {"head_commit": {"message": "build: AI [windows-package-only]\tx"}}, False),
        ("push", {"head_commit": {"message": "build: AI full gate\n\n" + PREFIX}}, False),
        ("push", {"head_commit": {"message": "build: AI [windows-full] full gate"}}, False),
        ("workflow_dispatch", {}, False),
        ("workflow_dispatch", {"head_commit": {"message": PREFIX + "ignored"}}, False),
        ("workflow_dispatch", {"inputs": {"mode": "package-only"}}, False),
        ("workflow_dispatch", {"inputs": {"mode": "full"}}, False),
        ("pull_request", {"head_commit": {"message": PREFIX + "ignored"}}, False),
        ("schedule", {"head_commit": {"message": PREFIX + "ignored"}}, False),
        ("", {"head_commit": {"message": PREFIX + "ignored"}}, False),
    ],
)
def test_only_explicit_push_prefix_skips_regression(event_name, payload, package_only):
    assert _package_only(event_name, payload) is package_only
    for job in ("pytest-manifest", "pytest-shards", "full-regression",
                "simplification-helper-safety"):
        assert _regression_enabled(job, event_name, payload) is not package_only


@pytest.mark.parametrize("ref", [
    "refs/heads/master", "refs/heads/feat/20261021-product-simplification", "refs/pull/19/merge",
])
def test_package_only_marker_never_bypasses_other_branches(ref):
    assert not _package_only("push", {"head_commit": {"message": PREFIX + "ignored"}}, ref)


def test_default_workflow_and_regression_dependency_contract_remain_full():
    assert "  workflow_dispatch:\n\npermissions:" in WORKFLOW
    assert "      - feat/20261005-windows-desktop-validation\n" in WORKFLOW
    assert "    needs: [pr-feedback, pytest-manifest]\n" in _job("pytest-shards")
    full = _job("full-regression")
    assert "    needs: [pr-feedback, pytest-manifest, pytest-shards, simplification-helper-safety]\n" in full
    assert "HELPER_SAFETY_RESULT: ${{ needs.simplification-helper-safety.result }}" in full
    assert "if ($env:HELPER_SAFETY_RESULT -ne 'success')" in full
    assert "$env:SHARD_RESULT -ne 'success' -or $env:MANIFEST_RESULT -ne 'success'" in full
    assert "scripts/release-gate.ps1 -Install -PytestEvidence pytest-evidence" in full
    assert 'if ($LASTEXITCODE -ne 0) { throw "Complete repository release gate failed" }' in full
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in WORKFLOW


def test_shard_timeout_adds_bounded_headroom_without_relaxing_gates():
    expected_timeouts = {
        "validation-package": "60",
        "simplification-helper-safety": "20",
        "pytest-manifest": "20",
        "pytest-shards": "120",
        "full-regression": "90",
        "validation-status": "5",
    }
    for job, minutes in expected_timeouts.items():
        assert re.findall(r"^    timeout-minutes: (\d+)$", _job(job), re.M) == [minutes]
    shards = _job("pytest-shards")
    assert re.findall(r"^      fail-fast: (.+)$", shards, re.M) == ["false"]
    assert re.findall(r"^      max-parallel: (\d+)$", shards, re.M) == ["4"]
    matrix = re.findall(r"^        shard: \[([^\]]+)\]$", shards, re.M)
    assert len(matrix) == 1
    assert [int(value.strip()) for value in matrix[0].split(",")] == list(range(20))
    assert "gate.py collect --count 20 --manifest" in _job("pytest-manifest")
    assert "name: Complete pytest shard ${{ matrix.shard }} of 20" in shards
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in WORKFLOW
    assert "continue-on-error:" not in WORKFLOW
    assert 'if ($LASTEXITCODE -ne 0) { throw "Full pytest shard failed" }' in shards
    assert "if: ${{ always() }}\n        uses: actions/upload-artifact@v4" in shards
    assert "2400 aggregate shard-minute ceiling" in WORKFLOW


@pytest.mark.parametrize("job", ["pytest-manifest", "pytest-shards", "full-regression"])
def test_regression_jobs_restore_and_verify_history_before_expensive_work(job):
    steps = re.split(r"^      - ", _job(job), flags=re.M)[1:]
    if job == "full-regression":
        assert steps.pop(0) == _dependency_preflight()
    assert steps[0].startswith("uses: actions/checkout@v4\n")
    assert re.search(r"^        with:\n(?:          #[^\n]*\n)*"
                     r"          fetch-depth: 0\n          persist-credentials: false\n", steps[0], re.M)
    assert steps[1].startswith("uses: actions/setup-python@v5\n")
    preflight = steps[2]
    assert preflight.startswith("name: Verify immutable regression history before ")
    assert "        run: |\n" in preflight
    assert "          python scripts/test_shards/check_history.py\n" in preflight
    assert ('          if ($LASTEXITCODE -ne 0) { throw '
            '"Required regression Git history is unavailable" }\n') in preflight
    assert not re.search(r"^        (if|continue-on-error):", "".join(steps[:3]), re.M)
    assert "uv sync" not in "".join(steps[:3])


def test_package_checks_remain_independent_and_mandatory():
    package = _job("validation-package")
    assert "    needs: pr-feedback\n" in package
    assert "    if: ${{ needs.pr-feedback.outputs.mode != 'selected' }}" in package
    assert re.findall(r'^    needs: (.+)$', package, re.M) == ['pr-feedback']
    assert "continue-on-error:" not in WORKFLOW
    # The only conditional step preserves evidence, including after failures.
    assert re.findall(r"^        if: (.+)$", package, re.M) == ["${{ always() }}"]
    for command, failure in [
        ("tests/test_desktop.py tests/test_auth_api.py tests/test_static_frontend.py",
         "Focused desktop regression tests failed"),
        ("npm.cmd test --prefix desktop", "Desktop lifecycle tests failed"),
        ("uv run --frozen ruff check .", "Ruff failed"),
        ("uv run --frozen mypy src/offerpilot/desktop.py src/offerpilot/api.py",
         "Desktop type check failed"),
        ("npm.cmd run build --prefix web", "Frontend build failed"),
        ("node web/node_modules/typescript/bin/tsc --project web/tests/desktop-layout/tsconfig.json",
         "Layout fixture types failed"),
        ("node web/tests/desktop-layout/run.mjs", "Rendered layout regression failed"),
        ("python desktop/build-backend.py", "Frozen backend build failed"),
        ("--backend desktop/backend-dist/offerpilot-backend/offerpilot-backend.exe",
         "Frozen backend smoke failed"),
        ("npm.cmd run build:win --prefix desktop", "NSIS packaging failed"),
        ("--backend desktop/dist/win-unpacked/resources/backend/offerpilot-backend.exe",
         "Packaged resource smoke failed"),
        ("-File desktop/smoke-install.ps1 -InstallerDirectory desktop/dist",
         "Current NSIS installation and installed backend smoke failed"),
    ]:
        assert re.search(
            re.escape(command) + r"[^\n]*\n\s+"
            + re.escape(f'if ($LASTEXITCODE -ne 0) {{ throw "{failure}" }}'),
            package,
        ), command


def test_package_only_is_disclosed_in_scope_installer_and_final_report():
    package = _job("validation-package")
    for start, end, destination in [
        ("Record validation scope and run identity", "actions/setup-python@v5", "scope.txt"),
        ("Hash installers and disclose validation-only scope", "Upload experimental", "VALIDATION-NOTES.txt"),
    ]:
        script = package.split(start, 1)[1].split(end, 1)[0]
        assert "if ($env:WINDOWS_PACKAGE_ONLY -eq 'true')" in script
        assert "EXPERIMENTAL PACKAGE-ONLY" in script
        assert "Full regression: NOT RUN" in script
        assert "NOT RELEASE READY" in script
        assert destination in script
        assert '"Commit: $env:GITHUB_SHA"' in script
        assert '"Attempt: $env:GITHUB_RUN_ATTEMPT"' in script
    status = _job("validation-status")
    assert "if ($env:WINDOWS_PACKAGE_ONLY -eq 'true')" in status
    assert "Full regression: NOT RUN" in status
    assert "NOT RELEASE READY" in status
    assert "Required full regression job: $env:REGRESSION_RESULT" in status


@pytest.mark.parametrize(
    ("feedback_result", "package_result", "regression_result"),
    tuple(itertools.product(("success", "failure", "cancelled", "skipped", ""), repeat=3)),
)
def test_final_status_requires_both_jobs_success(feedback_result, package_result, regression_result):
    status = _job("validation-status")
    assert "    if: ${{ always() }}\n" in status
    assert "    needs: [pr-feedback, validation-package, full-regression]\n" in status
    assert "PACKAGE_RESULT: ${{ needs.validation-package.result }}" in status
    assert "REGRESSION_RESULT: ${{ needs.full-regression.result }}" in status
    # Check and evaluate the actual final failure guard; no skipped-as-success branch.
    guards = re.findall(
        r"if \(\$env:FEEDBACK_RESULT -ne 'success' -or \$env:(\w+) -ne '([^']+)' -or \$env:(\w+) -ne '([^']+)'\) \{\n"
        r'\s+throw "Validation is incomplete:[^\n]+\n\s+\}',
        status,
    )
    assert guards == [("PACKAGE_RESULT", "success", "REGRESSION_RESULT", "success")]
    _, package_success, _, regression_success = guards[0]
    rejects = (feedback_result != "success" or package_result != package_success
               or regression_result != regression_success)
    assert not rejects if (feedback_result, package_result, regression_result) == ("success",) * 3 else rejects
    assert status.count("throw ") == 1
    assert not re.search(r"\b(exit|return|continue-on-error)\b", status)
    assert "if: ${{ always() }}\n        uses: actions/upload-artifact@v4" in status


def _dependency_preflight() -> str:
    steps = re.split(r"^      - ", _job("full-regression"), flags=re.M)[1:]
    assert steps[0].startswith("name: Require successful dependencies before regression setup\n")
    assert steps[1].startswith("uses: actions/checkout@v4\n")
    return steps[0]


@pytest.mark.parametrize(
    "results", tuple(itertools.product(("success", "failure", "cancelled", "skipped", ""), repeat=3)),
)
def test_dependency_preflight_rejects_every_non_success_before_setup(results):
    preflight = _dependency_preflight()
    names = ("HELPER_SAFETY_RESULT", "SHARD_RESULT", "MANIFEST_RESULT")
    for name, job in zip(names, ("simplification-helper-safety", "pytest-shards", "pytest-manifest")):
        assert f"{name}: ${{{{ needs.{job}.result }}}}" in preflight
    # Bounded source-contract evaluator, not a PowerShell runtime simulation.
    # Require unconditional throw bodies and exactly the original two guards.
    guards = re.findall(r'if \(([^\n]+)\) \{\n\s+throw "[^\n]+"\n\s+\}', preflight)
    assert guards == [
        "$env:HELPER_SAFETY_RESULT -ne 'success'",
        "$env:SHARD_RESULT -ne 'success' -or $env:MANIFEST_RESULT -ne 'success'",
    ]
    values = dict(zip(names, results))
    rejects = any(
        any(values[name].casefold() != expected.casefold()
            for name, expected in re.findall(r"\$env:(\w+) -ne '([^']+)'", guard))
        for guard in guards
    )
    assert rejects is (results != ("success", "success", "success"))
    assert preflight.count("if (") == preflight.count("throw ") == 2
    assert not re.search(r"\b(exit|return|continue-on-error)\b", preflight)
    assert not re.search(r"^        if:", preflight, re.M)


def test_dependency_failure_keeps_diagnostics_without_checkout_and_success_keeps_full_gate():
    preflight = _dependency_preflight()
    # Checkout cleans the workspace; diagnostics must live outside it on both paths.
    evidence = '"$env:RUNNER_TEMP/full-regression-preflight.txt"'
    assert f"$report | Tee-Object -FilePath {evidence}" in preflight
    assert "$report | Add-Content $env:GITHUB_STEP_SUMMARY" in preflight
    assert preflight.index("Tee-Object") < preflight.index("throw ")
    for field in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                  "HELPER_SAFETY_RESULT", "SHARD_RESULT", "MANIFEST_RESULT"):
        assert f"$env:{field}" in preflight
    steps = re.split(r"^      - ", _job("full-regression"), flags=re.M)[1:]
    # No new skip/continue-on-error policy: success still reaches the complete gate,
    # while a failed first step skips setup and always preserves the diagnostic.
    assert re.findall(r"^        if: (.+)$", "".join(steps), re.M) == ["${{ always() }}"] * 2
    assert "continue-on-error:" not in "".join(steps)
    gate = next(step for step in steps if step.startswith("name: Verify full pytest evidence"))
    assert "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release-gate.ps1 -Install -PytestEvidence pytest-evidence" in gate
    assert 'if ($LASTEXITCODE -ne 0) { throw "Complete repository release gate failed" }' in gate
    assert "needs." not in gate
    upload = steps[-1]
    assert "if: ${{ always() }}\n        uses: actions/upload-artifact@v4" in upload
    assert upload.split("          path: |\n", 1)[1].strip() == (
        "desktop/ci-evidence/\n            pytest-evidence/"
    )
    diagnostic = steps[-2]
    assert "if: ${{ always() }}\n        uses: actions/upload-artifact@v4" in diagnostic
    assert "name: windows-full-regression-preflight-${{ github.sha }}-${{ github.run_attempt }}" in diagnostic
    assert "path: ${{ runner.temp }}/full-regression-preflight.txt\n" in diagnostic
    assert "retention-days: 7\n" in diagnostic
    # Upload only this diagnostic, not runner temp files, secrets or the environment.
    assert re.findall(r"^          path: (.+)$", diagnostic, re.M) == [
        "${{ runner.temp }}/full-regression-preflight.txt",
    ]
    assert set(re.findall(r"\$env:(\w+)", preflight)) == {
        "RUNNER_TEMP", "GITHUB_STEP_SUMMARY", "GITHUB_SHA", "GITHUB_SERVER_URL",
        "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
        "HELPER_SAFETY_RESULT", "SHARD_RESULT", "MANIFEST_RESULT",
    }


@pytest.mark.parametrize("dependency,result", [(None, "success")] + [
    (dependency, result)
    for dependency in ("HELPER_SAFETY_RESULT", "SHARD_RESULT", "MANIFEST_RESULT")
    for result in ("failure", "cancelled", "skipped", None)
])
def test_dependency_preflight_native_exit_and_evidence(tmp_path, dependency, result):
    shell = shutil.which("pwsh")
    if not shell:
        if os.name == "nt":
            pytest.fail("Windows workflow requires pwsh; native preflight must not be skipped")
        pytest.skip("Native preflight requires pwsh (mandatory on Windows)")
    # Execute the actual workflow body, without checkout or repository dependencies.
    script = textwrap.dedent(_dependency_preflight().split("        run: |\n", 1)[1])
    launcher = tmp_path / "preflight.ps1"
    launcher.write_text("$ErrorActionPreference = 'Stop'\n" + script, encoding="utf-8")
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    workspace = tmp_path / "empty workspace"
    workspace.mkdir()
    summary = tmp_path / "step summary.txt"
    env = os.environ.copy()
    env.update(
        RUNNER_TEMP=str(runner_temp), GITHUB_STEP_SUMMARY=str(summary),
        GITHUB_SHA="fixture-commit", GITHUB_SERVER_URL="https://github.com",
        GITHUB_REPOSITORY="fixture/repository", GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
        HELPER_SAFETY_RESULT="success", SHARD_RESULT="success", MANIFEST_RESULT="success",
    )
    if dependency:
        if result is None:
            env.pop(dependency)  # Missing, not merely an explicitly empty value.
        else:
            env[dependency] = result
    completed = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(launcher)],
        cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert (completed.returncode == 0) is (dependency is None), completed.stderr
    if dependency:
        expected_error = ("Linux helper safety must succeed" if dependency == "HELPER_SAFETY_RESULT"
                          else "Collection and all shards must succeed")
        assert expected_error in completed.stderr
    expected = [
        "Full regression dependency preflight (all must be success).",
        "Commit: fixture-commit",
        "Run: https://github.com/fixture/repository/actions/runs/123",
        "Attempt: 2",
        f"Linux helper safety: {env.get('HELPER_SAFETY_RESULT', '')}",
        f"Pytest manifest: {env.get('MANIFEST_RESULT', '')}",
        f"Pytest shards: {env.get('SHARD_RESULT', '')}",
    ]
    assert (runner_temp / "full-regression-preflight.txt").read_text(encoding="utf-8-sig").splitlines() == expected
    assert summary.read_text(encoding="utf-8-sig").splitlines() == expected
    assert list(workspace.iterdir()) == []


def test_product_supersession_is_isolated_to_the_same_pr():
    # Pin the small expression rather than pretending to interpret arbitrary GHA.
    assert ("  group: windows-product-${{ github.event_name == 'pull_request' && "
            "format('pr-{0}', github.event.pull_request.number) || "
            "format('run-{0}', github.run_id) }}") in WORKFLOW
    assert "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in WORKFLOW
    assert WORKFLOW.count('cancel-in-progress:') == 1
    assert "types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]" in WORKFLOW
    # No broader branch, repository or paid-workflow concurrency namespace.
    concurrency = WORKFLOW.split('concurrency:\n', 1)[1].split('\n\n', 1)[0]
    assert 'github.ref' not in concurrency and 'companion' not in concurrency
    assert 'simplification' not in concurrency and 'workflow_dispatch' not in concurrency
    assert 'actions: write' not in WORKFLOW


def test_fixture_checks_move_early_without_duplication_or_gate_removal():
    package = _job('validation-package')
    node_command = ('node --test desktop/browser-cleanup-diagnostic/test-routing.mjs '
                    'desktop/haru-ui-preview/fixture-api.test.cjs '
                    'desktop/haru-ui-preview/visual.test.mjs')
    assert package.count(node_command) == 1
    assert package.index('npm.cmd ci --prefix web') < package.index(node_command)
    assert package.index(node_command) < package.index('node web/tests/desktop-layout/run.mjs')
    assert package.index('node web/tests/desktop-layout/run.mjs') < package.index(
        'uv run --frozen pytest -v tests/test_desktop.py')
    assert package.count('node web/tests/desktop-layout/run.mjs') == 1
    assert 'desktop/ci-evidence/00-fixture-contracts.log' in package
    assert 'finally {\n            Stop-Transcript' in package
    # Only the verified adapter may select reduced validation; package remains full.
    assert "needs.pr-feedback.outputs.mode != 'selected'" in package


def _feedback_adapter():
    import runpy
    return runpy.run_path(str(Path(__file__).parents[1] / 'scripts/windows-pr-feedback.py'))


def _frontend_plan():
    return {
        'schema_version': 1, 'profile': 'dev', 'release_eligible': False,
        'needs_full': False, 'unsupported_checks': [], 'selection_level': 'incremental',
        'risk_expansion': [], 'fallbacks': [], 'deleted_tests': [], 'unclassified': [],
        'required_checks': ['content_diff', 'frontend_build'],
        'selected_tests': [{'id': 'web/src/example.test.ts', 'surface': 'frontend', 'requires': []}],
        'selection_status': 'ready', 'content_basis': None,
        'changes': [{'source': 'committed', 'status': 'M',
                     'paths': ['web/src/theme/tokens.css']}],
    }


@pytest.mark.parametrize(('event', 'draft', 'action', 'expected'), [
    ('pull_request', True, 'opened', True),
    ('pull_request', True, 'synchronize', True),
    ('pull_request', True, 'reopened', True),
    ('pull_request', True, 'converted_to_draft', True),
    ('pull_request', True, 'ready_for_review', False),
    ('pull_request', False, 'synchronize', False),
    ('pull_request', 'true', 'synchronize', False),
    ('pull_request', True, 'edited', False),
    ('workflow_dispatch', True, 'synchronize', False),
    ('push', True, 'synchronize', False),
])
def test_feedback_event_identity_is_narrow(event, draft, action, expected):
    assert _feedback_adapter()['draft_event'](
        event, {'action': action, 'pull_request': {'draft': draft}}) is expected


def test_feedback_accepts_existing_frontend_or_audited_checks_only_not_release():
    decide = _feedback_adapter()['decision']
    value = _frontend_plan()
    assert decide(value)[0] == 'selected'
    value.update(selection_status='checks_only', selected_tests=[],
                 required_checks=['content_diff', 'documentation_links'],
                 content_basis='audited_non_runtime_documentation_paths',
                 changes=[{'source': 'committed', 'status': 'M', 'paths': ['README.md']}])
    assert decide(value)[0] == 'selected'
    value['content_basis'] = None
    assert decide(value)[0] == 'full'


@pytest.mark.parametrize(('field', 'value'), [
    ('schema_version', 2), ('profile', 'merge'), ('release_eligible', True),
    ('needs_full', True), ('unsupported_checks', ['harness_p0']),
    ('selection_level', 'full'), ('selection_level', 'surface_full'),
    ('risk_expansion', [{'id': 'auth'}]), ('fallbacks', [{'surface': 'all'}]),
    ('deleted_tests', ['tests/test_auth.py']), ('unclassified', [{'id': 'unknown'}]),
    ('selection_status', 'blocked'), ('selection_status', 'no_changes'),
    ('selected_tests', []), ('selected_tests', [{'surface': 'backend', 'requires': []}]),
    ('selected_tests', [{'surface': 'frontend', 'requires': ['real_model']}]),
    ('required_checks', ['content_diff', 'harness_p0']),
    ('required_checks', ['content_diff']), ('required_checks', 'frontend_build'),
])
def test_feedback_uncertain_or_broad_plan_requires_native_full(field, value):
    plan = _frontend_plan()
    plan[field] = value
    assert _feedback_adapter()['decision'](plan)[0] == 'full'


@pytest.mark.parametrize('missing', [
    'needs_full', 'unsupported_checks', 'risk_expansion', 'fallbacks', 'unclassified',
    'deleted_tests', 'selected_tests', 'required_checks', 'changes',
])
def test_feedback_missing_mandatory_plan_field_never_reduces_validation(missing):
    plan = _frontend_plan()
    del plan[missing]
    assert _feedback_adapter()['decision'](plan)[0] == 'full'


def test_feedback_non_draft_never_invokes_selector(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('stable/manual candidate must not invoke incremental planning')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    result = _feedback_adapter()['route'](tmp_path, 'workflow_dispatch', {}, '', tmp_path / 'output')
    assert result['mode'] == 'full' and result['release_eligible'] is False


def test_feedback_plan_error_falls_back_but_never_invokes_execution(tmp_path, monkeypatch):
    head, base = 'a' * 40, 'b' * 40
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: head + '\n')
    commands = []
    adapter = _feedback_adapter()
    def failed_plan(root, argv, log):
        commands.append(argv)
        log.write_text('missing metadata')
        return 2
    monkeypatch.setitem(adapter['route'].__globals__, 'run_planner', failed_plan)
    event = {'action': 'synchronize', 'pull_request': {'draft': True, 'base': {'sha': base}}}
    result = adapter['route'](tmp_path, 'pull_request', event, head, tmp_path / 'plan')
    assert result['mode'] == 'full' and 'planning unavailable' in result['reason']
    assert len(commands) == 1 and commands[0][2] == 'plan'
    assert 'execute' not in commands[0]
    assert (tmp_path / 'planning.log').read_text() == 'missing metadata'


def test_feedback_planning_timeout_stops_real_descendants(tmp_path):
    import sys
    import time
    root = Path(__file__).parents[1]
    heartbeat = tmp_path / 'grandchild-heartbeat'
    child = ("import pathlib,time; p=pathlib.Path(" + repr(str(heartbeat)) + "); "
             "exec('while True:\\n p.write_text(str(time.time_ns()))\\n time.sleep(.02)')")
    parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
              + repr(child) + "]); time.sleep(60)")
    with pytest.raises((subprocess.TimeoutExpired, TimeoutError)):
        _feedback_adapter()['run_planner'](
            root, [sys.executable, '-c', parent], tmp_path / 'planner.log', timeout=2)
    assert heartbeat.exists(), 'the real grandchild must have started before timeout'
    stopped = heartbeat.read_bytes()
    time.sleep(.2)
    assert heartbeat.read_bytes() == stopped, 'grandchild kept running after planner timeout'


def test_feedback_unproven_planner_cleanup_does_not_start_full_fallback(tmp_path, monkeypatch):
    adapter = _feedback_adapter()
    head = 'a' * 40
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: head + '\n')
    def unproven(*args, **kwargs):
        raise RuntimeError('planner cleanup unproven; full fallback blocked')
    monkeypatch.setitem(adapter['route'].__globals__, 'run_planner', unproven)
    event = {'action': 'synchronize', 'pull_request': {'draft': True, 'base': {'sha': 'b' * 40}}}
    with pytest.raises(RuntimeError, match='cleanup unproven'):
        adapter['route'](tmp_path, 'pull_request', event, head, tmp_path / 'plan')


def test_selector_output_namespace_is_ignored_without_weakening_snapshot():
    from scripts.test_selection.core import SelectionError, safe_output
    root = Path(__file__).parents[1]
    allowed = root / 'artifacts/test-plans/native-contract-probe'
    assert safe_output(root, allowed) == allowed.resolve()
    with pytest.raises(SelectionError):
        safe_output(root, root / 'artifacts/unreviewed-output')
    with pytest.raises(SelectionError):
        safe_output(root, root / 'web/node_modules/hidden-plan')


def test_selected_workflow_uses_same_environment_and_keeps_full_not_ready():
    feedback = _job('pr-feedback')
    assert feedback.index('uv sync --frozen') < feedback.index('python scripts/windows-pr-feedback.py')
    assert feedback.index('npm.cmd ci --prefix web') < feedback.index('python scripts/windows-pr-feedback.py')
    assert 'uv run --frozen python scripts/select_tests.py execute --plan "$env:SELECTED_PLAN"' in feedback
    assert 'Selected verification failed or incomplete; no success fallback' in feedback
    assert 'LAYOUT_EVIDENCE_DIR: artifacts/test-plans/layout' in feedback
    assert 'artifacts/test-plans/' in feedback
    assert 'continue-on-error' not in WORKFLOW and 'checks: write' not in WORKFLOW
    status = _job('validation-status')
    assert "'Full gate NOT RUN - draft feedback only'" in status
    assert 'full gate NOT RUN / NOT READY' in status
    assert "$env:PACKAGE_RESULT -ne 'success' -or $env:REGRESSION_RESULT -ne 'success'" in status
    assert "if: ${{ always() }}" in status


def test_real_unknown_source_probe_shape_cannot_masquerade_as_fast():
    # Matches the real source-only dry plan: needs_full=false is insufficient.
    value = _frontend_plan()
    value.update(selection_level='surface_full', needs_full=False, unsupported_checks=[],
                 fallbacks=[{'kind': 'fallback', 'path': 'src/offerpilot/selection_unknown_probe.py',
                             'surface': 'backend'}],
                 selected_tests=[{'id': 'tests/test_unknown.py::test_one',
                                  'surface': 'backend', 'requires': []}])
    assert _feedback_adapter()['decision'](value)[0] == 'full'


@pytest.mark.parametrize('path', [
    'web/src/services/chat.ts', 'web/src/services/auth.ts', 'web/src/services/http.ts',
    'web/src/services/pilotRuntime.ts', 'web/src/types/chat.ts',
    'web/src/components/ChatPanel/index.tsx', 'web/src/components/ui/ConfirmationPanel.tsx',
    'web/src/features/desktopUpdates/installSafety.ts',
    'web/src/features/actionPresentation/timeline.ts', 'web/src/layout/AppShell.tsx',
    'web/src/new-unclassified-presentation.tsx',
])
def test_frontend_surface_does_not_authorize_cross_boundary_or_unknown_changes(path):
    if path != 'web/src/new-unclassified-presentation.tsx':
        assert (Path(__file__).parents[1] / path).is_file()
    value = _frontend_plan()
    value['changes'] = [{'source': 'committed', 'status': 'M', 'paths': [path]}]
    assert _feedback_adapter()['decision'](value)[0] == 'full'


@pytest.mark.parametrize('paths', [
    ['web/src/layout/Sidebar.module.css'],
    ['web/src/layout/TopBar.module.css', 'README.md'],
    ['web/src/theme/tokens.css', 'web/src/layout/Sidebar.module.css'],
])
def test_explicit_presentation_paths_and_documentation_union_can_use_dev(paths):
    value = _frontend_plan()
    value['changes'] = [{'source': 'committed', 'status': 'M', 'paths': paths}]
    assert _feedback_adapter()['decision'](value)[0] == 'selected'


def test_renaming_or_mixing_a_risky_frontend_path_never_hides_its_old_identity():
    value = _frontend_plan()
    value['changes'] = [{'source': 'committed', 'status': 'R100',
                         'paths': ['web/src/services/chat.ts', 'web/src/theme/tokens.css']}]
    assert _feedback_adapter()['decision'](value)[0] == 'full'
    value['changes'] = [
        {'source': 'committed', 'status': 'M', 'paths': ['web/src/theme/tokens.css']},
        {'source': 'worktree', 'status': 'M', 'paths': ['web/src/services/auth.ts']},
    ]
    assert _feedback_adapter()['decision'](value)[0] == 'full'


def _command_plan(selected, catalog=None):
    def rows(names):
        return [{'id': name, 'file': name, 'surface': 'frontend'} for name in names]
    return {'selected_tests': rows(selected), 'catalog': rows(selected if catalog is None else catalog)}


def test_frontend_command_omits_only_exact_complete_catalog_filters(tmp_path):
    from scripts.test_selection.execution import frontend_command, WINDOWS_NPM_COMMAND_BUDGET
    names = [f'web/src/components/long_component_name_{n:03d}.test.tsx' for n in range(298)]
    value = _command_plan(names, list(reversed(names)))
    report = tmp_path / '报告 space.json'
    argv = frontend_command(value, report, windows=True)
    assert argv[:3] == ['npm.cmd', 'test', '--']
    assert len(argv) == 8 and argv[-1] == '--outputFile=' + str(report.resolve())
    assert '--maxWorkers=1' in argv and '--minWorkers=1' in argv and '--reporter=json' in argv
    assert len(subprocess.list2cmdline(argv).encode('utf-16-le')) // 2 < WINDOWS_NPM_COMMAND_BUDGET
    assert len(subprocess.list2cmdline(argv + [p.removeprefix('web/') for p in names])) > 8191
    subset = frontend_command(_command_plan(names[:2], names), report, windows=True)
    assert subset[8:] == [name.removeprefix('web/') for name in names[:2]]


@pytest.mark.parametrize('damage', ['empty', 'selected_duplicate', 'catalog_duplicate', 'different',
                                    'missing_catalog', 'noncanonical', 'backend_identity', 'wrong_file', 'option_name'])
def test_frontend_command_refuses_ambiguous_catalog_or_identity(tmp_path, damage):
    from scripts.test_selection.core import SelectionError
    from scripts.test_selection.execution import frontend_command
    value = _command_plan(['web/src/a.test.ts'])
    if damage == 'empty':
        value['selected_tests'] = []
    elif damage == 'selected_duplicate':
        value['selected_tests'] *= 2
    elif damage == 'catalog_duplicate':
        value['catalog'] *= 2
    elif damage == 'different':
        value['catalog'] = _command_plan(['web/src/b.test.ts'])['catalog']
    elif damage == 'missing_catalog':
        del value['catalog']
    elif damage == 'noncanonical':
        value = _command_plan(['web/src/../a.test.ts'])
    elif damage == 'backend_identity':
        value = _command_plan(['tests/test_backend.py'])
    elif damage == 'option_name':
        value = _command_plan(['web/--config=fixture.test.ts'])
    else:
        value['selected_tests'][0]['file'] = 'web/src/other.test.ts'
    with pytest.raises(SelectionError):
        frontend_command(value, tmp_path / 'report.json', windows=True)


@pytest.mark.parametrize('metachar', list('&|<>^%!()') + ['\r', '\n', '"'])
def test_frontend_windows_arguments_refuse_shell_metacharacters(tmp_path, metachar):
    from scripts.test_selection.core import SelectionError
    from scripts.test_selection.execution import frontend_command
    unsafe = f'web/src/unsafe{metachar}name.test.ts'
    with pytest.raises(SelectionError, match='unsafe Windows'):
        frontend_command(_command_plan([unsafe], [unsafe, 'web/src/other.test.ts']),
                         tmp_path / 'report.json', windows=True)
    with pytest.raises(SelectionError, match='unsafe Windows'):
        frontend_command(_command_plan(['web/src/a.test.ts']),
                         tmp_path / f'unsafe{metachar}report.json', windows=True)


def test_frontend_windows_budget_checks_quoted_whole_command_and_full_catalog(tmp_path):
    from scripts.test_selection.core import SelectionError
    from scripts.test_selection.execution import frontend_command
    selected = [f'web/src/long name {n:03d}_' + 'x' * 100 + '.test.ts' for n in range(70)]
    with pytest.raises(SelectionError, match='safe budget'):
        frontend_command(_command_plan(selected, selected + ['web/src/other.test.ts']),
                         tmp_path / 'report.json', windows=True)
    # Even a full-catalog command must reject an overlong output argument.
    with pytest.raises(SelectionError, match='safe budget'):
        frontend_command(_command_plan(['web/src/a.test.ts']),
                         tmp_path / ('report' + 'x' * 6100 + '.json'), windows=True)
    argv = frontend_command(_command_plan(['web/src/报告 space.test.ts'],
                                         ['web/src/报告 space.test.ts', 'web/src/other.test.ts']),
                            tmp_path / '报告 space.json', windows=True)
    assert argv[-1] == 'src/报告 space.test.ts'
    assert '"src/报告 space.test.ts"' in subprocess.list2cmdline(argv)


def test_frontend_command_budget_never_requires_filesystem_resolution(tmp_path):
    from scripts.test_selection.core import SelectionError
    from scripts.test_selection.execution import frontend_command
    class UnresolvablePath(type(tmp_path)):
        def resolve(self, *args, **kwargs):
            raise OSError(206, 'Windows filename too long')
    value = _command_plan(['web/src/a.test.ts'])
    report = UnresolvablePath(tmp_path / 'report.json')
    assert frontend_command(value, report, windows=True)[-1] == '--outputFile=' + str(report)
    with pytest.raises(SelectionError, match='safe budget'):
        frontend_command(value, UnresolvablePath(tmp_path / ('x' * 6100)), windows=True)


def test_frontend_unsafe_command_blocks_before_backend_or_any_check(tmp_path, monkeypatch):
    import json
    from scripts.test_selection import execution
    from scripts.test_selection.core import SelectionError
    value = _command_plan(['web/src/a.test.ts'])
    value.update(plan_id='unsafe-command', selection_status='ready', required_checks=['content_diff'])
    value['selected_tests'].insert(0, {'id': 'tests/test_one.py::test_one', 'surface': 'backend'})
    path = tmp_path / 'selection-plan.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    monkeypatch.setattr(execution, 'check_plan', lambda *args: None)
    def refuse(*args, **kwargs):
        raise SelectionError('Windows npm command exceeds safe budget')
    def never_start(*args, **kwargs):
        raise AssertionError('preflight must reject before any process starts')
    monkeypatch.setattr(execution, 'frontend_command', refuse)
    monkeypatch.setattr(execution, 'ProcessTree', never_start)
    result = execution.execute(tmp_path, path)
    assert result['exit_code'] == 3 and result['needs_full'] is True
    assert result['release_eligible'] is False
    assert result['checks'] == [{'check': 'frontend_command', 'status': 'blocked', 'code': 3,
                                 'reason': 'Windows npm command exceeds safe budget'}]
    assert not (tmp_path / 'execution/backend-ids.json').exists()


def _run_frontend_contract(argv, cwd, log):
    from scripts.evaluation_subject.process_tree import ProcessTree
    with log.open('wb') as stream:
        tree = ProcessTree(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        try:
            try:
                return tree.wait(60)
            except (subprocess.TimeoutExpired, TimeoutError):
                tree.kill()
                tree.wait(10)
                raise
        finally:
            tree.close()


def _save_frontend_contract_evidence(name, mode, *, argv, code, report, hooks, log):
    import json
    destination = os.environ.get('SELECTED_FRONTEND_CONTRACT_EVIDENCE')
    if not destination:
        return
    output = Path(destination) / f'{name}-{mode}'
    output.mkdir(parents=True, exist_ok=True)
    for source in (report, hooks, log):
        shutil.copyfile(source, output / source.name)
    (output / 'summary.json').write_text(json.dumps({
        'argv': argv, 'returncode': code, 'mode': mode, 'platform': os.name,
        'scope': 'tiny contract fixture, not the product frontend suite',
        'release_eligible': False,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@pytest.mark.parametrize('mode', ['all', 'subset', 'failure'])
def test_real_frontend_npm_contract(tmp_path, mode):
    import json
    from scripts.test_selection.execution import frontend_command
    if not shutil.which('npm.cmd' if os.name == 'nt' else 'npm'):
        pytest.skip('native npm unavailable')
    root = tmp_path / 'fixture 空 格'
    web = root / 'web'
    web.mkdir(parents=True)
    scripts = {'pretest': 'node lifecycle.cjs pre', 'test': 'node lifecycle.cjs test',
               'posttest': 'node lifecycle.cjs post'}
    (web / 'package.json').write_text(json.dumps({'name': 'selection-command-contract',
                                                'version': '1.0.0', 'scripts': scripts}), encoding='utf-8')
    (web / 'lifecycle.cjs').write_text('''const fs = require('node:fs');
const phase = process.argv[2];
fs.appendFileSync('lifecycle.jsonl', JSON.stringify({phase, args:process.argv.slice(3), cwd:process.cwd()})+'\\n');
if (phase === 'test') {
 const report = process.argv.find(arg => arg.startsWith('--outputFile='));
 fs.writeFileSync(report.slice('--outputFile='.length), JSON.stringify({args:process.argv.slice(3)}));
 if (fs.existsSync('fail-test')) process.exit(7);
}
''', encoding='utf-8')
    if mode == 'failure':
        (web / 'fail-test').touch()
    all_files = ['web/src/报告 space.test.js', 'web/src/b.test.js']
    selected = all_files[:1] if mode == 'subset' else all_files
    report = root / 'report 空格.json'
    argv = frontend_command(_command_plan(selected, all_files), report)
    code = _run_frontend_contract(argv, web, root / 'npm.log')
    assert code == (7 if mode == 'failure' else 0), (root / 'npm.log').read_text(errors='replace')
    phases = [json.loads(line) for line in (web / 'lifecycle.jsonl').read_text().splitlines()]
    assert [entry['phase'] for entry in phases] == (['pre', 'test'] if mode == 'failure' else ['pre', 'test', 'post'])
    actual = json.loads(report.read_text())['args']
    assert actual == argv[3:]
    assert Path(phases[1]['cwd']).resolve() == web.resolve()
    assert len(argv) == (9 if mode == 'subset' else 8)
    _save_frontend_contract_evidence('npm', mode, argv=argv, code=code, report=report,
                                     hooks=web / 'lifecycle.jsonl', log=root / 'npm.log')


@pytest.mark.parametrize('mode', ['all', 'subset', 'skip', 'failure'])
def test_real_frontend_vitest_contract(tmp_path, mode):
    import json
    from scripts.test_selection.execution import frontend_command, reconcile_frontend
    vitest = Path(__file__).parents[1] / 'web/node_modules/vitest/vitest.mjs'
    available = vitest.is_file() and shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if os.environ.get('REQUIRE_SELECTED_VITEST_CONTRACT') == '1':
        assert available, 'required native selected Vitest contract dependencies are missing'
    elif not available:
        pytest.skip('locked Vitest unavailable; installer runs this contract with dependencies required')
    root = tmp_path / 'vitest 空 格'
    web = root / 'web'
    (web / 'src').mkdir(parents=True)
    package = {'name': 'selected-vitest-contract', 'version': '1.0.0', 'type': 'module',
               'scripts': {'pretest': 'node lifecycle.cjs pre',
                           'test': 'node "' + vitest.resolve().as_posix() + '" run',
                           'posttest': 'node lifecycle.cjs post'}}
    (web / 'package.json').write_text(json.dumps(package), encoding='utf-8')
    (web / 'lifecycle.cjs').write_text(
        "require('node:fs').appendFileSync('lifecycle.txt', process.argv[2]+'\\n');\n", encoding='utf-8')
    (web / 'vitest.config.mjs').write_text(
        "export default {test:{globals:true,include:['src/*.test.js']}};\n", encoding='utf-8')
    first = "test.skip('one', () => expect(1).toBe(1));" if mode == 'skip' else (
        "test('one', () => expect(1).toBe(2));" if mode == 'failure' else
        "test('one', () => expect(1).toBe(1));")
    (web / 'src/one.test.js').write_text(first, encoding='utf-8')
    (web / 'src/two.test.js').write_text("test('two', () => expect(2).toBe(2));", encoding='utf-8')
    catalog = ['web/src/one.test.js', 'web/src/two.test.js']
    selected = catalog[:1] if mode == 'subset' else catalog
    report = root / 'report 空格.json'
    argv = frontend_command(_command_plan(selected, catalog), report)
    code = _run_frontend_contract(argv, web, root / 'vitest.log')
    assert code == (1 if mode == 'failure' else 0), (root / 'vitest.log').read_text(errors='replace')
    data = json.loads(report.read_text(encoding='utf-8'))
    result, details = reconcile_frontend(data, selected, root)
    assert result == (1 if mode == 'failure' else 2 if mode == 'skip' else 0)
    assert details['missing'] == [] and details['extra'] == []
    assert details['executed'] == len(selected)
    assert (web / 'lifecycle.txt').read_text().splitlines() == (
        ['pre'] if mode == 'failure' else ['pre', 'post'])
    _save_frontend_contract_evidence('vitest', mode, argv=argv, code=code, report=report,
                                     hooks=web / 'lifecycle.txt', log=root / 'vitest.log')
    if mode == 'all':
        data['testResults'].pop()
        assert reconcile_frontend(data, selected, root)[0] == 2
