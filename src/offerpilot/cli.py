from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Optional

import typer
import uvicorn

from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.workflows import analyze_jd, generate_questions, match_resume
from offerpilot.application_status import APPLICATION_STATUS_IDS, normalize_application_status
from offerpilot.api import create_app
from offerpilot.config import (
    AIProviderProfile,
    Config,
    load_config,
    normalize_runtime_mode,
    resolve_data_dir,
    save_config,
)
from offerpilot.db import session_factory_for_data_dir
from offerpilot.diagnostics import append_log_entry
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.jd import JDAnalysesRepository
from offerpilot.repositories.notes import NoteCreate, NotesRepository
from offerpilot.repositories.offers import OfferCreate, OffersRepository
from offerpilot.repositories.questions import QuestionsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository
from offerpilot.repositories.wakeups import WakeupCreate, WakeupsRepository
from offerpilot.smoke import (
    run_application_jd_smoke,
    run_core_smoke,
    run_http_smoke,
    run_interview_story_smoke,
    run_mock_interview_real_ai_smoke,
    run_offer_negotiation_real_ai_smoke,
)
from offerpilot.skills import SkillRegistryError, register_skill, skills_payload, update_skill

app = typer.Typer(help="OfferPilot - your local job search workbench")
resume_app = typer.Typer(help="Manage resumes")
note_app = typer.Typer(help="Manage interview notes")
offer_app = typer.Typer(help="Manage offers")
question_app = typer.Typer(help="Manage interview questions")
skill_app = typer.Typer(help="Manage trusted skill packages")
wakeup_app = typer.Typer(help="Manage scheduled wakeups")
knowledge_app = typer.Typer(help="Manage Knowledge data domain")

app.add_typer(resume_app, name="resume")
app.add_typer(note_app, name="note")
app.add_typer(offer_app, name="offer")
app.add_typer(question_app, name="question")
app.add_typer(skill_app, name="skill")
app.add_typer(wakeup_app, name="wakeup")
app.add_typer(knowledge_app, name="knowledge")


@app.command()
def add(
    company: str = typer.Option(..., "--company", "-c", help="company name (required)"),
    position: str = typer.Option(..., "--position", help="position/job title (required)"),
    url: str = typer.Option("", "--url", "-u", help="job posting URL"),
    notes: str = typer.Option("", "--notes", "-n", help="notes about this application"),
) -> None:
    repo = _applications_repo()
    created = repo.create(
        ApplicationCreate(
            company_name=company,
            position_name=position,
            job_url=url,
            notes=notes,
            status="applied",
            source="cli",
        )
    )

    typer.echo(f"\nAdded: {created.company_name} - {created.position_name}")
    typer.echo(f"   ID: {created.id}  Status: {created.status}")


@app.command(name="list")
def list_applications(
    status: str = typer.Option(
        "",
        "--status",
        "-s",
        help=f"filter by status ({', '.join(APPLICATION_STATUS_IDS)})",
    ),
) -> None:
    repo = _applications_repo()
    try:
        parsed_status = normalize_application_status(status) if status else ""
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    applications = repo.list(status=parsed_status)
    if not applications:
        typer.echo("\nNo applications found. Use 'oc add' to add one.")
        return

    typer.echo("\nJob Applications")
    typer.echo("-------------------------------------------------------------")
    typer.echo(f"{'ID':<4} {'Company':<20} {'Position':<20} {'Status':<12} {'Applied':<12}")
    typer.echo("-------------------------------------------------------------")
    for item in applications:
        typer.echo(
            f"{str(item.id):<4} {_truncate(item.company_name, 20):<20} "
            f"{_truncate(item.position_name, 20):<20} {item.status:<12} "
            f"{item.applied_at.strftime('%Y-%m-%d'):<12}"
        )
    typer.echo(f"\nTotal: {len(applications)} applications")


