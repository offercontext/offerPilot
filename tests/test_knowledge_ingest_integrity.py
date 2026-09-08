"""Knowledge ingest 文件提交与路径完整性回归测试。"""

from __future__ import annotations

import io
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy.exc import IntegrityError

from conftest import symlink_or_skip, wait_for_extraction
from offerpilot import api
from offerpilot.config import Config
from offerpilot.db import session_factory_for_data_dir
from offerpilot.knowledge.repository import KnowledgeRepository
from offerpilot.knowledge.repository import (
    EvidenceDraftInput,
    SnapshotCreateInput,
    commit_extraction,
)
from offerpilot.knowledge.service import _safe_cleanup
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.models import (
    KnowledgeBriefAttempt,
    KnowledgeEvidence,
    KnowledgeExtractionSnapshot,
    KnowledgeJob,
    KnowledgeSource,
    KnowledgeSourceBrief,
)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 30, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_commit_failure_cleans_final_directory(tmp_path, monkeypatch) -> None:
    """Extraction 提交事务失败时 Source 标记 failed，不留半提交 Snapshot/Evidence。"""

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError("forced commit failure", {}, RuntimeError("forced"))

    monkeypatch.setattr("offerpilot.knowledge.worker.commit_extraction", fail_commit)
    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/knowledge/sources",
            files={"file": ("broken.md", b"# Broken\n\ncommit failure\n", "text/markdown")},
        )
        # 异步：上传入队成功（202），worker commit 失败 → extraction_failed。
        assert response.status_code == 202
        source_id = response.json()["source"]["id"]
        source = wait_for_extraction(client, source_id)
        assert source["extraction_status"] == "failed"
    # commit 失败：Source 标记 failed（保留 row，可重试），staging 已在 ingest 时清理。
    staging_root = tmp_path / "knowledge" / "staging"
    assert not staging_root.exists() or not list(staging_root.iterdir())
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT extraction_status FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchone()
        snapshots = conn.execute(
            "SELECT COUNT(*) FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        evidence = conn.execute(
            "SELECT COUNT(*) FROM knowledge_evidence WHERE source_id = ?", (source_id,)
        ).fetchone()
    assert row == ("failed",)
    # commit 失败不得遗留半提交 Snapshot/Evidence。
    assert snapshots == (0,)
    assert evidence == (0,)


