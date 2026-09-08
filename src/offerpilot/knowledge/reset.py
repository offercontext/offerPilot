"""KBR-07：一次性离线 Knowledge 数据域迁移（非长期产品能力）。

批准 Spec：``docs/superpowers/specs/2026-07-17-kbr07-one-time-knowledge-reset-design.md``。

能力边界
~~~~~~~~

- 唯一操作界面是本地 CLI ``oc knowledge reset --confirm``。
- 不暴露 HTTP API、前端入口、Agent tool 或启动恢复协议。
- 不调用 ``session_factory_for_data_dir`` / ``init_database`` 等正常应用初始化入口。
- 使用专用最小 SQLite 连接打开已存在的 ``data.db``：不创建库、不迁移、不修复 Schema、不恢复。
- 失败后不恢复旧 Knowledge，只允许重新运行命令继续向空状态收敛。
- 成功后写入一次性完成标记 ``kbr07_one_time_knowledge_reset_complete``；标记存在时永久拒绝再次清空。

执行顺序
~~~~~~~~

1. 解析 data directory 与配置。
2. 检查 ``runtime_mode=local`` 与 ``--confirm``。
3. 检查 ``knowledge/`` 与 ``.knowledge-reset/`` 固定根路径。
4. 通过专用连接检查完成标记；已存在则 ``reset_already_completed``。
5. 单事务 DELETE Knowledge 表闭集并提交。
6. 清理 ``knowledge/`` 与 ``.knowledge-reset/``，重建空 ``knowledge/``。
7. 验证 Knowledge 为空。
8. 独立短事务写完成标记并返回成功。

非 Knowledge 表、Schema 与 AI 配置的不变性由 DELETE 闭集与集成测试保证，
不在运行时做审计快照。
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from offerpilot.config import RuntimeMode

# Knowledge 表族闭集（reset 范围）。删除顺序：先子表后父表。
# 该清单是 reset 边界的唯一事实源；绝不包含非 Knowledge 表。
KNOWLEDGE_RESET_TABLES: tuple[str, ...] = (
    "knowledge_evidence_fts",
    "knowledge_retrieval_traces",
    "knowledge_logs",
    "knowledge_jobs",
    "knowledge_source_briefs",
    "knowledge_brief_attempt_steps",
    "knowledge_brief_attempts",
    "knowledge_evidence",
    "knowledge_source_assets",
    "knowledge_extraction_snapshots",
    "knowledge_source_origins",
    "knowledge_sources",
)

KNOWLEDGE_DIR_NAME = "knowledge"
LEGACY_RESET_DIR_NAME = ".knowledge-reset"
COMPLETION_MIGRATION_VERSION = "kbr07_one_time_knowledge_reset_complete"
COMPLETION_MIGRATION_DESCRIPTION = (
    "KBR-07 one-time offline Knowledge reset completed; refuse further resets"
)


class KnowledgeResetError(Exception):
    """一次性 Knowledge 迁移边界错误。``code`` 供 CLI 稳定映射。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class KnowledgeResetSummary:
    """reset 执行报告（破坏性操作的简要摘要）。"""

    deleted_source_rows: int
    cleared_tables: list[str]
    cleared_dir_entries: list[str]
    completion_marked: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted_source_rows": self.deleted_source_rows,
            "cleared_tables": list(self.cleared_tables),
            "cleared_dir_entries": list(self.cleared_dir_entries),
            "completion_marked": self.completion_marked,
        }


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table')"
        ).fetchall()
    }


def _delete_from_table(conn: sqlite3.Connection, table_name: str) -> None:
    """删除单张 Knowledge 表的全部行。表不存在时由调用方跳过。"""
    conn.execute(f"DELETE FROM {table_name}")


def _count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0]) if row is not None else 0


def _assert_safe_fixed_root(data_dir: Path, target: Path, label: str) -> None:
    """固定清理根必须是 data_dir 下的真实直接子目录，不能是 symlink / 非目录。

    helper 在执行删除前自行检查，不依赖调用方预检。
    """
    if target.is_symlink():
        raise KnowledgeResetError(
            "reset_path_escape",
            f"{label} 是符号链接，拒绝跟随外部目标",
        )
    if not target.exists():
        return
    if not target.is_dir():
        raise KnowledgeResetError(
            "reset_path_escape",
            f"{label} 不是目录，拒绝清理",
        )
    if target.parent.resolve() != data_dir.resolve():
        raise KnowledgeResetError(
            "reset_path_escape",
            f"{label} 不是 data_dir 的直接子路径，拒绝清理",
        )


