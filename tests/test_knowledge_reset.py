"""KBR-07：一次性离线 Knowledge reset 边界测试（Revision 2）。

批准 Spec：docs/superpowers/specs/2026-07-17-kbr07-one-time-knowledge-reset-design.md

最高测试 seam 是真实 CLI + 临时 data directory + 真实 SQLite Schema + 真实文件。
不覆盖在线并发、写后撤销、私有 helper 白盒、quarantine/manifest/启动恢复协议。
绝不触碰真实 ``$OFFERPILOT_DATA``。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from offerpilot.api import create_app
from offerpilot.config import AIProviderProfile, Config, save_config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW, BRIEF_SCHEMA_VERSION
from offerpilot.knowledge.repository import KnowledgeRepository
from offerpilot.knowledge.reset import (
    COMPLETION_MIGRATION_VERSION,
    KNOWLEDGE_RESET_TABLES,
    KnowledgeResetError,
    reset_knowledge_domain,
)
from offerpilot.models import Application, Conversation

from _knowledge_seam import (
    RoleAwareModelClient,
    build_supported_brief_json,
    drive_brief_queue,
    ingest_and_extract,
)
from conftest import symlink_or_skip

CONTENT = (
    "# 概述\n\n"
    "OfferPilot 使用 SQLite 作为 Knowledge 单一事实源。\n\n"
    "## 第二段\n\n"
    "Evidence 是引用单位，Evidence 不重叠。\n"
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _qualified_config() -> Config:
    provider = AIProviderProfile(
        id="default",
        label="Default",
        provider="openai",
        api_key="sk-test-kbr07",
        base_url="https://example.com",
        model="gpt-test",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    return Config(
        api_key="sk-test-kbr07",
        providers=[provider],
        active_provider_id="default",
        runtime_mode="local",
        confirmation_secret="test-confirmation-secret",
    )


def _seed_knowledge(
    tmp_path: Path, *, config: Config | None = None
) -> tuple[KnowledgeRepository, int, int]:
    """通过正式 Ingest + Extraction 产出一条完整 Knowledge 数据（含文件目录）。"""
    cfg = config or _qualified_config()
    repository, _session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, CONTENT.encode("utf-8"), config=cfg
    )
    return repository, source_id, snapshot_id


def _seed_non_knowledge(tmp_path: Path) -> dict[str, int]:
    """插入非 Knowledge 代表数据，返回用于前后比对的计数摘要。"""
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        session.add(
            Application(
                company_name="ByteDance",
                position_name="Backend",
                status="interview",
            )
        )
        session.add(
            Conversation(title="Pilot 对话", context_type="workspace", context_ref="")
        )
        session.commit()
    return _non_knowledge_counts(tmp_path)


def _non_knowledge_counts(tmp_path: Path) -> dict[str, int]:
    with sqlite3.connect(tmp_path / "data.db") as conn:
        return {
            "applications": conn.execute(
                "SELECT COUNT(*) FROM applications"
            ).fetchone()[0],
            "conversations": conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0],
        }


def _knowledge_counts(tmp_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sqlite3.connect(tmp_path / "data.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table')"
            ).fetchall()
        }
        for table in KNOWLEDGE_RESET_TABLES:
            if table in tables:
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
    return counts


def _knowledge_tables_present(tmp_path: Path) -> set[str]:
    with sqlite3.connect(tmp_path / "data.db") as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table')"
            ).fetchall()
        }
    return {t for t in KNOWLEDGE_RESET_TABLES if t in existing}


def _schema_migrations(tmp_path: Path) -> set[str]:
    with sqlite3.connect(tmp_path / "data.db") as conn:
        return {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }


def _assert_completion_marked(tmp_path: Path) -> None:
    assert COMPLETION_MIGRATION_VERSION in _schema_migrations(tmp_path)


def _assert_knowledge_empty(tmp_path: Path) -> None:
    assert set(_knowledge_counts(tmp_path).values()) == {0}
    knowledge_dir = tmp_path / "knowledge"
    assert knowledge_dir.is_dir()
    assert not knowledge_dir.is_symlink()
    assert not any(knowledge_dir.iterdir())
    assert not (tmp_path / ".knowledge-reset").exists()


def _snapshot_data_dir(tmp_path: Path) -> tuple[dict[str, int], set[str], list[str], str]:
    """门禁拒绝路径用的全量不变性快照：表计数、迁移、相对文件路径、配置原文。"""
    counts = _knowledge_counts(tmp_path)
    migrations = _schema_migrations(tmp_path)
    files = sorted(
        p.relative_to(tmp_path).as_posix()
        for p in tmp_path.rglob("*")
        if p.is_file() or p.is_symlink()
    )
    config_path = tmp_path / "config.json"
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    return counts, migrations, files, config_text


def _run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    from offerpilot.cli import app as cli_app

    return CliRunner().invoke(cli_app, list(args))


# ---------------------------------------------------------------------------
# 1. 主成功路径（真实 CLI）
# ---------------------------------------------------------------------------


def test_cli_reset_main_success_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    non_knowledge_before = _seed_non_knowledge(tmp_path)
    config_before = (tmp_path / "config.json").read_text(encoding="utf-8")
    expected_tables = _knowledge_tables_present(tmp_path)
    expected_migrations = _schema_migrations(tmp_path)
    before_counts = _knowledge_counts(tmp_path)
    assert before_counts["knowledge_sources"] >= 1
    assert before_counts["knowledge_evidence"] >= 1
    assert before_counts["knowledge_evidence_fts"] >= 1
    assert before_counts["knowledge_extraction_snapshots"] >= 1

    legacy = tmp_path / ".knowledge-reset"
    (legacy / "quarantine-old").mkdir(parents=True)
    (legacy / "quarantine-old" / "leftover.md").write_text("stale", encoding="utf-8")

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output
    assert "一次性迁移完成" in result.output or "完成标记" in result.output

    _assert_knowledge_empty(tmp_path)
    _assert_completion_marked(tmp_path)
    assert _non_knowledge_counts(tmp_path) == non_knowledge_before
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == config_before
    assert _knowledge_tables_present(tmp_path) == expected_tables
    after_migrations = _schema_migrations(tmp_path)
    assert expected_migrations.issubset(after_migrations)
    assert after_migrations - expected_migrations == {COMPLETION_MIGRATION_VERSION}
    assert not legacy.exists()


def test_cli_reset_does_not_touch_files_outside_knowledge_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "offerpilot.log"
    log_file.write_text("keep-me", encoding="utf-8")
    outside_marker = tmp_path / "outside-marker.txt"
    outside_marker.write_text("keep", encoding="utf-8")
    similar_name = tmp_path / ".knowledge-reset-userdata"
    similar_name.mkdir()
    (similar_name / "keep.txt").write_text("用户数据", encoding="utf-8")
    similar_knowledge = tmp_path / "knowledge-backup"
    similar_knowledge.mkdir()
    (similar_knowledge / "keep.txt").write_text("backup", encoding="utf-8")

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output

    assert outside_marker.read_text(encoding="utf-8") == "keep"
    assert log_file.read_text(encoding="utf-8") == "keep-me"
    assert (tmp_path / "data.db").exists()
    assert (similar_name / "keep.txt").read_text(encoding="utf-8") == "用户数据"
    assert (similar_knowledge / "keep.txt").read_text(encoding="utf-8") == "backup"
    _assert_knowledge_empty(tmp_path)


def test_cli_reset_missing_dirs_are_empty_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    init_database(tmp_path / "data.db")
    assert not (tmp_path / "knowledge").exists()
    assert not (tmp_path / ".knowledge-reset").exists()

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output
    _assert_knowledge_empty(tmp_path)
    _assert_completion_marked(tmp_path)


# ---------------------------------------------------------------------------
# 2. 门禁（真实 CLI）
# ---------------------------------------------------------------------------


def test_cli_requires_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before = _snapshot_data_dir(tmp_path)

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset")
    assert result.exit_code != 0
    assert "reset_requires_confirm" in result.output
    assert _snapshot_data_dir(tmp_path) == before


def test_cli_refuses_non_local_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    config.runtime_mode = "server"
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before = _snapshot_data_dir(tmp_path)

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code != 0
    assert "reset_not_allowed_in_runtime" in result.output
    assert _snapshot_data_dir(tmp_path) == before


def test_cli_already_completed_refuses_and_protects_new_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)

    ok = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert ok.exit_code == 0, ok.output
    _assert_completion_marked(tmp_path)

    # 完成后重新导入新 Source。
    repository, source_id, snapshot_id = _seed_knowledge(tmp_path, config=config)
    assert source_id > 0
    assert snapshot_id > 0
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    assert evidence
    source_file = tmp_path / "knowledge" / "sources" / str(source_id)
    assert source_file.is_dir()
    before_counts = _knowledge_counts(tmp_path)
    before_snapshot = _snapshot_data_dir(tmp_path)

    again = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert again.exit_code != 0
    assert "reset_already_completed" in again.output
    assert _knowledge_counts(tmp_path) == before_counts
    assert source_file.is_dir()
    assert _snapshot_data_dir(tmp_path) == before_snapshot


# ---------------------------------------------------------------------------
# 3. 专用路径：CLI 不得触发启动恢复
# ---------------------------------------------------------------------------


def test_cli_does_not_invoke_session_factory_or_startup_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功路径与拒绝路径均不得经过 session_factory_for_data_dir / init_database。"""
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)

    calls = {"session_factory": 0, "init_database": 0}

    import offerpilot.cli as cli_module
    import offerpilot.db as db_module

    real_session_factory = db_module.session_factory_for_data_dir
    real_init = db_module.init_database

    def tracking_session_factory(data_dir: Path):  # type: ignore[no-untyped-def]
        calls["session_factory"] += 1
        return real_session_factory(data_dir)

    def tracking_init(db_path: Path):  # type: ignore[no-untyped-def]
        calls["init_database"] += 1
        return real_init(db_path)

    monkeypatch.setattr(db_module, "session_factory_for_data_dir", tracking_session_factory)
    monkeypatch.setattr(db_module, "init_database", tracking_init)
    # CLI 模块若直接 import 了符号，也一并拦截。
    if hasattr(cli_module, "session_factory_for_data_dir"):
        monkeypatch.setattr(
            cli_module, "session_factory_for_data_dir", tracking_session_factory
        )

    # 成功路径。
    ok = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert ok.exit_code == 0, ok.output
    assert calls["session_factory"] == 0
    assert calls["init_database"] == 0
    _assert_completion_marked(tmp_path)

    # 完成后拒绝路径：staging sentinel 不得被启动恢复清理。
    staging = tmp_path / "knowledge" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    sentinel = staging / "sentinel.bin"
    sentinel.write_bytes(b"keep-me")
    before = _snapshot_data_dir(tmp_path)

    again = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert again.exit_code != 0
    assert "reset_already_completed" in again.output
    assert sentinel.exists()
    assert sentinel.read_bytes() == b"keep-me"
    assert _snapshot_data_dir(tmp_path) == before
    assert calls["session_factory"] == 0
    assert calls["init_database"] == 0