@app.command()
def config(
    api_key: Optional[str] = typer.Option(None, "--api-key", help="set API key"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="set base_url"),
    model: Optional[str] = typer.Option(None, "--model", help="set model name"),
    runtime_mode: Optional[str] = typer.Option(None, "--runtime-mode", help="local or server"),
    auth_enabled: Optional[bool] = typer.Option(
        None,
        "--auth/--no-auth",
        help="enable auth guard for server mode",
    ),
    auth_token: Optional[str] = typer.Option(None, "--auth-token", help="set local UI/API auth token"),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="DEBUG, INFO, WARNING, ERROR"),
    auto_approve: Optional[bool] = typer.Option(
        None,
        "--auto-approve/--no-auto-approve",
        help="已弃用：Agent 写操作始终需要逐次人工确认；该参数不会启用自动写入",
    ),
) -> None:
    data_dir = resolve_data_dir()
    current = load_config(data_dir)
    next_config = Config(**current.model_dump())
    changed = False

    if api_key is not None:
        next_config.api_key = api_key
        next_config = _sync_active_provider_config(next_config, api_key=api_key)
        changed = True
    if base_url is not None:
        next_config.base_url = base_url
        next_config = _sync_active_provider_config(next_config, base_url=base_url)
        changed = True
    if model is not None:
        next_config.model = model
        next_config = _sync_active_provider_config(next_config, model=model)
        changed = True
    if auto_approve is not None:
        next_config.chat_auto_approve_writes = False
        changed = True
    if runtime_mode is not None:
        parsed_runtime_mode = normalize_runtime_mode(runtime_mode, next_config.runtime_mode)
        if parsed_runtime_mode != runtime_mode:
            raise typer.BadParameter("--runtime-mode must be local or server")
        next_config.runtime_mode = parsed_runtime_mode
        changed = True
    if auth_enabled is not None:
        next_config.auth_enabled = auth_enabled
        changed = True
    if auth_token is not None:
        next_config.auth_token = auth_token
        changed = True
    if log_level is not None:
        parsed_level = log_level.upper()
        if parsed_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise typer.BadParameter("--log-level must be DEBUG, INFO, WARNING, or ERROR")
        next_config.log_level = parsed_level
        changed = True

    if changed:
        save_config(data_dir, next_config)
        typer.echo(f"Config saved to {data_dir / 'config.json'}")

    _print_config(data_dir, next_config)