def _precheck_cleanup_roots(data_dir: Path) -> None:
    """在任何破坏性 DB 操作前预检固定清理根。

    不存在视为空；symlink / 非目录 → ``reset_path_escape``。
    """
    _assert_safe_fixed_root(data_dir, data_dir / KNOWLEDGE_DIR_NAME, KNOWLEDGE_DIR_NAME)
    _assert_safe_fixed_root(
        data_dir, data_dir / LEGACY_RESET_DIR_NAME, LEGACY_RESET_DIR_NAME
    )


def _open_existing_db(data_dir: Path) -> sqlite3.Connection:
    """打开已存在的 data.db：普通文件路径，不创建、不迁移、不修复、不恢复。

    fail closed：
    - 文件不存在 / 不可读
    - 不是有效 SQLite
    - 缺少 schema_migrations
    """
    db_path = data_dir / "data.db"
    if not db_path.exists() or not db_path.is_file():
        raise KnowledgeResetError(
            "reset_database_unavailable",
            f"data.db 不存在或不是普通文件：{db_path}",
        )
    try:
        # 使用普通文件路径，不拼接 file: URI。
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise KnowledgeResetError(
            "reset_database_unavailable",
            f"无法打开 data.db：{exc.__class__.__name__}",
        ) from exc

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations' LIMIT 1"
        ).fetchone()
        if row is None:
            conn.close()
            raise KnowledgeResetError(
                "reset_database_unavailable",
                "data.db 缺少 schema_migrations，拒绝作为未初始化库执行 reset",
            )
    except KnowledgeResetError:
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise KnowledgeResetError(
            "reset_database_unavailable",
            f"data.db 不是可用 SQLite 或读取失败：{exc.__class__.__name__}",
        ) from exc
    return conn


def _completion_mark_exists(conn: sqlite3.Connection) -> bool:
    """读取完成标记。调用方已保证 schema_migrations 存在；SQLite 错误向上传播。"""
    found = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ? LIMIT 1",
        (COMPLETION_MIGRATION_VERSION,),
    ).fetchone()
    return found is not None