# ---------------------------------------------------------------------------
# 4. 路径安全 fail closed（真实 CLI）
# ---------------------------------------------------------------------------


def test_cli_rejects_knowledge_root_symlink_with_external_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before_counts = _knowledge_counts(tmp_path)
    assert before_counts["knowledge_sources"] >= 1

    outside = tmp_path.parent / "kbr07-knowledge-root-outside"
    outside.mkdir(parents=True, exist_ok=True)
    staging_sentinel = outside / "staging" / "sentinel.bin"
    staging_sentinel.parent.mkdir(parents=True, exist_ok=True)
    staging_sentinel.write_bytes(b"staging-keep")
    source_sentinel = outside / "999001" / "secret.txt"
    source_sentinel.parent.mkdir(parents=True, exist_ok=True)
    source_sentinel.write_text("must-not-delete", encoding="utf-8")

    knowledge_dir = tmp_path / "knowledge"
    shutil.rmtree(knowledge_dir)
    knowledge_link = tmp_path / "knowledge"
    symlink_or_skip(knowledge_link, outside, target_is_directory=True)

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code != 0
    assert "reset_path_escape" in result.output
    assert staging_sentinel.read_bytes() == b"staging-keep"
    assert source_sentinel.read_text(encoding="utf-8") == "must-not-delete"
    assert knowledge_link.is_symlink()
    assert _knowledge_counts(tmp_path) == before_counts
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)