def test_brief_enqueue_failure_is_persisted_after_extraction(tmp_path, monkeypatch) -> None:
    """Brief 入队失败不能静默留下 not_started。"""

    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(
        repository,
        tmp_path,
        session_factory,
        config=Config(),
    )

    def fail_enqueue(_source_id: int) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(service, "enqueue_or_block_brief", fail_enqueue)
    result = service.ingest(
        IngestRequest(filename="queue.md", content_bytes=b"# Queue\n\nbody\n")
    )
    # 显式驱动 extraction；callback enqueue_or_block_brief 被 monkeypatch 失败 → brief_failed。
    from offerpilot.knowledge.worker import ExtractionWorker, KnowledgeJobRunner
    KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory,
                         on_extraction_succeeded=service.enqueue_or_block_brief),
    ).tick_extraction(lease_owner="test")

    source = repository.get_source(result.source.id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.brief_status == "failed"
    assert source.brief_error_code == "brief_enqueue_failed"
    assert "queue unavailable" in source.brief_error_message


def test_bundle_rejects_final_filename_collision(tmp_path) -> None:
    """不同逻辑名若映射到同一安全文件名，必须在 staging 前拒绝。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        main = b"# Collision\n\n![hidden](.a.png)\n![plain](a.png)\n"
        image = _png_bytes()
        response = client.post(
            "/api/knowledge/sources",
            files=[
                ("file", ("bundle.md", main, "text/markdown")),
                ("files", (".a.png", image, "image/png")),
                ("files", ("a.png", image, "image/png")),
            ],
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == "bundle_invalid"
        sources_root = tmp_path / "knowledge" / "sources"
        assert not sources_root.exists() or not list(sources_root.iterdir())


def test_download_rejects_tampered_paths(tmp_path) -> None:
    """SQLite 中被篡改的相对路径不能越出当前 Source 的目录。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        main = b"# Safe\n\n![asset](asset.png)\n"
        response = client.post(
            "/api/knowledge/sources",
            files=[
                ("file", ("safe.md", main, "text/markdown")),
                ("files", ("asset.png", _png_bytes(), "image/png")),
            ],
        )
        assert response.status_code == 202
        source_id = response.json()["source"]["id"]
        wait_for_extraction(client, source_id)
        asset_id = client.get(f"/api/knowledge/sources/{source_id}/assets").json()["items"][0]["id"]

        outside = tmp_path / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        with sqlite3.connect(tmp_path / "data.db") as conn:
            conn.execute(
                "UPDATE knowledge_sources SET main_relative_path = ? WHERE id = ?",
                ("../../outside-secret.txt", source_id),
            )
            conn.execute(
                "UPDATE knowledge_source_assets SET relative_path = ? WHERE id = ?",
                ("../../outside-secret.txt", asset_id),
            )
            conn.commit()

        assert client.get(f"/api/knowledge/sources/{source_id}/content").status_code == 404
        assert (
            client.get(
                f"/api/knowledge/sources/{source_id}/assets/{asset_id}/content"
            ).status_code
            == 404
        )


def test_brief_attempt_rejects_cross_source_snapshot(tmp_path) -> None:
    """Brief Attempt 的 Source 与 Snapshot 必须属于同一 Source。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        first = client.post(
            "/api/knowledge/sources",
            files={"file": ("first.md", b"# First\n\none\n", "text/markdown")},
        )
        second = client.post(
            "/api/knowledge/sources",
            files={"file": ("second.md", b"# Second\n\ntwo\n", "text/markdown")},
        )
        assert first.status_code == 202
        assert second.status_code == 202
        first_id = first.json()["source"]["id"]
        wait_for_extraction(client, first_id)
        second_id = second.json()["source"]["id"]
        wait_for_extraction(client, second_id)

        session_factory = session_factory_for_data_dir(tmp_path)
        with session_factory() as session:
            first_source = session.get(KnowledgeSource, first_id)
            second_source = session.get(KnowledgeSource, second_id)
            assert first_source is not None and first_source.active_snapshot_id is not None
            assert second_source is not None and second_source.active_snapshot_id is not None
            session.add(
                KnowledgeBriefAttempt(
                    source_id=first_id,
                    snapshot_id=second_source.active_snapshot_id,
                    status="pending",
                    provider_id="test",
                    provider_model="test-model",
                    provider_base_url="",
                    context_window=96_000,
                    max_output_tokens=1_000,
                    prompt_version="v1",
                    schema_version=1,
                    language="zh-CN",
                )
            )
            with pytest.raises(IntegrityError, match="knowledge_brief_attempt_snapshot_mismatch"):
                session.commit()


def test_evidence_neighbor_rejects_cross_snapshot_link(tmp_path) -> None:
    """同一 Source 的不同 Snapshot 之间不能建立邻接 Evidence 链。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/knowledge/sources",
            files={"file": ("source.md", b"# Source\n\nbody\n", "text/markdown")},
        )
        assert response.status_code == 202
        source_id = response.json()["source"]["id"]
        wait_for_extraction(client, source_id)

        session_factory = session_factory_for_data_dir(tmp_path)
        with session_factory() as session:
            source = session.get(KnowledgeSource, source_id)
            assert source is not None and source.active_snapshot_id is not None
            first_snapshot = session.get(KnowledgeExtractionSnapshot, source.active_snapshot_id)
            assert first_snapshot is not None
            second_snapshot = KnowledgeExtractionSnapshot(
                source_id=source_id,
                extractor_version="test-v2",
                parser_version="test",
                normalization_version="test",
                tokenizer_version="test",
                encoding="utf-8",
                detection_method="test",
                canonical_text="second",
                structure_manifest="{}",
                digest="digest-test-v2",
                token_count=1,
                char_count=6,
            )
            session.add(second_snapshot)
            session.flush()
            session.add(
                KnowledgeEvidence(
                    id="ev-cross-snapshot-1",
                    source_id=source_id,
                    snapshot_id=first_snapshot.id,
                    kind="text",
                    block_kind="paragraph",
                    ordinal=99,
                    heading_path_json="[]",
                    char_start=0,
                    char_end=1,
                    line_start=1,
                    line_end=1,
                    canonical_excerpt="one",
                    search_text="one",
                    content_hash="hash-one",
                )
            )
            session.flush()
            session.add(
                KnowledgeEvidence(
                    id="ev-cross-snapshot-2",
                    source_id=source_id,
                    snapshot_id=second_snapshot.id,
                    kind="text",
                    block_kind="paragraph",
                    ordinal=1,
                    heading_path_json="[]",
                    char_start=0,
                    char_end=1,
                    line_start=1,
                    line_end=1,
                    canonical_excerpt="two",
                    search_text="two",
                    content_hash="hash-two",
                    previous_evidence_id="ev-cross-snapshot-1",
                )
            )
            with pytest.raises(IntegrityError, match="knowledge_evidence_neighbor_mismatch"):
                session.commit()


