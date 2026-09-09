from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_python_runtime_not_go_builder():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:" in dockerfile
    assert "uv sync" in dockerfile
    assert "COPY --from=web /web/dist" in dockerfile
    assert "go build" not in dockerfile
    assert "golang:" not in dockerfile
    assert 'ENTRYPOINT ["oc"]' in dockerfile
    assert 'CMD ["start", "--host", "0.0.0.0", "--port", "8080"]' in dockerfile


def test_install_script_installs_python_tool_not_go_binary():
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "uv tool install" in script
    assert "requires Python 3.10+" in script
    assert "go build" not in script
    assert "Go is required" not in script


def test_readme_describes_python_first_runtime():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv sync" in readme
    assert "uv run oc start" in readme
    assert "docker build -t offerpilot ." in readme
    assert "go build" not in readme
    assert "Go 1.22" not in readme


def test_readme_states_the_product_boundary_and_core_capabilities():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for text in [
        "# OfferPilot — 开源、本地优先的 AI 求职与投递管理工具",
        "| 投递管理 |",
        "| 简历与材料准备 |",
        "| 面试准备与模拟 |",
        "| 面试复盘 |",
        "| Offer 对比与谈薪 |",
        "| Pilot AI 助手 |",
        "本地工作区",
        "相关资料会发送给你配置的模型服务",
        "不自动向招聘方投递或发送消息",
        "默认需要用户确认",
        "仍由你决定",
        "AGPLv3",
        "[English](README.en.md)",
    ]:
        assert text in readme
    english = (ROOT / "README.en.md").read_text(encoding="utf-8")
    for text in (
        "local-first",
        "configured model service",
        "does not automatically submit applications or message recruiters",
        "[LICENSE](LICENSE)",
    ):
        assert text in english


def test_docker_smoke_scripts_document_container_smoke_path():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    shell_script = (ROOT / "scripts" / "docker-smoke.sh").read_text(encoding="utf-8")
    powershell_script = (ROOT / "scripts" / "docker-smoke.ps1").read_text(encoding="utf-8")
    assert "COPY --from=web /web/dist /app/web/dist" in dockerfile

    assert "docker build" in shell_script
    assert "docker run" in shell_script
    assert "oc smoke" in shell_script
    assert "/app/web/dist" in shell_script

    assert "docker build" in powershell_script
    assert "docker run" in powershell_script
    assert "oc smoke" in powershell_script
    assert "/app/web/dist" in powershell_script



def test_local_smoke_scripts_exercise_oc_start_with_built_spa():
    shell_script = (ROOT / "scripts" / "local-smoke.sh").read_text(encoding="utf-8")
    powershell_script = (ROOT / "scripts" / "local-smoke.ps1").read_text(encoding="utf-8")
    assert "npm run build" in shell_script
    assert "uv run oc start" in shell_script
    assert "/api/health" in shell_script
    assert "/applications/smoke" in shell_script

    assert "npm.cmd run build" in powershell_script
    assert "uv run oc start" in powershell_script
    assert "/api/health" in powershell_script
    assert "/applications/smoke" in powershell_script



def test_release_gate_scripts_wrap_required_v01_checks():
    powershell_script = (ROOT / "scripts" / "release-gate.ps1").read_text(encoding="utf-8")
    shell_script = (ROOT / "scripts" / "release-gate.sh").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "p0-release-checklist.md").read_text(encoding="utf-8")

    for command in [
        "uv run pytest -q",
        "uv run ruff check .",
        "uv run mypy src",
        "oc verify --profile local",
        "oc verify --profile real-ai",
    ]:
        assert command in powershell_script
        assert command in shell_script

    assert "npm.cmd test" in powershell_script
    assert "npm.cmd run build" in powershell_script
    assert "scripts\\local-smoke.ps1" in powershell_script
    assert "scripts\\docker-smoke.ps1" in powershell_script

    assert "npm test" in shell_script
    assert "npm run build" in shell_script
    assert "scripts/local-smoke.sh" in shell_script
    assert "scripts/docker-smoke.sh" in shell_script

    assert "release-gate.ps1" in checklist
    assert "release-gate.sh" in checklist


def test_install_gate_scripts_cover_source_and_tool_install_paths():
    powershell_script = (ROOT / "scripts" / "install-gate.ps1").read_text(encoding="utf-8")
    shell_script = (ROOT / "scripts" / "install-gate.sh").read_text(encoding="utf-8")
    release_powershell = (ROOT / "scripts" / "release-gate.ps1").read_text(encoding="utf-8")
    release_shell = (ROOT / "scripts" / "release-gate.sh").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "p0-release-checklist.md").read_text(encoding="utf-8")

    assert "uv run oc --help" in powershell_script
    assert "uv tool install --force ." in powershell_script
    assert "UV_TOOL_BIN_DIR" in powershell_script

    assert "uv run oc --help" in shell_script
    assert "uv tool install --force ." in shell_script
    assert "scripts/install.sh --source" in shell_script
    assert "UV_TOOL_BIN_DIR" in shell_script

    assert "install-gate.ps1" in release_powershell
    assert "install-gate.sh" in release_shell
    assert "install-gate.ps1" in checklist
    assert "install-gate.sh" in checklist


def test_p0_release_checklist_documents_browser_product_walkthrough():
    checklist = (ROOT / "docs" / "p0-release-checklist.md").read_text(encoding="utf-8")

    assert "Browser Product Walkthrough" in checklist
    for required_area in [
        "Dashboard",
        "Resumes",
        "Applications",
        "Application events",
        "Pilot",
        "Settings",
        "Interview empty state",
    ]:
        assert required_area in checklist


def test_p0_release_checklist_documents_non_docker_release_gate():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "p0-release-checklist.md").read_text(encoding="utf-8")

    assert "!docs/p0-release-checklist.md" in gitignore
    assert "P0 Release Checklist" in checklist
    assert "Docker: deferred" in checklist
    assert "uv run pytest -q" in checklist
    assert "uv run ruff check ." in checklist
    assert "uv run mypy src" in checklist
    assert "npm.cmd test" in checklist
    assert "npm.cmd run build" in checklist
    assert "scripts/local-smoke.ps1" in checklist
    assert "oc verify --profile local" in checklist
    assert "oc verify --profile real-ai" in checklist
    assert "AGPLv3" in checklist
    assert "schema_migrations" in checklist
    assert "pending actions" in checklist
    assert "LiteLLM" in checklist


def test_go_backend_sources_removed_after_python_cutover():
    go_sources = [
        path
        for path in ROOT.rglob("*.go")
        if ".git" not in path.parts
        and ".venv" not in path.parts
        and ".worktrees" not in path.parts
        and "web" not in path.parts
    ]

    assert go_sources == []
    assert not (ROOT / "go.mod").exists()
    assert not (ROOT / "go.sum").exists()