def test_cli_rejects_legacy_reset_root_symlink_with_external_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before_counts = _knowledge_counts(tmp_path)

    outside = tmp_path.parent / "kbr07-legacy-reset-outside"
    outside.mkdir(parents=True, exist_ok=True)
    staging_sentinel = outside / "staging" / "sentinel.bin"
    staging_sentinel.parent.mkdir(parents=True, exist_ok=True)
    staging_sentinel.write_bytes(b"legacy-staging-keep")
    source_sentinel = outside / "42" / "secret.txt"
    source_sentinel.parent.mkdir(parents=True, exist_ok=True)
    source_sentinel.write_text("legacy-must-not-delete", encoding="utf-8")

    legacy_link = tmp_path / ".knowledge-reset"
    symlink_or_skip(legacy_link, outside, target_is_directory=True)

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code != 0
    assert "reset_path_escape" in result.output
    assert staging_sentinel.read_bytes() == b"legacy-staging-keep"
    assert source_sentinel.read_text(encoding="utf-8") == "legacy-must-not-delete"
    assert legacy_link.is_symlink()
    assert _knowledge_counts(tmp_path) == before_counts
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)


def test_cli_rejects_knowledge_root_non_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before_counts = _knowledge_counts(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    shutil.rmtree(knowledge_dir)
    knowledge_file = tmp_path / "knowledge"
    knowledge_file.write_text("not-a-dir", encoding="utf-8")

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code != 0
    assert "reset_path_escape" in result.output
    assert knowledge_file.is_file()
    assert knowledge_file.read_text(encoding="utf-8") == "not-a-dir"
    assert _knowledge_counts(tmp_path) == before_counts
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)