@app.command("analyze")
def analyze_command(
    jd: str = typer.Option("", "--jd", "-j", help="JD text to analyze (use '-' to read stdin)"),
    jd_url: str = typer.Option("", "--jd-url", "-u", help="JD page URL to fetch then analyze"),
    app_id: int = typer.Option(0, "--app", "-a", help="linked application ID"),
) -> None:
    jd_text = _read_dash_stdin(jd)
    _validate_cli_jd_input(jd_text, jd_url)
    try:
        result = analyze_jd(
            _build_ai_model(),
            _jd_repo(),
            jd_text=jd_text,
            jd_url=jd_url,
            application_id=app_id if app_id > 0 else None,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    summary = str(result.result.get("summary") or "")
    typer.echo(f"\nJD analysis saved  (id: {result.id}, source: {result.jd_source})")
    if summary:
        typer.echo(f"Summary: {summary}")


@app.command()
def start(
    port: Optional[int] = typer.Option(None, "--port", "-p", help="local server port"),
    host: str = typer.Option("127.0.0.1", "--host", help="server bind host"),
) -> None:
    data_dir = resolve_data_dir()
    cfg = load_config(data_dir)
    resolved_port = port if port is not None else cfg.local_port
    session_factory_for_data_dir(data_dir)
    append_log_entry(data_dir, "INFO", f"server starting on port {resolved_port}")
    typer.echo(f"OfferPilot running at http://localhost:{resolved_port}")
    uvicorn.run(create_app(data_dir=data_dir), host=host, port=resolved_port)


@app.command()
def smoke(
    static_dir: Optional[Path] = typer.Option(None, "--static-dir", help="built frontend dist directory"),
) -> None:
    report = run_core_smoke(resolve_data_dir(), static_dir=static_dir)
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo("Smoke passed")


@app.command()
def verify(
    profile: str = typer.Option("local", "--profile", help="local or real-ai"),
    static_dir: Optional[Path] = typer.Option(None, "--static-dir", help="built frontend dist directory"),
) -> None:
    if profile not in {"local", "real-ai"}:
        raise typer.BadParameter("--profile must be local or real-ai")
    report = run_http_smoke(resolve_data_dir(), static_dir=static_dir, real_ai=profile == "real-ai")
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo(f"Verify {profile} passed")


@app.command("verify-mock-interview")
def verify_mock_interview(
    profile: str = typer.Option(
        "real-ai",
        "--profile",
        help="隔离 Mock API 验收，仅支持 real-ai；不替代完整 verify 或浏览器/CDP 发布证据",
    ),
    static_dir: Optional[Path] = typer.Option(
        None,
        "--static-dir",
        help="built frontend dist directory（隔离 Mock API 验收使用）",
    ),
) -> None:
    if profile != "real-ai":
        raise typer.BadParameter("--profile must be real-ai for Mock Interview verification")
    report = run_mock_interview_real_ai_smoke(resolve_data_dir(), static_dir=static_dir)
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo("隔离 Mock API 验收通过（不替代完整 verify 或浏览器/CDP 发布证据）")


@app.command("verify-offer-negotiation")
def verify_offer_negotiation(
    static_dir: Optional[Path] = typer.Option(
        None,
        "--static-dir",
        help="isolated Offer negotiation API acceptance; not full verify or browser/CDP evidence",
    ),
) -> None:
    report = run_offer_negotiation_real_ai_smoke(resolve_data_dir(), static_dir=static_dir)
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo("Isolated Offer negotiation API acceptance passed")


@app.command("verify-application-jd")
def verify_application_jd(
    profile: str = typer.Option("local", "--profile", help="isolated Application JD contract acceptance"),
    static_dir: Optional[Path] = typer.Option(None, "--static-dir", help="built frontend dist directory"),
) -> None:
    if profile not in {"local", "real-ai"}:
        raise typer.BadParameter("--profile must be local or real-ai")
    report = run_application_jd_smoke(
        resolve_data_dir(), static_dir=static_dir, real_ai=profile == "real-ai"
    )
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo("Isolated Application JD acceptance passed")


@app.command("verify-interview-stories")
def verify_interview_stories(
    profile: str = typer.Option(
        "real-ai",
        "--profile",
        help="isolated Interview Story API verification; does not replace full verify or browser/CDP evidence",
    ),
    static_dir: Optional[Path] = typer.Option(
        None,
        "--static-dir",
        help="built frontend dist directory for isolated Interview Story API verification",
    ),
) -> None:
    if profile not in {"local", "real-ai"}:
        raise typer.BadParameter("--profile must be local or real-ai")
    report = run_interview_story_smoke(
        resolve_data_dir(), static_dir=static_dir, real_ai=profile == "real-ai"
    )
    for step in report.steps:
        typer.echo(f"ok {step.name}: {step.detail}")
    typer.echo("Isolated Interview Story API verification passed; does not replace full verify or browser/CDP evidence")


@knowledge_app.command("reset")
def knowledge_reset(
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="必须显式传入以执行一次性离线 Knowledge 数据域清空",
    ),
) -> None:
    """KBR-07：一次性离线清空 Knowledge 数据域。

    仅本地 runtime 允许。执行前必须停止应用与 Knowledge Worker，并显式传入 ``--confirm``。
    命令使用专用 SQLite 连接，不调用正常应用初始化 / 启动恢复。
    先在单个 SQLite 事务中清空 Knowledge 表，再清理 ``knowledge/`` 与旧
    ``.knowledge-reset/``；验证通过后写入一次性完成标记。完成后永久拒绝再次清空。
    不恢复旧 Knowledge；中断后重新运行同一命令继续向空状态收敛。
    """
    from offerpilot.knowledge.reset import (
        KnowledgeResetError,
        reset_knowledge_domain,
    )

    data_dir = resolve_data_dir()
    cfg = load_config(data_dir)
    typer.echo(
        "前提：请确认应用与 Knowledge Worker 已停止。"
        "本命令是离线一次性迁移，不提供跨进程锁或在线恢复。"
    )
    try:
        # 专用离线路径：不调用 session_factory_for_data_dir / init_database，
        # 因此不会触发 Schema repair、staging、Source 删除或 Job lease 恢复。
        summary = reset_knowledge_domain(
            data_dir,
            runtime_mode=cfg.runtime_mode,
            confirm=confirm,
        )
    except KnowledgeResetError as exc:
        typer.echo(f"[{exc.code}] {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(
        "Knowledge 一次性迁移完成："
        f"删除 Source {summary.deleted_source_rows} 条，"
        f"清表 {len(summary.cleared_tables)} 张，"
        f"清理 knowledge 条目 {','.join(summary.cleared_dir_entries) or '(空)'}；"
        f"完成标记={summary.completion_marked}。"
    )


