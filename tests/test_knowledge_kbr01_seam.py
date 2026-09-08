"""KBR-01 最高层集成测试 seam 验收。

验证从 Source 原始字节经正式 Ingest → Extraction queue → Brief queue 的 seam：

- ``ingest_and_extract`` 显式驱动 Extraction queue，得到 extracted Source + 可见 Evidence。
- ``RoleAwareModelClient`` 区分 generation/repair/validation 角色，记录调用角色/顺序/摘要/次数，
  且 call_log 不含完整 Prompt 或 Source 正文（Spec 隐私边界）。
- 成功路径产出 ready Brief；失败路径保留 Evidence 可搜索且无半提交 current Brief；
  重建路径在候选失败时保留旧 current Brief。

本文件只验证 seam 行为；Brief/Extraction 的产品语义由 ki09/ki10 等测试覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW
from offerpilot.knowledge.service import KnowledgeIngestService

from _knowledge_seam import (
    BriefRunOutcome,
    EMPTY_REPAIR_PATCH,
    RoleAwareModelClient,
    build_supported_brief_json,
    drive_brief_queue,
    expected_validation_count,
    ingest_and_extract,
)

_CONTENT = (
    "# 概述\n\n"
    "Source 描述 OfferPilot 与 SQLite 单一事实源决策。\n\n"
    "## 第二段\n\n"
    "Evidence 是引用单位，Evidence 不重叠。\n"
)


def _qualified_config() -> Config:
    provider = AIProviderProfile(
        id="default",
        label="Default",
        provider="openai",
        api_key="sk-test",
        base_url="https://example.com",
        model="gpt-test",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    return Config(api_key="sk-test", providers=[provider], active_provider_id="default")


# ---------------------------------------------------------------------------
# Spec seam 验收点 1-2：正式 Ingest/Job/Worker 边界 + 显式 Extraction queue
# ---------------------------------------------------------------------------


def test_ingest_and_extract_drives_extraction_queue(tmp_path: Path) -> None:
    """seam 显式驱动 Extraction queue：返回的 Source 已 extracted、Snapshot/Evidence 可见。"""
    repository, _, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8")
    )
    assert snapshot_id > 0
    source = repository.get_source(source_id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.active_snapshot_id == snapshot_id
    # Evidence 经正式 Extraction 提交，可搜索。
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    assert len(evidence_page.items) >= 2
    assert repository.search_evidence("Evidence", limit=5)


# ---------------------------------------------------------------------------
# Spec seam 验收点 3-4：角色区分 + 调用记录（不含完整 Prompt/Source）
# ---------------------------------------------------------------------------


def test_role_aware_client_classifies_generation_repair_validation(tmp_path: Path) -> None:
    """一次 repair 路径覆盖三种角色：generation → validation(unsupported) → repair → validation。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    unsupported = json.dumps({"decision": "unsupported", "reason": "no link"})
    val_count = expected_validation_count(brief_json)
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[unsupported] * (val_count * 2),
    )
    drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    roles = client.role_sequence()
    # repair 路径调用顺序：generation → validation*N → repair → validation*N
    assert roles[0] == "generation"
    assert "repair" in roles
    repair_index = roles.index("repair")
    # repair 之前是 validation 批次，之后是第二批 validation。
    assert roles[1:repair_index] == ["validation"] * val_count
    assert roles[repair_index + 1 :] == ["validation"] * val_count
    assert client.count("generation") == 1
    assert client.count("repair") == 1
    assert client.count("validation") == val_count * 2


def test_call_log_excludes_full_prompt_and_source(tmp_path: Path) -> None:
    """call_log 只保留角色、序号和截断 statement 摘要；不含完整 Prompt 或 Source 正文。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    full_prompt_marker = "你是 OfferPilot 的 Knowledge Brief"
    source_marker = "Source 描述 OfferPilot 与 SQLite 单一事实源决策"
    for record in client.call_log:
        digest = record.input_digest
        assert len(digest) <= 80
        assert full_prompt_marker not in digest
        assert source_marker not in digest


# ---------------------------------------------------------------------------
# Spec seam 验收点 6：成功路径
# ---------------------------------------------------------------------------


def test_seam_success_path_produces_ready_brief(tmp_path: Path) -> None:
    """Extraction 提交后 Brief 才开始，最终得到 ready Brief。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    outcome: BriefRunOutcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    assert outcome.job_results
    assert outcome.job_results[0].status == "succeeded", outcome.job_results[0].error_message
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready"
    assert outcome.brief is not None
    assert outcome.attempt is not None
    assert outcome.attempt.status == "succeeded"
    assert client.count("generation") == 1
    assert client.count("repair") == 0


# ---------------------------------------------------------------------------
# Spec seam 验收点 7：失败路径
# ---------------------------------------------------------------------------


def test_seam_failure_path_keeps_evidence_searchable_and_no_half_committed_brief(
    tmp_path: Path,
) -> None:
    """Brief 失败不影响 Evidence 搜索，且没有半提交 current Brief。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    unsupported = json.dumps({"decision": "unsupported", "reason": "no link"})
    val_count = expected_validation_count(brief_json)
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[unsupported] * (val_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "failed"
    # 失败候选不能成为 current Brief。
    assert outcome.brief is None
    assert outcome.attempt is not None
    assert outcome.attempt.status == "failed"
    # Evidence 仍可搜索。
    assert repository.search_evidence("Evidence", limit=5)


# ---------------------------------------------------------------------------
# Spec seam 验收点 8：重建路径
# ---------------------------------------------------------------------------


def test_seam_rebuild_failure_preserves_existing_brief(tmp_path: Path) -> None:
    """旧 current Brief 在重建候选失败时继续可见。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    # 首轮成功：建立 current Brief。
    success_client = RoleAwareModelClient(generation=[brief_json])
    first = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=success_client,
        source_id=source_id,
    )
    assert first.brief is not None
    winning_attempt_id = first.attempt.id if first.attempt else 0
    # 触发显式重建：创建新 brief Job（新 Attempt、独立重试预算）。
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=_qualified_config()
    )
    source_state, message = service.rebuild_brief(source_id)
    assert source_state is not None
    assert message == "brief_rebuild_queued"
    # 重建候选失败：validation 全 unsupported，repair 后仍失败。
    unsupported = json.dumps({"decision": "unsupported", "reason": "no link"})
    val_count = expected_validation_count(brief_json)
    rebuild_client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[unsupported] * (val_count * 2),
    )
    rebuild = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=rebuild_client,
        source_id=source_id,
    )
    assert rebuild.attempt is not None
    assert rebuild.attempt.status == "failed"
    assert rebuild.attempt.id != winning_attempt_id
    # 旧 current Brief 仍可见且未变；重建失败不破坏 ready 状态（旧 Brief 保留语义）。
    preserved = repository.get_source_brief(source_id)
    assert preserved is not None
    assert preserved.winning_attempt_id == winning_attempt_id
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"  # 旧 Brief 保留