def test_job_snapshot_requires_source_owner(tmp_path) -> None:
    """Job 绑定 Snapshot 时必须同时绑定同一 Source。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/knowledge/sources",
            files={"file": ("source.md", b"# Source\n\nbody\n", "text/markdown")},
        )
        assert response.status_code == 202
        source_id = response.json()["source"]["id"]
        wait_for_extraction(client, source_id)

        session_factory = session_factory_for_data_dir(tmp_path)
        with session_factory() as session:
            source = session.get(KnowledgeSource, source_id)
            assert source is not None and source.active_snapshot_id is not None
            session.add(
                KnowledgeJob(
                    kind="brief",
                    queue="brief",
                    source_id=None,
                    snapshot_id=source.active_snapshot_id,
                    stage="brief_pending",
                    status="pending",
                )
            )
            with pytest.raises(IntegrityError, match="knowledge_job_snapshot_mismatch"):
                session.commit()


def test_failed_commit_cleanup_does_not_follow_symlink(tmp_path) -> None:
    """事务清理遇到 symlink 时只能删除链接，不能递归删除链接目标。"""

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    cleanup_root = tmp_path / "staging-upload"
    cleanup_root.mkdir()
    symlink_or_skip(cleanup_root / "outside-link", outside, target_is_directory=True)

    _safe_cleanup(cleanup_root)

    assert secret.read_text(encoding="utf-8") == "keep"
    assert not cleanup_root.exists()


def test_evidence_list_requires_source_and_snapshot_match(tmp_path) -> None:
    """指定 Snapshot 不属于 URL Source 时不得跨 Source 回读 Evidence。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        first = client.post(
            "/api/knowledge/sources",
            files={"file": ("first.md", b"# First\n\none\n", "text/markdown")},
        )
        second = client.post(
            "/api/knowledge/sources",
            files={"file": ("second.md", b"# Second\n\ntwo\n", "text/markdown")},
        )
        assert first.status_code == 202
        assert second.status_code == 202
        first_id = first.json()["source"]["id"]
        wait_for_extraction(client, first_id)
        second_id = second.json()["source"]["id"]
        second_snapshot_id = wait_for_extraction(client, second_id)["active_snapshot_id"]

        response = client.get(
            f"/api/knowledge/sources/{first_id}/evidence",
            params={"snapshot_id": second_snapshot_id},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


def test_evidence_detail_hidden_while_source_deleting(tmp_path) -> None:
    """Source 进入 deleting 过渡态后，Evidence detail 不得继续暴露。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        upload = client.post(
            "/api/knowledge/sources",
            files={"file": ("source.md", b"# Source\n\nbody\n", "text/markdown")},
        )
        assert upload.status_code == 202
        source_id = upload.json()["source"]["id"]
        wait_for_extraction(client, source_id)
        evidence_id = client.get(f"/api/knowledge/sources/{source_id}/evidence").json()["items"][0]["id"]

        session_factory = session_factory_for_data_dir(tmp_path)
        repository = KnowledgeRepository(session_factory)
        assert repository.begin_delete(source_id) is not None

        response = client.get(f"/api/knowledge/evidence/{evidence_id}")
        assert response.status_code == 404
        search = client.post(
            "/api/knowledge/evidence/search",
            json={"query": "body", "include_archived": True},
        )
        assert search.status_code == 200
        assert search.json()["hits"] == []


def test_extractor_upgrade_marks_brief_outdated_and_api_keeps_old_payload(
    tmp_path,
) -> None:
    """新 Snapshot 切换必须标记 Brief 过期，但保留旧内容与 active 引用。"""

    with TestClient(api.create_app(data_dir=tmp_path)) as client:
        upload = client.post(
            "/api/knowledge/sources",
            files={"file": ("source.md", b"# Source\n\nold body\n", "text/markdown")},
        )
        assert upload.status_code == 202
        source_id = upload.json()["source"]["id"]
        wait_for_extraction(client, source_id)
        session_factory = session_factory_for_data_dir(tmp_path)
        repository = KnowledgeRepository(session_factory)

        with session_factory() as session:
            source = session.get(KnowledgeSource, source_id)
            assert source is not None and source.active_snapshot_id is not None
            old_snapshot_id = source.active_snapshot_id
            winning_attempt = KnowledgeBriefAttempt(
                source_id=source_id,
                snapshot_id=old_snapshot_id,
                status="succeeded",
                provider_id="primary",
                provider_model="test-model",
                provider_base_url="https://provider.invalid",
                context_window=96_000,
                max_output_tokens=4_096,
                prompt_version="brief-v1",
                schema_version=1,
                language="zh-CN",
            )
            session.add(winning_attempt)
            session.flush()
            old_brief = KnowledgeSourceBrief(
                source_id=source_id,
                snapshot_id=old_snapshot_id,
                winning_attempt_id=winning_attempt.id,
                schema_version=1,
                language="zh-CN",
                payload_json='{"overview":[{"statement":"旧 Brief"}]}',
                outdated=False,
            )
            session.add(old_brief)
            session.flush()
            source.active_brief_id = old_brief.id
            source.brief_status = "ready"
            session.commit()
            old_brief_id = old_brief.id

        with session_factory() as session:
            new_snapshot, _ = commit_extraction(
                session,
                snapshot_input=SnapshotCreateInput(
                    source_id=source_id,
                    extractor_version="extractor-v2",
                    parser_version="parser-v2",
                    normalization_version="normalization-v2",
                    tokenizer_version="tokenizer-v2",
                    encoding="utf-8",
                    detection_method="strict-utf8",
                    canonical_text="# Source\n\nnew body\n",
                    structure_manifest="{}",
                    digest="digest-extractor-v2",
                    token_count=3,
                    char_count=15,
                ),
                evidence_drafts=[
                    EvidenceDraftInput(
                        block_kind="paragraph",
                        heading_path=("Source",),
                        char_start=10,
                        char_end=19,
                        line_start=3,
                        line_end=3,
                        canonical_excerpt="new body",
                        search_text="new body",
                        content_hash="new-body-hash",
                        locator="paragraph:1",
                    )
                ],
                source_id=source_id,
                source_title="Source",
                extractor_version="extractor-v2",
            )
            session.commit()
            new_snapshot_id = new_snapshot.id

        updated_source = repository.get_source(source_id)
        updated_brief = repository.get_source_brief(source_id)
        assert updated_source is not None
        assert updated_source.active_snapshot_id == new_snapshot_id
        assert updated_source.brief_status == "outdated"
        assert updated_source.active_brief_id == old_brief_id
        assert updated_brief is not None
        assert updated_brief.id == old_brief_id
        assert updated_brief.snapshot_id == old_snapshot_id
        assert updated_brief.outdated is True
        response = client.get(f"/api/knowledge/sources/{source_id}/brief")
        assert response.status_code == 200
        payload = response.json()
        assert payload["brief_status"] == "outdated"
        assert payload["brief"]["payload"]["overview"][0]["statement"] == "旧 Brief"
        assert payload["brief"]["outdated"] is True