@app.command("knowledge-acceptance")
def knowledge_acceptance(
    fixtures_dir: Optional[Path] = typer.Option(
        None,
        "--fixtures-dir",
        help="真实 Source fixtures 目录（含 manifest.json 与 queries.json）",
    ),
    profile: str = typer.Option(
        "v1",
        "--profile",
        help=(
            "验收 profile：v1（默认，无 AI Provider，纯 Source/Evidence/检索/回读/edge/bundle）"
            "或 brief（V1.1 候选，额外运行 Brief pass rate 与故障场景）"
        ),
    ),
    real_ai: bool = typer.Option(
        False,
        "--real-ai",
        help="brief profile 下使用真实 litellm Provider 完成 Brief 验收并记录模型/耗时/费用",
    ),
    report_path: Optional[Path] = typer.Option(
        None, "--report", help="将安全验收报告写到 JSON 文件"
    ),
) -> None:
    """KV1-03 / KI-11：对真实 Source 与检索质量做一次性硬门禁验收。

    默认 ``v1`` profile（ADR-0002）：无 AI Provider，只验证 Imported Source / Extraction /
    Evidence / FTS / 搜索 / 回读 / 状态 / edge / bundle，不创建 Brief Job、不调用模型。
    ``brief`` profile（V1.1 候选）额外运行 Brief pass rate 与故障场景，默认用 stub Provider，
    ``--real-ai`` 接真实 Provider。任一硬门禁失败时进程非零退出，并输出可定位 bad case 的 Evidence ID。
    """
    from offerpilot.knowledge.acceptance import run_acceptance

    data_dir = resolve_data_dir()
    if fixtures_dir is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        fixtures_dir = repo_root / "tests" / "fixtures" / "knowledge"
    if not fixtures_dir.exists():
        raise typer.BadParameter(
            f"fixtures 目录不存在：{fixtures_dir}；请用 --fixtures-dir 指定"
        )
    run_data_dir = data_dir / "acceptance-run"
    run_data_dir.mkdir(parents=True, exist_ok=True)

    if profile == "v1":
        # KV1-03：V1 无 Provider，纯 Source/Evidence；不创建 Brief Job、不调用模型。
        if real_ai:
            raise typer.BadParameter("--real-ai 仅适用于 --profile brief")
        cfg = Config()
        acceptance_report = run_acceptance(
            fixtures_dir=fixtures_dir,
            data_dir=run_data_dir,
            config=cfg,
            enable_brief=False,
            enable_brief_failure_scenarios=False,
        )
    elif profile == "brief":
        # brief profile（V1.1 候选）：Brief pass rate + 故障场景。默认 stub，--real-ai 真实。
        model_client = None
        real_mode = False
        if real_ai:
            from offerpilot.knowledge.provider import build_knowledge_brief_provider_client

            cfg = load_config(data_dir)
            model_client = build_knowledge_brief_provider_client().complete_once
            real_mode = True
        else:
            # stub 模式：注入合格 stub Provider，Brief 用 perfect stub model_client
            # 覆盖全部门禁逻辑，不访问网络、不产生真实费用。
            from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW

            stub_provider = AIProviderProfile(
                id="acceptance-stub",
                label="Acceptance Stub",
                provider="openai",
                api_key="sk-acceptance-stub",
                base_url="https://example.com",
                model="gpt-acceptance-stub",
                enabled=True,
                context_window=BRIEF_MIN_CONTEXT_WINDOW,
                max_output_tokens=4096,
            )
            cfg = Config(
                api_key="sk-acceptance-stub",
                providers=[stub_provider],
                active_provider_id="acceptance-stub",
            )
        acceptance_report = run_acceptance(
            fixtures_dir=fixtures_dir,
            data_dir=run_data_dir,
            config=cfg,
            model_client=model_client,
            real_mode=real_mode,
        )
    else:
        raise typer.BadParameter(
            f"未知 profile：{profile}；支持 v1（默认）或 brief"
        )

    payload = json.dumps(acceptance_report.to_safe_json(), ensure_ascii=False, indent=2)
    if report_path is not None:
        report_path.write_text(payload, encoding="utf-8")
    typer.echo(payload)
    if not acceptance_report.passed:
        raise typer.Exit(code=1)


@skill_app.command("list")
def skill_list() -> None:
    cfg = load_config(resolve_data_dir())
    payload = skills_payload(cfg)
    packages = payload["packages"]
    if not packages:
        typer.echo("\nNo skill packages registered.")
        return
    typer.echo("\nSkill Packages")
    typer.echo("-------------------------------------------------------------")
    typer.echo(f"{'ID':<24} {'Trusted':<8} {'Enabled':<8} {'State':<8} Source")
    for package in packages:
        state = "loaded" if package["loaded"] else "inactive"
        typer.echo(
            f"{package['id']:<24} {_format_bool(package['trusted']):<8} "
            f"{_format_bool(package['enabled']):<8} {state:<8} {package['source']}"
        )