def test_cli_does_not_follow_nested_escape_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rmtree 对嵌套 symlink 只 unlink 链接本身，不跟随外部目标。"""
    config = _qualified_config()
    save_config(tmp_path, config)
    _repository, source_id, _snapshot_id = _seed_knowledge(tmp_path, config=config)
    nested_dir = tmp_path / "knowledge" / "sources" / str(source_id)
    assert nested_dir.is_dir()
    outside_file = tmp_path.parent / "kbr07-nested-outside-target.txt"
    outside_file.write_text("nested-must-not-delete", encoding="utf-8")
    symlink_or_skip(nested_dir / "escape-link", outside_file)

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output
    assert outside_file.exists()
    assert outside_file.read_text(encoding="utf-8") == "nested-must-not-delete"
    _assert_knowledge_empty(tmp_path)


# ---------------------------------------------------------------------------
# 5. DB 事务失败 / 文件失败重试
# ---------------------------------------------------------------------------


def test_reset_db_transaction_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before_counts = _knowledge_counts(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    assert knowledge_dir.is_dir()
    before_files = sorted(p.name for p in knowledge_dir.rglob("*") if p.is_file())

    from offerpilot.knowledge import reset as reset_module

    real_execute = reset_module._delete_from_table
    call_count = {"n": 0}

    def failing_delete(conn, table_name: str) -> None:  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated mid-reset failure")
        real_execute(conn, table_name)

    monkeypatch.setattr(reset_module, "_delete_from_table", failing_delete)

    with pytest.raises(Exception):
        reset_knowledge_domain(
            tmp_path,
            runtime_mode="local",
            confirm=True,
        )

    assert _knowledge_counts(tmp_path) == before_counts
    assert knowledge_dir.is_dir()
    after_files = sorted(p.name for p in knowledge_dir.rglob("*") if p.is_file())
    assert after_files == before_files
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)


def test_reset_file_cleanup_failure_then_retry_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    non_knowledge_before = _seed_non_knowledge(tmp_path)

    from offerpilot.knowledge import reset as reset_module

    def failing_clear(data_dir: Path) -> list[str]:
        # 模拟 DB 已提交后、文件只清理一部分：留下 knowledge/sources 残留。
        knowledge_dir = data_dir / "knowledge"
        if knowledge_dir.exists() and knowledge_dir.is_dir():
            leftover = knowledge_dir / "sources"
            leftover.mkdir(parents=True, exist_ok=True)
            (leftover / "partial.bin").write_bytes(b"partial")
        raise KnowledgeResetError(
            "reset_file_cleanup_failed",
            "simulated partial file cleanup failure",
        )

    monkeypatch.setattr(reset_module, "_clear_knowledge_files", failing_clear)

    with pytest.raises(KnowledgeResetError) as first:
        reset_knowledge_domain(
            tmp_path,
            runtime_mode="local",
            confirm=True,
        )
    assert first.value.code == "reset_file_cleanup_failed"
    # DB 已空，文件仍有残留，完成标记未写。
    assert set(_knowledge_counts(tmp_path).values()) == {0}
    assert (tmp_path / "knowledge" / "sources" / "partial.bin").exists()
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)
    assert _non_knowledge_counts(tmp_path) == non_knowledge_before

    # 去掉失败注入后，第二次走真实 CLI 从当前状态继续收敛。
    monkeypatch.undo()
    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output
    _assert_knowledge_empty(tmp_path)
    _assert_completion_marked(tmp_path)
    assert _non_knowledge_counts(tmp_path) == non_knowledge_before


# ---------------------------------------------------------------------------
# 6. reset 后重新导入并完成 Brief v2
# ---------------------------------------------------------------------------


def test_cli_reset_enables_reimport_and_brief_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    _repository, source_id, _snapshot_id = _seed_knowledge(tmp_path, config=config)
    first_source_id = source_id

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output
    _assert_completion_marked(tmp_path)

    # 完成标记存在后，正常 ingest 仍可运行；但再次 reset 会被拒绝。
    repository2, session_factory, second_source_id, second_snapshot_id = (
        ingest_and_extract(tmp_path, CONTENT.encode("utf-8"), config=config)
    )
    assert second_source_id != first_source_id
    assert second_snapshot_id > 0
    evidence = repository2.list_evidence(
        second_source_id, snapshot_id=second_snapshot_id, limit=50
    ).items
    assert evidence

    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    outcome = drive_brief_queue(
        repository2,
        session_factory,
        tmp_path,
        config=config,
        model_client=client,
        source_id=second_source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready"
    assert outcome.brief is not None
    assert outcome.brief.schema_version == BRIEF_SCHEMA_VERSION

    again = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert again.exit_code != 0
    assert "reset_already_completed" in again.output
    assert repository2.get_source(second_source_id) is not None


def test_cli_reset_post_state_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _qualified_config()
    save_config(tmp_path, config)
    repository, _source_id, _snapshot_id = _seed_knowledge(tmp_path, config=config)
    assert repository.list_sources()

    result = _run_cli(tmp_path, monkeypatch, "knowledge", "reset", "--confirm")
    assert result.exit_code == 0, result.output

    assert repository.list_sources() == []
    assert repository.search_evidence("Evidence", limit=5) == []
    with sqlite3.connect(tmp_path / "data.db") as conn:
        pending_running = conn.execute(
            "SELECT COUNT(*) FROM knowledge_jobs WHERE status IN ('pending', 'running')"
        ).fetchone()[0]
    assert pending_running == 0


# ---------------------------------------------------------------------------
# 7. 正常启动恢复仍保留；CLI 不调用它
# ---------------------------------------------------------------------------


def test_startup_recovery_still_cleans_orphan_sources_and_staging(tmp_path: Path) -> None:
    repository, source_id, _snapshot_id = _seed_knowledge(tmp_path)
    source_dir = tmp_path / "knowledge" / "sources" / str(source_id)
    assert source_dir.is_dir()

    orphan = tmp_path / "knowledge" / "sources" / "999999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "leftover.md").write_text("x", encoding="utf-8")
    staging = tmp_path / "knowledge" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "half.bin").write_bytes(b"x")

    init_database(tmp_path / "data.db")

    assert not orphan.exists()
    assert source_dir.exists()  # 有 DB 记录的 Source 目录保留
    if staging.exists():
        assert not any(staging.iterdir())
    assert repository.get_source(source_id) is not None


# ---------------------------------------------------------------------------
# 8. API：reset handler 不存在；请求不能执行清理
# ---------------------------------------------------------------------------


def test_api_reset_handler_absent_and_cannot_execute_cleanup(tmp_path: Path) -> None:
    """路由表中不得存在 reset handler；请求不得产生 reset 结果。

    不锁定全局 fallback 的具体 404/405/200 状态码。
    """
    config = _qualified_config()
    save_config(tmp_path, config)
    _seed_knowledge(tmp_path, config=config)
    before_sources = _knowledge_counts(tmp_path)["knowledge_sources"]
    assert before_sources >= 1

    app = create_app(data_dir=tmp_path)
    routes = []
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None) or set()
        routes.append((path, set(methods) if methods else set()))

    reset_routes = [
        (path, methods)
        for path, methods in routes
        if "knowledge" in path and "reset" in path
    ]
    assert reset_routes == []

    with TestClient(app) as client:
        responses = [
            client.post("/api/knowledge/reset", json={"confirm": True}),
            client.get("/api/knowledge/reset"),
            client.delete("/api/knowledge/reset"),
            client.put("/api/knowledge/reset", json={"confirm": True}),
            client.patch("/api/knowledge/reset", json={"confirm": True}),
        ]
        for response in responses:
            body = response.text
            assert "deleted_source_rows" not in body
            assert "cleared_tables" not in body
            assert "completion_marked" not in body
            # 只要不能执行 reset 即满足契约；不锁定状态码。
            assert response.status_code != 200 or "error" in body.lower()

    # 请求不得清空 Knowledge 或写完成标记（应用启动副作用除外）。
    assert _knowledge_counts(tmp_path)["knowledge_sources"] == before_sources
    assert COMPLETION_MIGRATION_VERSION not in _schema_migrations(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    assert knowledge_dir.is_dir()
    assert any(knowledge_dir.iterdir())
