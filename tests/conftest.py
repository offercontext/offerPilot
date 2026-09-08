"""Pytest 共享配置。

KBR-01：存在 conftest.py 后，pytest 会把 ``tests/`` 加入 ``sys.path``，使
``tests/_knowledge_seam.py`` 这类共享测试 helper 可被各测试文件直接导入。

``app_client`` fixture 用 ``with TestClient(...)`` 触发 FastAPI startup，
``KnowledgeWorkerRuntime`` 后台线程消费 extraction/brief queue，匹配异步产品行为。
上传后 Source 初始为 pending；测试调用 ``wait_for_extraction`` 等待 Source 达到
extracted 再验证 Evidence/搜索/结构等 extraction 产物。
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from offerpilot.api import create_app


def symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """Create a symlink, skipping only when the host lacks symlink capability."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if (
            (os.name == "nt" and getattr(exc, "winerror", None) == 1314)
            or exc.errno in {errno.EACCES, errno.EPERM}
        ):
            pytest.skip("当前环境没有创建符号链接的权限")
        raise


@pytest.fixture
def app_client(tmp_path: Path):
    """启动真实 app（含后台 KnowledgeWorkerRuntime 消费 extraction/brief queue）。"""
    with TestClient(create_app(data_dir=tmp_path)) as client:
        yield client


def wait_for_extraction(
    client: TestClient,
    source_id: int,
    *,
    timeout: float = 5.0,
    interval: float = 0.02,
) -> dict:
    """Poll ``GET /api/knowledge/sources/{id}`` 直到 extraction 完成。

    返回最终 source payload。extraction 对小测试内容是毫秒级，后台 worker 通常在
    首次 poll 即完成；timeout 仅作安全上限。
    """
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/knowledge/sources/{source_id}")
        last = resp.json()
        if last.get("extraction_status") in ("extracted", "failed"):
            return last
        time.sleep(interval)
    raise AssertionError(
        f"source {source_id} 未在 {timeout}s 内完成 extraction"
        f"（last status={last.get('extraction_status')!r}）"
    )


def wait_for_source_deleted(
    client: TestClient,
    source_id: int,
    *,
    db_path: Path | None = None,
    timeout: float = 5.0,
    interval: float = 0.02,
) -> bool:
    """等待异步 delete 的 complete_purge 完成。

    ``GET source`` 在 ``lifecycle=deleting`` 时就返回 404，但 row 要等
    complete_purge 才物理删除。提供 ``db_path`` 时直接 poll DB row（可靠）；
    否则只 poll GET 404（仅适用于不验证 row 物理删除的场景）。
    """
    import sqlite3

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if db_path is not None:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM knowledge_sources WHERE id = ?", (source_id,)
                ).fetchone()
            if row is None:
                return True
        elif client.get(f"/api/knowledge/sources/{source_id}").status_code == 404:
            return True
        time.sleep(interval)
    return False
