"""Real Git range routing and required-check failure propagation for mail CI."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/job-mail-ci-scope.py"


@pytest.mark.parametrize(("paths", "expected"), [
    (["README.md", "docs/architecture/job-mail.md"], (False, False)),
    (["plugins/dsh-offerpilot/index.js", "scripts/dsh-market-install-probe.mjs"], (False, False)),
    (["web/src/layout/AppShell.tsx"], (True, False)),
    (["web/src/services/jobMail.ts", "web/package-lock.json"], (True, False)),
    (["src/offerpilot/auth.py"], (True, True)),
    (["src/offerpilot/job_mail/scheduler.py"], (True, True)),
    (["uv.lock"], (True, True)),
    (["tests/test_job_mail_process_lock.py"], (False, True)),
    (["scripts/job-mail-browser-wait.mjs"], (True, False)),
    (["tests/fixtures/job_mail_scheduler_app.py"], (True, False)),
    (["tests/conftest.py", "tests/_offline_network_guard.py"], (True, True)),
    ([".github/workflows/job-mail-assistant.yml"], (True, True)),
    (["scripts/job-mail-ci-scope.py", "tests/test_job_mail_ci_scope.py"], (True, True)),
    (["unknown-new-runtime.toml"], (True, True)),
    ([], (True, True)),
])
def test_change_scopes(paths: list[str], expected: tuple[bool, bool]) -> None:
    classify = runpy.run_path(str(SCRIPT))["classify"]
    assert classify(paths) == expected


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, str, str]:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "ci@example.invalid")
    git(tmp_path, "config", "user.name", "CI fixture")
    (tmp_path / "src/offerpilot").mkdir(parents=True)
    (tmp_path / "src/offerpilot/auth.py").write_text("# original\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("documentation\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "docs")
    return tmp_path, base, git(tmp_path, "rev-parse", "HEAD")


def route(repo: Path, event: dict, event_name: str, head: str) -> dict:
    return runpy.run_path(str(SCRIPT))["route"](repo, event_name, event, head)


@pytest.mark.parametrize("event_name", ["push", "pull_request"])
def test_exact_git_range_can_skip_documentation(history, event_name):
    repo, base, head = history
    event = {"before": base, "after": head, "pull_request": {"base": {"sha": base}}}
    result = route(repo, event, event_name, head)
    assert result["browser"] is False and result["native_lock"] is False
    assert result["base"] == base and result["head"] == head


def test_push_diff_covers_all_commits_not_only_last(history):
    repo, base, _ = history
    (repo / "src/offerpilot/auth.py").write_text("# changed\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "shared runtime")
    (repo / "README.md").write_text("more documentation\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "docs last")
    head = git(repo, "rev-parse", "HEAD")
    assert route(repo, {"before": base, "after": head}, "push", head)["native_lock"] is True


def test_rename_out_of_runtime_still_detects_deleted_source(history):
    repo, base, _ = history
    git(repo, "mv", "src/offerpilot/auth.py", "README-renamed.md")
    git(repo, "commit", "-qm", "rename")
    head = git(repo, "rev-parse", "HEAD")
    result = route(repo, {"before": base, "after": head}, "push", head)
    assert result["browser"] is True and result["native_lock"] is True
    assert "src/offerpilot/auth.py" in result["paths"]


def test_pr_merge_candidate_excludes_changes_already_in_base(history):
    repo, base, feature = history
    git(repo, "checkout", "-qb", "target", base)
    (repo / "src/offerpilot/base_only.py").write_text("# already in target\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "target advances")
    current_base = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--no-ff", feature, "-qm", "merge candidate")
    merge = git(repo, "rev-parse", "HEAD")
    result = route(repo, {"pull_request": {"base": {"sha": current_base}}},
                   "pull_request", merge)
    assert result["paths"] == ["README.md"]
    assert result["browser"] is False and result["native_lock"] is False


def test_unicode_and_spaces_are_not_git_quoted_paths(history):
    repo, base, _ = history
    (repo / "web/src").mkdir(parents=True)
    (repo / "web/src/邮件 设置.ts").write_text("// UI\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "UI")
    head = git(repo, "rev-parse", "HEAD")
    result = route(repo, {"before": base, "after": head}, "push", head)
    assert "web/src/邮件 设置.ts" in result["paths"]
    assert result["browser"] is True and result["native_lock"] is False


@pytest.mark.parametrize("event_name,event", [
    ("workflow_dispatch", {}), ("unknown", {}), ("push", {}),
    ("push", {"before": "0" * 40}), ("push", {"before": "a" * 40}),
    ("pull_request", {"pull_request": {"base": {"sha": "--help"}}}),
])
def test_uncertain_range_runs_all(history, event_name, event):
    repo, _, head = history
    if event_name == "push":
        event = {**event, "after": head}
    result = route(repo, event, event_name, head)
    assert result["browser"] is True and result["native_lock"] is True


def test_head_mismatch_never_skips(history):
    repo, base, head = history
    assert route(repo, {"before": base, "after": base}, "push", head)["browser"] is True


def test_cli_outputs_strict_booleans_and_summary(history, tmp_path):
    repo, base, head = history
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": base, "after": head}))
    output, summary = tmp_path / "output", tmp_path / "summary"
    subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--event-name", "push",
                    "--event-path", str(event), "--head", head, "--output", str(output),
                    "--summary", str(summary)], check=True)
    assert output.read_text() == "browser=false\nnative_lock=false\n"
    assert "not required" in summary.read_text()


@pytest.mark.parametrize("upstream,required,allowed", [
    ("success", "true", True), ("success", "false", True),
    ("success", "", False), ("success", "FALSE", False),
    ("failure", "false", False), ("cancelled", "false", False),
    ("skipped", "false", False), ("", "false", False),
])
def test_required_gate_propagates_failure(upstream, required, allowed):
    # Execute the exact workflow's dependency guard, rather than a test reimplementation.
    workflow = (ROOT / ".github/workflows/job-mail-assistant.yml").read_text(encoding="utf-8")
    import yaml
    job = yaml.safe_load(workflow)["jobs"]["windows-native-lock"]
    guard = job["steps"][0]
    import os
    result = subprocess.run([sys.executable, "-c", guard["run"]],
                            env={**os.environ, "UPSTREAM_RESULT": upstream,
                                 "NATIVE_REQUIRED": required}, capture_output=True)
    assert (result.returncode == 0) is allowed


@pytest.mark.parametrize("browser,native,allowed", [
    ("false", "false", True), ("true", "false", True), ("false", "true", True),
    ("true", "true", True), ("", "false", False), ("true", "", False),
    ("False", "false", False), ("false", "unknown", False),
])
def test_missing_scope_output_cannot_turn_required_work_into_success(browser, native, allowed):
    import os
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/job-mail-assistant.yml")
                              .read_text(encoding="utf-8"))
    guard = workflow["jobs"]["synthetic-acceptance"]["steps"][2]
    result = subprocess.run([sys.executable, "-c", guard["run"]],
                            env={**os.environ, "BROWSER_REQUIRED": browser,
                                 "NATIVE_REQUIRED": native}, capture_output=True)
    assert (result.returncode == 0) is allowed


def test_workflow_preserves_required_entries_and_scopes_expensive_steps():
    import yaml
    source = (ROOT / ".github/workflows/job-mail-assistant.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    # PyYAML's YAML 1.1 treats GitHub's `on` key as True.
    trigger = workflow.get("on", workflow.get(True))
    assert "paths" not in trigger["push"] and "paths" not in trigger["pull_request"]
    jobs = workflow["jobs"]
    assert set(jobs) == {"synthetic-acceptance", "windows-native-lock"}
    synthetic, native = jobs["synthetic-acceptance"], jobs["windows-native-lock"]
    assert "if" not in synthetic
    assert native["if"] == "${{ always() }}"
    assert native["needs"] == "synthetic-acceptance"
    assert "native_lock == 'true'" in native["runs-on"]
    assert "ubuntu-latest" in native["runs-on"]
    heavy = synthetic["steps"][3:]
    for step in heavy:
        assert "steps.scope.outputs.browser == 'true'" in step["if"]
    for step in native["steps"][1:]:
        assert "needs.synthetic-acceptance.outputs.native_lock == 'true'" in step["if"]
    assert "npm test --prefix web" not in source
    assert "uv run mypy" not in source and "uv run ruff" not in source
    assert "tests/test_application_events_repository.py" not in source
    assert "npm run build --prefix web" in source
    assert "node --test scripts/job-mail-browser-wait.test.mjs" in source
    assert "continue-on-error" not in source and "pull_request_target" not in source
    assert source.count("persist-credentials: false") == 2
