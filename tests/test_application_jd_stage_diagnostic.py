from __future__ import annotations

import json
import sqlite3

from scripts.application_jd_stage_diagnostic import build_stage_record, slice_audit_records
from scripts.application_jd_stage_diagnostic import collect_stage_diagnostic


def test_stage_record_keeps_only_redacted_evidence_and_provider_metadata() -> None:
    snapshot = {
        "jd": {"text": "不要出现在报告里的岗位原文"},
        "resume": {
            "content_json": {
                "raw_text": "不要出现在报告里的简历原文",
                "skills": ["Python"],
            }
        },
        "candidate_assertions": [{"index": 0, "text": "不要出现在报告里的断言"}],
    }
    proposal = {
        "summary": {"evidence_refs": []},
        "conditions": [],
        "risks": [],
        "questions": [],
        "next_steps": [],
    }

    record = build_stage_record(
        stage="triage",
        snapshot=snapshot,
        proposal=proposal,
        result_status="ready",
        provider_records=[
            {
                "operation": "opportunity_fit",
                "provider_type": "openai_compatible",
                "provider_id": "default",
                "model": "deepseek-v4-pro",
                "input_fingerprint_sha256": "a" * 64,
                "schema_fingerprint_sha256": "b" * 64,
            }
        ],
        provider_results=[
            {
                "operation": "opportunity_fit",
                "status": "success",
                "elapsed_ms": 1234,
                "failure_category": None,
                "provider_request_id_hash": "c" * 12,
            }
        ],
    )

    assert record["stage"] == "triage"
    assert record["input_fingerprints"] == ["a" * 64]
    assert record["schema_fingerprints"] == ["b" * 64]
    assert record["evidence_counts"] == {"jd": 1, "resume": 2, "user_assertion": 1}
    assert record["path_types"] == ["jd", "resume", "user_assertion"]
    assert record["user_assertion_count"] == 1
    assert record["proposal_status"] == "safe_empty"
    assert record["array_lengths"] == {
        "conditions": 0,
        "risks": 0,
        "questions": 0,
        "next_steps": 0,
        "summary.evidence_refs": 0,
    }
    serialized = str(record)
    assert "不要出现在报告里的" not in serialized
    assert record["provider_model"] == {
        "provider_type": "openai_compatible",
        "provider_id": "default",
        "model": "deepseek-v4-pro",
    }
    assert record["provider_result"] == {
        "status": "success",
        "elapsed_ms": 1234,
        "failure_category": None,
        "provider_request_id_hash": "c" * 12,
    }


def test_stage_record_marks_non_empty_triage_proposal_without_inventing_failure() -> None:
    record = build_stage_record(
        stage="triage",
        snapshot={"jd": {"text": "jd"}, "resume": {"content_json": {"raw_text": "resume"}}},
        proposal={
            "summary": {"evidence_refs": [{"source": "resume", "path": "/raw_text", "excerpt": "resume"}]},
            "conditions": [{"id": "condition-1"}],
            "risks": [],
            "questions": [],
            "next_steps": [],
        },
        result_status="ready",
        provider_records=[],
        provider_results=[],
    )

    assert record["proposal_status"] == "non_empty"
    assert record["failure_category"] is None
    assert record["provider_model"] is None


def test_stage_a_record_proves_pilot_chat_confirmation_and_version_readback() -> None:
    browser_records = [
        {
            "kind": "browser_request",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat",
        },
        {
            "kind": "browser_response",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat",
            "response_status": 200,
        },
        {
            "kind": "browser_request",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat/confirm",
        },
        {
            "kind": "browser_response",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat/confirm",
            "response_status": 200,
        },
        {
            "kind": "browser_response",
            "method": "GET",
            "url": "http://127.0.0.1:8000/api/applications/1/job-description/versions",
            "response_status": 200,
            "response_source_kinds": ["pilot", "ui"],
            "response_jd_version_ids": [99, 1],
        },
        {
            "kind": "browser_response",
            "method": "GET",
            "url": "http://127.0.0.1:8000/api/applications/1/job-description/versions/2",
            "response_status": 200,
            "response_source_kind": "pilot",
            "response_jd_version_id": 2,
        },
    ]

    record = build_stage_record(
        stage="jd_pilot",
        snapshot=None,
        proposal=None,
        result_status=None,
        provider_records=[],
        provider_results=[],
        browser_records=browser_records,
    )

    assert record["result_status"] == "ready"
    assert record["pilot_observed"] is True
    assert record["confirmation_observed"] is True
    assert record["jd_version_id"] == 2
    assert record["source_kinds"] == ["pilot", "ui"]