@skill_app.command("add")
def skill_add(
    skill_id: Optional[str] = typer.Option(None, "--id", help="stable skill id"),
    label: str = typer.Option("", "--label", help="display label"),
    source: str = typer.Option("", "--source", help="local path, package URL, or registry source"),
    version: str = typer.Option("", "--version", help="skill version"),
    description: str = typer.Option("", "--description", help="skill description"),
    entrypoint: str = typer.Option("", "--entrypoint", help="manifest entrypoint"),
    manifest: Optional[Path] = typer.Option(None, "--manifest", help="skill manifest JSON file"),
) -> None:
    cfg = load_config(resolve_data_dir())
    payload: dict[str, object] = {
        "id": skill_id or "",
        "label": label,
        "source": source,
        "version": version,
        "description": description,
        "entrypoint": entrypoint,
    }
    if manifest is not None:
        payload["manifest"] = _read_json_object(manifest, "--manifest")
    try:
        next_config = register_skill(cfg, payload)
    except SkillRegistryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    save_config(resolve_data_dir(), next_config)
    registered = next_config.skills[-1]
    typer.echo(f"Skill registered: {registered.id}")


@skill_app.command("trust")
def skill_trust(skill_id: str = typer.Argument(...)) -> None:
    _set_skill_state(skill_id, trusted=True)
    typer.echo(f"Skill trusted: {skill_id}")


@skill_app.command("enable")
def skill_enable(skill_id: str = typer.Argument(...)) -> None:
    _set_skill_state(skill_id, enabled=True)
    typer.echo(f"Skill enabled: {skill_id}")


@skill_app.command("disable")
def skill_disable(skill_id: str = typer.Argument(...)) -> None:
    _set_skill_state(skill_id, enabled=False)
    typer.echo(f"Skill disabled: {skill_id}")


@wakeup_app.command("add")
def wakeup_add(
    kind: str = typer.Option(..., "--kind", help="wakeup kind"),
    due_at: str = typer.Option(..., "--due-at", help="RFC3339 due time"),
    payload_json: str = typer.Option("{}", "--payload-json", help="JSON object payload"),
) -> None:
    parsed_due_at = _parse_cli_datetime(due_at)
    payload = _parse_cli_payload(payload_json)
    wakeup = _wakeups_repo().create(WakeupCreate(kind=kind, due_at=parsed_due_at, payload=payload))
    typer.echo(f"Wakeup scheduled #{wakeup.id}: {wakeup.kind} at {wakeup.due_at.isoformat()}")


@wakeup_app.command("list")
def wakeup_list(status: str = typer.Option("", "--status", help="filter by status")) -> None:
    rows = _wakeups_repo().list_wakeups(status=status)
    if not rows:
        typer.echo("\nNo wakeups scheduled.")
        return
    typer.echo("\nWakeups")
    typer.echo("-------------------------------------------------------------")
    typer.echo(f"{'ID':<4} {'Kind':<16} {'Status':<12} Due")
    for row in rows:
        typer.echo(f"{row.id:<4} {_truncate(row.kind, 16):<16} {row.status:<12} {row.due_at.isoformat()}")


@wakeup_app.command("dispatch-due")
def wakeup_dispatch_due(
    now: Optional[str] = typer.Option(None, "--now", help="RFC3339 clock override"),
    limit: int = typer.Option(25, "--limit", help="maximum wakeups to dispatch"),
) -> None:
    now_dt = _parse_cli_datetime(now) if now else datetime.now(timezone.utc)
    rows = _wakeups_repo().dispatch_due(now_dt, limit=limit)
    typer.echo(f"Dispatched {len(rows)} wakeup{'s' if len(rows) != 1 else ''}")


@resume_app.command("add")
def resume_add(
    file: Path = typer.Option(..., "--file", "-f", help="path to resume text/markdown file"),
    name: str = typer.Option("", "--name", "-n", help="optional resume name"),
) -> None:
    text = file.read_text(encoding="utf-8")
    created = _resumes_repo().create(
        ResumeCreate(
            name=name,
            file_path=str(file),
            parsed_data=text,
            parse_status="text-ready",
        )
    )
    typer.echo(f"\nResume saved  (id: {created.id}, name: {created.name!r}, {len(text)} chars)")


@resume_app.command("list")
def resume_list() -> None:
    rows = _resumes_repo().list()
    if not rows:
        typer.echo("\nNo resumes yet. Use `oc resume add --file path/to/resume.txt`.")
        return
    typer.echo("\nResumes")
    typer.echo("--------------------------------------------------------")
    typer.echo(f"{'ID':<4} {'Name':<20} {'Status':<12} {'Chars':<12}")
    for row in rows:
        name = row.name or "(unnamed)"
        typer.echo(f"{row.id:<4} {_truncate(name, 20):<20} {row.parse_status:<12} {len(row.parsed_data):<12}")


