"""KI-09 Brief HTTP API 契约测试。"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from conftest import wait_for_extraction

from offerpilot.config import AIProviderProfile, Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_MIN_CONTEXT_WINDOW,
    BRIEF_PROMPT_VERSION,
    BRIEF_SCHEMA_VERSION,
)
from offerpilot.knowledge.repository import (
    BriefAttemptCreateInput,
    JobCreateInput,
    KnowledgeRepository,
)
from offerpilot.api import create_app
from offerpilot.knowledge.worker import BriefWorker, KnowledgeJobRunner, ExtractionWorker



def _upload(client: TestClient, filename: str, content: bytes) -> Any:
    files = {"file": (filename, content, "text/markdown")}
    response = client.post("/api/knowledge/sources", files=files)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response

def _upload(client, filename: str, content: bytes) -> Any:
    files = {"file": (filename, content, "text/markdown")}
    response = client.post("/api/knowledge/sources", files=files)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response

def _valid_payload(evidence_items: list, plan: Any) -> dict[str, Any]:
    """KBR-04 v2 合法 payload：每个文本章节至少一条自身 Evidence 被 key_points 引用。"""
    evidence_ids = [item.id for item in evidence_items]
    assert len(evidence_ids) >= 2
    section_eids: dict[str, list[str]] = {}
    for item in evidence_items:
        path = tuple(item.heading_path or ())
        key = "__document__" if not path else " / ".join(path)
        section_eids.setdefault(key, []).append(item.id)
    text_sections = [entry for entry in plan.sections.values() if not entry.must_skip]
    reps = [
        section_eids[entry.section_key][0]
        for entry in text_sections
        if section_eids.get(entry.section_key)
    ]
    first = text_sections[0] if text_sections else plan.sections["__document__"]
    first_eid = section_eids.get(first.section_key, [evidence_ids[0]])[0]
    ov1 = reps[0] if reps else evidence_ids[0]
    ov2 = (
        reps[1]
        if len(reps) > 1
        else (evidence_ids[1] if len(evidence_ids) > 1 else ov1)
    )
    return {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "Source 描述 OfferPilot。", "evidence_ids": [ov1]},
            {"statement": "Source 涉及 SQLite。", "evidence_ids": [ov2]},
        ],
        "key_points": (
            [{"statement": "Evidence 是引用单位。", "evidence_ids": [rep]} for rep in reps]
            or [{"statement": "Evidence 是引用单位。", "evidence_ids": [ov1]}]
        ),
        "section_guides": [
            {
                "section_key": first.section_key,
                "heading_path": list(first.heading_path),
                "summary": "介绍 OfferPilot。",
                "evidence_ids": [first_eid],
            }
        ],
        "limitations": [
            {"statement": "未涉及细节。", "evidence_ids": [ov2]},
        ],
    }


def test_ki09_brief_endpoint_returns_empty_without_brief(app_client) -> None:
    upload = _upload(app_client, "doc.md", "# 概述\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]
    response = app_client.get(f"/api/knowledge/sources/{source_id}/brief")
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == source_id
    # KV1-01：V1 导入不自动触发 Brief；未显式 rebuild 时 Brief 保持 not_started，
    # endpoint 返回空 Brief 且不携带 block reason。
    assert body["brief_status"] == "not_started"
    assert body["brief"] is None


def test_ki09_brief_endpoint_returns_404_for_unknown_source(app_client) -> None:
    response = app_client.get("/api/knowledge/sources/99991/brief")
    assert response.status_code == 404


def test_ki09_brief_rebuild_returns_202_with_block_reason(app_client) -> None:
    upload = _upload(app_client, "doc.md", "# 概述\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]
    response = app_client.post(f"/api/knowledge/sources/{source_id}/brief/rebuild")
    assert response.status_code == 202
    body = response.json()
    assert body["source_id"] == source_id
    # 测试环境无合格 Provider，必然返回 block reason
    assert body["brief_block_reason"] in (
        "provider_unavailable",
        "provider_context_too_small",
    )


def test_ki09_brief_rebuild_returns_404_for_unknown_source(app_client) -> None:
    response = app_client.post("/api/knowledge/sources/99991/brief/rebuild")
    assert response.status_code == 404


def test_ki09_brief_endpoint_exposes_committed_brief_payload(
    app_client, tmp_path
) -> None:
    """Spec §10 / §16.1：成功 Brief 通过 endpoint 暴露 payload；前端展示用。"""
    upload = _upload(
        app_client,
        "doc.md",
        "# 概述\n\n正文一段。\n\n## 第二段\n\n正文二段。\n".encode("utf-8"),
    )
    source_id = upload.json()["source"]["id"]

    # 通过 repository + worker 直接构造一个成功 Brief，跳过模型调用
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    evidence_page = repository.list_evidence(source_id, limit=50)
    evidence_ids = [item.id for item in evidence_page.items]
    assert len(evidence_ids) >= 2

    # 使用 endpoint client 的同一个进程内 repository 提交一次成功 Brief
    from offerpilot.knowledge.brief import build_section_coverage_plan

    plan = build_section_coverage_plan(evidence_page.items)
    payload_dict = _valid_payload(evidence_page.items, plan)
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=evidence_page.items[0].snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    ok, _, _ = repository.commit_brief_attempt_success(
        attempt.id,
        job_id=job_id,
        attempt_token=token,
        payload_json=json.dumps(payload_dict, ensure_ascii=False),
        validation_report_json='{"stage":"ok"}',
        token_input_count=100,
        token_output_count=200,
        latency_ms=500,
    )
    assert ok

    response = app_client.get(f"/api/knowledge/sources/{source_id}/brief")
    assert response.status_code == 200
    body = response.json()
    assert body["brief"] is not None
    assert body["brief"]["payload"]["schema_version"] == 2
    assert body["brief"]["payload"]["language"] == "zh-CN"
    assert len(body["brief"]["payload"]["overview"]) == 2


def test_ki09_brief_endpoint_lists_attempts_with_redacted_secrets(
    app_client, tmp_path
) -> None:
    """Spec §18：Attempt 不暴露 API Key 或原始 Prompt。"""
    upload = _upload(app_client, "doc.md", "# 概述\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    evidence_page = repository.list_evidence(source_id, limit=10)
    attempt, _, _ = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=evidence_page.items[0].snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="https://secret.example.com",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    assert attempt.id > 0
    for index in range(201):
        repository.append_brief_attempt_step(
            attempt.id,
            phase="test",
            output={"index": index},
        )

    response = app_client.get(f"/api/knowledge/sources/{source_id}/brief")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_attempt"] is not None
    assert len(body["latest_attempt"]["steps"]) == 200
    assert body["latest_attempt"]["total_steps"] == 201
    assert body["latest_attempt"]["has_more"] is True
    serialized = json.dumps(body)
    # Spec §18 安全检查：API Key 与 base URL 都不得出现在响应中
    assert "sk-" not in serialized
    assert "secret.example.com" not in serialized


def test_ki09_brief_worker_processes_real_queue_with_stub_provider(
    tmp_path
) -> None:
    """Spec §10.4：tick_brief 触发完整 generation/validation/commit 流程。"""
    # 独立 app 上传完成 extraction；with 退出后后台 worker 停，避免与 stub tick_brief 竞争。
    with TestClient(create_app(data_dir=tmp_path)) as client:
        upload = _upload(
            client,
            "doc.md",
            "# 概述\n\n正文一段。\n\n## 第二段\n\n正文二段。\n".encode("utf-8"),
        )
        source_id = upload.json()["source"]["id"]

    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    # 配置合格 Provider
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
    config = Config(
        api_key="sk-test",
        providers=[provider],
        active_provider_id="default",
    )
    evidence_page = repository.list_evidence(source_id, limit=50)
    from offerpilot.knowledge.brief import build_section_coverage_plan

    plan = build_section_coverage_plan(evidence_page.items)
    valid_payload = _valid_payload(evidence_page.items, plan)

    def _stub_client(**payload: Any) -> dict[str, Any]:
        system_text = ""
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                system_text = message.get("content") or ""
        if "Validator" in system_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": "supported", "reason": "ok"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        return {
            "choices": [
                {"message": {"content": json.dumps(valid_payload, ensure_ascii=False)}}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

    worker = BriefWorker(repository, config, model_client=_stub_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=evidence_page.items[0].snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "succeeded", results[0].error_message

    # Brief 已提交；repository 可读 ready Brief（避开后台 worker 干扰）。
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"