def _write_completion_mark(conn: sqlite3.Connection) -> None:
    """在调用方提供的连接上写入一次性完成标记（独立短事务）。"""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, description)
            VALUES (?, ?)
            """,
            (COMPLETION_MIGRATION_VERSION, COMPLETION_MIGRATION_DESCRIPTION),
        )
        conn.commit()
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise KnowledgeResetError(
            "reset_database_unavailable",
            f"写入完成标记失败：{exc.__class__.__name__}",
        ) from exc


def _knowledge_tables_empty(conn: sqlite3.Connection) -> bool:
    existing = _existing_tables(conn)
    for table_name in KNOWLEDGE_RESET_TABLES:
        if table_name in existing and _count_rows(conn, table_name) > 0:
            return False
    return True


def _knowledge_files_empty(data_dir: Path) -> bool:
    """Knowledge 文件是否已收敛到空安全状态。"""
    knowledge_dir = data_dir / KNOWLEDGE_DIR_NAME
    legacy_reset_dir = data_dir / LEGACY_RESET_DIR_NAME

    try:
        if knowledge_dir.exists():
            if knowledge_dir.is_symlink() or not knowledge_dir.is_dir():
                return False
            if any(knowledge_dir.iterdir()):
                return False

        if legacy_reset_dir.exists():
            return False
    except OSError:
        return False

    return True


def _list_top_level_names(target: Path, label: str) -> list[str]:
    try:
        return sorted(child.name for child in target.iterdir())
    except OSError as exc:
        raise KnowledgeResetError(
            "reset_file_cleanup_failed",
            f"列举 {label}/ 失败：{exc.__class__.__name__}",
        ) from exc


def _clear_fixed_directory(data_dir: Path, name: str, *, recreate_empty: bool) -> list[str]:
    """安全清理 data_dir 下的固定直接子路径。

    - 不存在：视为空，不构成错误。
    - symlink / 非目录：``reset_path_escape``。
    - 删除前再次校验；``rmtree`` 对嵌套 symlink 只 unlink 链接本身。
    """
    target = data_dir / name
    _assert_safe_fixed_root(data_dir, target, name)
    if not target.exists():
        if recreate_empty:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise KnowledgeResetError(
                    "reset_file_cleanup_failed",
                    f"创建 {name}/ 失败：{exc.__class__.__name__}",
                ) from exc
            _assert_safe_fixed_root(data_dir, target, name)
        return []

    cleared = _list_top_level_names(target, name)

    # 再次校验后再删除。
    _assert_safe_fixed_root(data_dir, target, name)
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise KnowledgeResetError(
            "reset_file_cleanup_failed",
            f"清理 {name}/ 失败：{exc.__class__.__name__}",
        ) from exc

    if recreate_empty:
        try:
            target.mkdir(parents=True, exist_ok=True)
            _assert_safe_fixed_root(data_dir, target, name)
            if any(target.iterdir()):
                raise KnowledgeResetError(
                    "reset_file_cleanup_failed",
                    f"{name}/ 重建后仍非空",
                )
        except KnowledgeResetError:
            raise
        except OSError as exc:
            raise KnowledgeResetError(
                "reset_file_cleanup_failed",
                f"重建 {name}/ 失败：{exc.__class__.__name__}",
            ) from exc
    return cleared


def _clear_knowledge_files(data_dir: Path) -> list[str]:
    """清理 ``knowledge/`` 与旧 ``.knowledge-reset/``，返回 knowledge 根下曾存在的条目名。"""
    cleared_entries = _clear_fixed_directory(
        data_dir, KNOWLEDGE_DIR_NAME, recreate_empty=True
    )
    # 旧长期 reset 残留：只按固定直接子路径识别，不扫描相似前缀。
    _clear_fixed_directory(data_dir, LEGACY_RESET_DIR_NAME, recreate_empty=False)
    return cleared_entries


def _clear_knowledge_tables(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """在单个 SQLite 事务中清空 Knowledge 表闭集。

    任何删除或 commit 失败都整体回滚；表不存在视为已空。
    """
    deleted_source_rows = 0
    cleared_tables: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing_tables = _existing_tables(conn)
        if "knowledge_sources" in existing_tables:
            deleted_source_rows = _count_rows(conn, "knowledge_sources")
        for table_name in KNOWLEDGE_RESET_TABLES:
            if table_name in existing_tables:
                _delete_from_table(conn, table_name)
                cleared_tables.append(table_name)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    return deleted_source_rows, cleared_tables


def _assert_knowledge_domain_empty(conn: sqlite3.Connection, data_dir: Path) -> None:
    if not _knowledge_tables_empty(conn):
        raise KnowledgeResetError(
            "reset_verification_failed",
            "Knowledge 表清空后验证仍非空，拒绝写入完成标记",
        )
    if not _knowledge_files_empty(data_dir):
        raise KnowledgeResetError(
            "reset_verification_failed",
            "Knowledge 文件清空后验证仍非空，拒绝写入完成标记",
        )


def reset_knowledge_domain(
    data_dir: Path,
    *,
    runtime_mode: RuntimeMode,
    confirm: bool,
) -> KnowledgeResetSummary:
    """执行一次性离线 Knowledge 数据域清空。

    覆盖范围：``KNOWLEDGE_RESET_TABLES`` + 固定路径 ``knowledge/`` 与 ``.knowledge-reset/``。
    保留：数据库 Schema、既有 ``schema_migrations``、AI 配置与全部非 Knowledge 表。

    本函数使用专用 SQLite 连接，绝不调用 ``session_factory_for_data_dir`` /
    ``init_database``，因此不会触发 Schema repair、staging 恢复、Source 删除恢复或
    Job lease 恢复。
    """
    if runtime_mode != "local":
        raise KnowledgeResetError(
            "reset_not_allowed_in_runtime",
            f"runtime_mode={runtime_mode!r} 非本地模式，拒绝执行破坏性 Knowledge reset",
        )
    if not confirm:
        raise KnowledgeResetError(
            "reset_requires_confirm",
            "破坏性 reset 需要显式确认（confirm=True）",
        )

    # 固定根路径预检必须在任何破坏性操作之前。
    _precheck_cleanup_roots(data_dir)

    conn = _open_existing_db(data_dir)
    try:
        try:
            if _completion_mark_exists(conn):
                raise KnowledgeResetError(
                    "reset_already_completed",
                    "KBR-07 一次性 Knowledge reset 已完成，拒绝再次清空",
                )
        except KnowledgeResetError:
            raise
        except sqlite3.Error as exc:
            raise KnowledgeResetError(
                "reset_database_unavailable",
                f"读取完成标记失败：{exc.__class__.__name__}",
            ) from exc

        # DB 先于文件。事务失败整体回滚且不开始文件清理。
        deleted_source_rows, cleared_tables = _clear_knowledge_tables(conn)

        # 文件清理失败：不恢复旧 Knowledge、不写完成标记；重新执行继续收敛。
        cleared_dir_entries = _clear_knowledge_files(data_dir)

        # 写标记前只验证 Knowledge 已空；不审计非 Knowledge / AI 配置。
        _assert_knowledge_domain_empty(conn, data_dir)
        _write_completion_mark(conn)

        return KnowledgeResetSummary(
            deleted_source_rows=deleted_source_rows,
            cleared_tables=cleared_tables,
            cleared_dir_entries=cleared_dir_entries,
            completion_marked=True,
        )
    finally:
        conn.close()