@resume_app.command("match")
def resume_match(
    resume_id: int = typer.Option(..., "--resume", "-r", help="resume ID"),
    jd: str = typer.Option("", "--jd", "-j", help="JD text to match (use '-' to read stdin)"),
    jd_url: str = typer.Option("", "--jd-url", "-u", help="JD page URL to fetch then match"),
    app_id: int = typer.Option(0, "--app", "-a", help="linked application ID"),
) -> None:
    jd_text = _read_dash_stdin(jd)
    _validate_cli_jd_input(jd_text, jd_url)
    try:
        result = match_resume(
            _build_ai_model(),
            _resumes_repo(),
            resume_id=resume_id,
            jd_text=jd_text,
            jd_url=jd_url,
            application_id=app_id if app_id > 0 else None,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    score = result.result.get("match_score")
    typer.echo(f"\nResume match saved  (id: {result.id}, resume: {result.resume_id})")
    if score is not None:
        typer.echo(f"Score: {score}")
    summary = str(result.result.get("summary") or "")
    if summary:
        typer.echo(f"Summary: {summary}")


@note_app.command("add")
def note_add(
    app_id: int = typer.Option(..., "--app", "-a", help="application ID to link"),
    company: str = typer.Option("", "--company", help="company name"),
    position: str = typer.Option("", "--position", help="position name"),
    round: str = typer.Option("", "--round", "-r", help="interview round"),
    date: str = typer.Option("", "--date", help="interview date"),
    questions: str = typer.Option("", "--questions", "-q", help="interview questions"),
    reflection: str = typer.Option("", "--reflection", "-f", help="self reflection"),
    difficulty: str = typer.Option("", "--difficulty", help="difficult points"),
    mood: str = typer.Option("", "--mood", help="mood"),
) -> None:
    applications = _applications_repo()
    app_model = applications.get(app_id)
    if app_model is None:
        raise typer.BadParameter(f"application #{app_id} not found")
    company = company or app_model.company_name
    position = position or app_model.position_name
    created = _notes_repo().create(
        NoteCreate(
            application_id=app_id,
            company=company,
            position=position,
            round=round,
            date=date,
            questions=questions,
            self_reflection=reflection,
            difficulty_points=difficulty,
            mood=mood,
        )
    )
    typer.echo(f"\nNote saved  (id: {created.id}, {company} - {position} - {round})")


@note_app.command("list")
def note_list(
    app_id: int = typer.Option(0, "--app", "-a", help="filter by application ID"),
) -> None:
    rows = _notes_repo().list(application_id=app_id)
    if not rows:
        typer.echo("\nNo interview notes.")
        return
    typer.echo("\nInterview Notes")
    typer.echo("-------------------------------------------------------------")
    for row in rows:
        typer.echo(f"#{row.id}  {row.company} - {row.position} - {row.round} - {row.date} - mood:{row.mood}")
        if row.questions:
            typer.echo(f"   Questions: {_truncate(row.questions, 60)}")
        if row.self_reflection:
            typer.echo(f"   Reflection: {_truncate(row.self_reflection, 60)}")
        if row.difficulty_points:
            typer.echo(f"   Difficulty: {_truncate(row.difficulty_points, 60)}")


@offer_app.command("add")
def offer_add(
    company: str = typer.Option("", "--company", "-c", help="company name"),
    position: str = typer.Option("", "--position", help="position name"),
    app_id: int = typer.Option(0, "--app", "-a", help="linked application ID"),
    base: int = typer.Option(0, "--base", help="monthly base salary"),
    months: int = typer.Option(12, "--months", help="months per year"),
    signing: int = typer.Option(0, "--signing", help="signing bonus"),
    equity: str = typer.Option("", "--equity", help="equity description"),
    perks: str = typer.Option("", "--perks", help="perks description"),
    deadline: str = typer.Option("", "--deadline", help="offer deadline"),
    notes: str = typer.Option("", "--notes", "-n", help="notes"),
) -> None:
    if base < 0 or signing < 0:
        raise typer.BadParameter("--base and --signing must be non-negative")
    if months < 1:
        raise typer.BadParameter("--months must be at least 1")
    application_id = app_id if app_id > 0 else None
    if application_id is not None:
        app_model = _applications_repo().get(application_id)
        if app_model is not None:
            company = company or app_model.company_name
            position = position or app_model.position_name
    if not company or not position:
        raise typer.BadParameter("--company and --position are required")
    created = _offers_repo().create(
        OfferCreate(
            application_id=application_id,
            company_name=company,
            position_name=position,
            base_monthly=base,
            months_per_year=months,
            signing_bonus=signing,
            equity=equity,
            perks=perks,
            deadline=deadline,
            notes=notes,
        )
    )
    typer.echo(
        f"\nOffer added: {created.company_name} - {created.position_name} "
        f"({created.base_monthly}x{created.months_per_year} + {created.signing_bonus}, total {created.total_cash})"
    )


@offer_app.command("list")
def offer_list(status: str = typer.Option("", "--status", "-s", help="filter by status")) -> None:
    rows = _offers_repo().list(status=status)
    if not rows:
        typer.echo("\nNo offers found. Use 'oc offer add' to add one.")
        return
    typer.echo("\nOffers")
    typer.echo("--------------------------------------------------------------")
    typer.echo(f"{'ID':<4} {'Company':<16} {'Position':<14} {'Status':<12} {'BasexM':<10} {'Total':<10}")
    for row in rows:
        typer.echo(
            f"{row.id:<4} {_truncate(row.company_name, 16):<16} {_truncate(row.position_name, 14):<14} "
            f"{row.status:<12} {str(row.base_monthly) + 'x' + str(row.months_per_year):<10} {row.total_cash:<10}"
        )


@offer_app.command("update")
def offer_update(
    offer_id: int = typer.Argument(...),
    status: Optional[str] = typer.Option(None, "--status", help="new status"),
    base: Optional[int] = typer.Option(None, "--base", help="monthly base salary"),
    months: Optional[int] = typer.Option(None, "--months", help="months per year"),
    signing: Optional[int] = typer.Option(None, "--signing", help="signing bonus"),
) -> None:
    repo = _offers_repo()
    existing = repo.get(offer_id)
    if existing is None:
        raise typer.BadParameter("offer not found")
    next_months = months if months is not None else existing.months_per_year
    if next_months < 1:
        raise typer.BadParameter("months must be at least 1")
    updated = repo.update(
        offer_id,
        OfferCreate(
            application_id=existing.application_id,
            company_name=existing.company_name,
            position_name=existing.position_name,
            status=status if status is not None else existing.status,
            base_monthly=base if base is not None else existing.base_monthly,
            months_per_year=next_months,
            signing_bonus=signing if signing is not None else existing.signing_bonus,
            equity=existing.equity,
            perks=existing.perks,
            deadline=existing.deadline,
            notes=existing.notes,
            assessment=existing.assessment,
        ),
    )
    if updated is None:
        raise typer.BadParameter("offer not found")
    typer.echo(f"\nOffer #{offer_id} updated (status {updated.status}, total {updated.total_cash})")


@offer_app.command("delete")
def offer_delete(offer_id: int = typer.Argument(...)) -> None:
    _offers_repo().delete(offer_id)
    typer.echo(f"\nOffer #{offer_id} deleted")


@offer_app.command("compare")
def offer_compare(ids: str = typer.Argument(...)) -> None:
    repo = _offers_repo()
    rows = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        offer = repo.get(int(raw))
        if offer is not None:
            rows.append(offer)
    if not rows:
        typer.echo("\nNo matching offers.")
        return
    typer.echo("\nOffer Compare")
    typer.echo("--------------------------------------------------------------")
    for row in rows:
        typer.echo(f"#{row.id} {row.company_name} - {row.position_name}: total {row.total_cash}")


@question_app.command("list")
def question_list(
    status: str = typer.Option("", "--status", help="filter by status"),
    topic: str = typer.Option("", "--topic", help="filter by topic"),
) -> None:
    rows = _questions_repo().list(status=status, topic=topic)
    if not rows:
        typer.echo("\nNo questions yet. Try: oc question generate --topic system-design")
        return
    typer.echo("\nQuestion Bank")
    typer.echo("-------------------------------------------------------------")
    for row in rows:
        typer.echo(
            f"#{row.id} [{row.category}/{row.difficulty}] {_truncate(row.question, 60)} "
            f"- status:{row.status} practice:{row.practice_count}"
        )


@question_app.command("generate")
def question_generate(
    source: str = typer.Option("notes", "--source", "-s", help="notes source"),
    topic: str = typer.Option("", "--topic", help="topic label for generated questions"),
    app_id: int = typer.Option(0, "--app", "-a", help="application ID for notes source"),
    count: int = typer.Option(8, "--count", "-n", help="number of questions to generate"),
) -> None:
    try:
        result = generate_questions(
            _build_ai_model(),
            _questions_repo(),
            _notes_repo(),
            source=source,
            topic=topic,
            application_id=app_id,
            count=count,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"\nGenerated {result.count} questions  (skipped duplicates: {result.skipped})")
    for question in result.questions:
        typer.echo(f"#{question.id} [{question.category}/{question.difficulty}] {question.question}")


def main() -> None:
    app()


def _applications_repo() -> ApplicationsRepository:
    return ApplicationsRepository(session_factory_for_data_dir(resolve_data_dir()))


def _jd_repo() -> JDAnalysesRepository:
    return JDAnalysesRepository(session_factory_for_data_dir(resolve_data_dir()))


def _resumes_repo() -> ResumesRepository:
    return ResumesRepository(session_factory_for_data_dir(resolve_data_dir()))


def _notes_repo() -> NotesRepository:
    return NotesRepository(session_factory_for_data_dir(resolve_data_dir()))


def _offers_repo() -> OffersRepository:
    return OffersRepository(session_factory_for_data_dir(resolve_data_dir()))


def _questions_repo() -> QuestionsRepository:
    return QuestionsRepository(session_factory_for_data_dir(resolve_data_dir()))


def _wakeups_repo() -> WakeupsRepository:
    return WakeupsRepository(session_factory_for_data_dir(resolve_data_dir()))


def _build_ai_model() -> ConfiguredAIClient:
    return ConfiguredAIClient(load_config(resolve_data_dir()))


def _read_dash_stdin(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return value


def _validate_cli_jd_input(jd_text: str, jd_url: str) -> None:
    if jd_url:
        if not jd_text.strip():
            typer.echo(
                json.dumps(
                    {
                        "code": "jd_text_required",
                        "error": "jd_text is required; jd_url is record-only",
                    },
                    ensure_ascii=False,
                )
            )
            raise typer.Exit(code=2)
        typer.echo(
            json.dumps(
                {"code": "jd_url_not_supported", "error": "jd_url is record-only"},
                ensure_ascii=False,
            )
        )
        raise typer.Exit(code=2)
    if not jd_text.strip():
        raise typer.BadParameter("jd_text_required")


def _print_config(data_dir: Path, cfg: Config) -> None:
    active = cfg.active_provider()
    typer.echo("\nOfferPilot Configuration")
    typer.echo("---------------------------")
    typer.echo(f"Config file: {data_dir / 'config.json'}")
    typer.echo(f"  provider : {active.provider}")
    typer.echo(f"  base_url : {active.base_url}")
    typer.echo(f"  model    : {active.model}")
    if active.api_key:
        typer.echo(f"  api_key  : {_mask_key(active.api_key)}")
    else:
        typer.echo("  api_key  : (not set - AI features will return an error)")
    typer.echo(f"  local_port: {cfg.local_port}")
    typer.echo(f"  runtime_mode: {cfg.runtime_mode}")
    typer.echo(f"  auth_enabled: {_format_bool(cfg.auth_enabled)}")
    if cfg.auth_token:
        typer.echo(f"  auth_token: {_mask_key(cfg.auth_token)}")
    else:
        typer.echo("  auth_token: (not set)")
    typer.echo(f"  log_level: {cfg.log_level}")
    typer.echo(f"  ai_auto_approve: {_format_bool(cfg.chat_auto_approve_writes)}")


def _sync_active_provider_config(
    cfg: Config,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> Config:
    active = cfg.active_provider()
    providers = []
    for profile in cfg.provider_profiles():
        if profile.id == active.id:
            providers.append(
                profile.model_copy(
                    update={
                        "api_key": api_key if api_key is not None else profile.api_key,
                        "base_url": base_url if base_url is not None else profile.base_url,
                        "model": model if model is not None else profile.model,
                    }
                )
            )
        else:
            providers.append(profile)
    if not providers:
        providers = [
            AIProviderProfile(
                id=active.id,
                label=active.label,
                provider=active.provider,
                api_key=api_key if api_key is not None else active.api_key,
                base_url=base_url if base_url is not None else active.base_url,
                model=model if model is not None else active.model,
                enabled=active.enabled,
            )
        ]
    return cfg.model_copy(update={"providers": providers})


def _set_skill_state(
    skill_id: str,
    *,
    trusted: bool | None = None,
    enabled: bool | None = None,
) -> None:
    data_dir = resolve_data_dir()
    payload = {}
    if trusted is not None:
        payload["trusted"] = trusted
    if enabled is not None:
        payload["enabled"] = enabled
    try:
        next_config = update_skill(load_config(data_dir), skill_id, payload)
    except KeyError as exc:
        raise typer.BadParameter("skill not found") from exc
    except SkillRegistryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    save_config(data_dir, next_config)


def _parse_cli_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter("must be RFC3339") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_cli_payload(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--payload-json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--payload-json must be a JSON object")
    return parsed


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"{label} cannot be read") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{label} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{label} must be a JSON object")
    return parsed


def _mask_key(value: str) -> str:
    if len(value) <= 6:
        return "******"
    return value[:4] + "****" + value[-2:]


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _truncate(value: str, limit: int) -> str:
    if len(value) > limit:
        return value[: limit - 1] + "..."
    return value