def test_stage_a_record_fails_closed_on_unsuccessful_version_readback() -> None:
    browser_records = [
        {
            "kind": "browser_request",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat",
        },
        {
            "kind": "browser_request",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat/confirm",
        },
        {
            "kind": "browser_response",
            "method": "POST",
            "url": "http://127.0.0.1:8000/api/chat/confirm",
            "response_status": 200,
        },
        {
            "kind": "browser_response",
            "method": "GET",
            "url": "http://127.0.0.1:8000/api/applications/1/job-description/versions",
            "response_status": 500,
            "response_source_kinds": ["pilot", "ui"],
            "response_jd_version_ids": [2, 1],
        },
        {
            "kind": "browser_response",
            "method": "GET",
            "url": "http://127.0.0.1:8000/api/applications/1/job-description/versions/2",
            "response_status": 500,
            "response_source_kind": "pilot",
            "response_jd_version_id": 2,
        },
    ]

    record = build_stage_record(
        stage="jd_pilot",
        snapshot=None,
        proposal=None,
        result_status=None,
        provider_records=[],
        provider_results=[],
        browser_records=browser_records,
    )

    assert record["result_status"] == "not_observed"
    assert record["ready"] is False
    assert record["jd_version_id"] is None


def test_slice_audit_records_uses_a_stage_window() -> None:
    records = [{"id": 1}, {"id": 2}, {"id": 3}]

    selected, end_index = slice_audit_records(records, 1)

    assert selected == [{"id": 2}, {"id": 3}]
    assert end_index == 3


def test_collect_stage_diagnostic_reads_only_the_selected_triage_window(tmp_path) -> None:
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE opportunity_fit_review_stages ("
            "id INTEGER, application_id INTEGER, stage TEXT, jd_version_id INTEGER, "
            "source_snapshot_json TEXT, proposal_json TEXT, status TEXT)"
        )
        db.execute(
            "INSERT INTO opportunity_fit_review_stages VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                7,
                "triage",
                9,
                json.dumps(
                    {
                        "jd": {"text": "private jd"},
                        "resume": {"content_json": {"raw_text": "private resume"}},
                        "candidate_assertions": [],
                    }
                ),
                json.dumps(
                    {
                        "summary": {"evidence_refs": []},
                        "conditions": [],
                        "risks": [],
                        "questions": [],
                        "next_steps": [],
                    }
                ),
                "ready",
            ),
        )
        db.commit()

    provider_audit = tmp_path / "provider.jsonl"
    provider_audit.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {"kind": "provider_request_metadata", "input_fingerprint_sha256": "d" * 64},
                {
                    "kind": "provider_request_metadata",
                    "provider_type": "openai_compatible",
                    "provider_id": "default",
                    "model": "deepseek-v4-pro",
                    "input_fingerprint_sha256": "e" * 64,
                    "schema_fingerprint_sha256": "f" * 64,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    operation_audit = tmp_path / "operation.jsonl"
    operation_audit.write_text(
        json.dumps(
            {
                "kind": "provider_request_result",
                "status": "success",
                "elapsed_ms": 12,
                "failure_category": None,
                "provider_request_id_hash": "a" * 12,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record, offsets = collect_stage_diagnostic(
        stage="triage",
        db_path=db_path,
        application_id=7,
        jd_version_id=9,
        provider_audit_path=provider_audit,
        operation_audit_path=operation_audit,
        provider_start_index=1,
    )

    assert record["input_fingerprints"] == ["e" * 64]
    assert record["schema_fingerprints"] == ["f" * 64]
    assert record["proposal_status"] == "safe_empty"
    assert offsets == {"provider_end_index": 2, "operation_end_index": 1}
