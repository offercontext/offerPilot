from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
from uuid import uuid4

from offerpilot.ai.opportunity_fit_reviews import build_source_snapshot
from offerpilot.api import create_app
from offerpilot.config import resolve_data_dir
from offerpilot.repositories.json_contract import canonical_json, sha256_text
from offerpilot.smoke import (
    _cleanup_real_ai_smoke_records,
    _full_verify_client,
    _running_server,
    _smoke_exception_category,
    _validate_opportunity_fit_v2_stage_response,
)
from scripts.full_real_ai_verify import (
    _build_summary,
    _prepare_temp_config,
    _read_operation_audit,
    _read_request_audit,
    _safe_config_summary,
    _write_json,
)


TARGET_JD = "Build reliable API quality workflows."
TARGET_ASSERTION = "I led the migration."
RESUME_CONTENT = {
    "raw_text": "Built API services and led migration.",
    "skills": ["Python"],
}
JD_SOURCE_LABEL = "Targeted pasted JD"
TARGET_EVENT_AT = "2026-07-22T10:00:00+08:00"
SAFE_CODE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def _safe_error_code(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    value = body.get("error_code")
    if not isinstance(value, str) or not value or len(value) > 96:
        return ""
    if any(character not in SAFE_CODE_CHARS for character in value):
        return ""
    return value


def _hash_token(token: object) -> str:
    if not isinstance(token, str) or not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _safe_stage(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = (
        "id",
        "review_id",
        "stage_id",
        "application_id",
        "resume_id",
        "jd_version_id",
        "stage",
        "schema_version",
        "stage_status",
        "parent_triage_stage_id",
        "idempotency_key",
        "source_fingerprint_sha256",
        "proposal_sha256",
    )
    result: dict[str, Any] = {}
    for field in fields:
        if field in value:
            result[field] = value[field]
    return result


def _safe_history(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for field in ("id", "review_id", "application_id", "schema_version", "status", "triage_idempotency_key"):
        if field in value:
            result[field] = value[field]
    stages = value.get("stages")
    if isinstance(stages, list):
        result["stages"] = [_safe_stage(stage) for stage in stages if isinstance(stage, dict)]
    return result


def _stage_from_history(history: object, stage_name: str) -> dict[str, Any]:
    if not isinstance(history, dict):
        return {}
    stages = history.get("stages")
    if not isinstance(stages, list):
        return {}
    for stage in stages:
        if isinstance(stage, dict) and stage.get("stage") == stage_name:
            return _safe_stage(stage)
    return {}


def _response_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"payload_type": type(value).__name__}
    return {
        "top_level_keys": sorted(key for key in value if isinstance(key, str))[:32],
        "stage": value.get("stage") if isinstance(value.get("stage"), str) else "",
        "stage_status": value.get("stage_status")
        if isinstance(value.get("stage_status"), str)
        else "",
        "error_code": value.get("error_code")
        if isinstance(value.get("error_code"), str)
        else "",
    }


def _api_result(response: Any) -> dict[str, Any]:
    return {
        "status": response.status_code,
        "error_code": _safe_error_code(response),
    }


def _operation_records(report_dir: Path) -> list[dict[str, Any]]:
    return [
        record
        for record in _read_operation_audit(report_dir)
        if record.get("kind") == "api_request"
        and record.get("operation") == "opportunity_fit"
    ]


def _provider_records(report_dir: Path) -> list[dict[str, Any]]:
    return [
        record
        for record in _read_operation_audit(report_dir)
        if record.get("kind") == "provider_request_result"
        and record.get("operation") == "opportunity_fit"
    ]


def run_targeted_diagnostic(
    *,
    source_data: Path,
    report_dir: Path,
    model: str = "deepseek-v4-pro",
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    for artifact in (
        "targeted-start.json",
        "targeted-summary.json",
        "full-verify-inner-diagnostic.json",
        "provider-request-audit.jsonl",
        "full-verify-operation-audit.jsonl",
    ):
        (report_dir / artifact).unlink(missing_ok=True)

    isolated_data = Path(tempfile.mkdtemp(prefix="offerpilot-opportunity-fit-deep-targeted-"))
    source_config = source_data / "config.json"
    source_hash_before = source_config.read_bytes() if source_config.is_file() else b""
    environment_keys = (
        "OFFERPILOT_DATA",
        "OFFERPILOT_FULL_VERIFY_REPORT_DIR",
        "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE",
        "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE",
        "OFFERPILOT_FULL_VERIFY_OPERATION",
        "OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE",
        "NO_PROXY",
    )
    previous_env = {key: os.environ.get(key) for key in environment_keys}
    started = time.perf_counter()
    application_id: int | None = None
    resume_ids: list[int] = []
    event_id: int | None = None
    api_statuses: dict[str, int | None] = {
        "triage": None,
        "confirm": None,
        "history_before_deep": None,
        "deep": None,
        "history_after_deep": None,
    }
    error_codes: dict[str, str] = {}
    response_summaries: dict[str, dict[str, Any]] = {}
    triage_stage: dict[str, Any] = {}
    history_before_deep: dict[str, Any] = {}
    history_after_deep: dict[str, Any] = {}
    expected_source_fingerprint = ""
    confirmation_token_hash = ""
    triage_idempotency_key = str(uuid4())
    deep_idempotency_key = str(uuid4())
    review_id: int | None = None
    stage_id: int | None = None
    failure_category: str | None = None
    stderr = ""
    exit_code = 1
    config_summary: dict[str, Any] = {}

    try:
        _prepare_temp_config(source_data, isolated_data, model)
        config_summary = _safe_config_summary(isolated_data / "config.json", isolated_data)
        operation_audit = report_dir / "full-verify-operation-audit.jsonl"
        request_audit = report_dir / "provider-request-audit.jsonl"
        _write_json(
            report_dir / "targeted-start.json",
            {
                "status": "started",
                "stage": "opportunity_fit",
                "targeted_phase": "deep_review_request",
                "client_timeout_seconds": 180,
                "config": config_summary,
                "child_env": {
                    "OFFERPILOT_DATA": str(isolated_data),
                    "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
                    "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(request_audit),
                    "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(operation_audit),
                },
                "source_fixture": "synthetic_resume_jd_and_interview_event",
            },
        )
        os.environ["OFFERPILOT_DATA"] = str(isolated_data)
        os.environ["OFFERPILOT_FULL_VERIFY_REPORT_DIR"] = str(report_dir)
        os.environ["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = str(request_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE"] = str(operation_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION"] = "opportunity_fit"
        os.environ["OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE"] = "opportunity_fit"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

        app = create_app(data_dir=isolated_data)
        with _running_server(app) as base_url:
            with _full_verify_client(base_url, timeout_seconds=180.0) as client:
                created = client.post(
                    "/api/applications",
                    json={
                        "company_name": "targeted opportunity fit",
                        "position_name": "Verification Engineer",
                        "status": "applied",
                    },
                )
                if created.status_code != 201:
                    raise RuntimeError("targeted application setup failed")
                application_id = int(created.json()["id"])

                resume = client.post(
                    "/api/resumes",
                    json={
                        "title": "Targeted Opportunity Resume",
                        "text": RESUME_CONTENT["raw_text"],
                        "content_json": RESUME_CONTENT,
                    },
                )
                if resume.status_code != 201:
                    raise RuntimeError("targeted resume setup failed")
                resume_id = int(resume.json()["id"])
                resume_ids.append(resume_id)
                resume_title = str(resume.json().get("title") or "Targeted Opportunity Resume")

                event = client.post(
                    "/api/application-events",
                    json={
                        "application_id": application_id,
                        "event_type": "interview",
                        "subtype": "technical",
                        "round": 1,
                        "scheduled_at": TARGET_EVENT_AT,
                        "duration_minutes": 45,
                        "location": "targeted diagnostic",
                    },
                )
                if event.status_code != 201:
                    raise RuntimeError("targeted event setup failed")
                event_id = int(event.json()["id"])

                application_response = client.get(f"/api/applications/{application_id}")
                if application_response.status_code != 200:
                    raise RuntimeError("targeted application read failed")
                application = application_response.json()

                jd = client.post(
                    f"/api/applications/{application_id}/job-description/versions",
                    json={
                        "jd_text": TARGET_JD,
                        "source_url": None,
                        "expected_current_version_id": None,
                        "idempotency_key": f"opportunity-fit-deep-targeted-{uuid4()}",
                    },
                )
                if jd.status_code != 201:
                    raise RuntimeError("targeted JD setup failed")
                jd_version_id = int(jd.json()["id"])
                snapshot = build_source_snapshot(
                    application_id=application_id,
                    company_name=str(application.get("company_name") or ""),
                    position_name=str(application.get("position_name") or ""),
                    resume_id=resume_id,
                    resume_title=resume_title,
                    resume_content=RESUME_CONTENT,
                    jd_text=TARGET_JD,
                    jd_source_label=JD_SOURCE_LABEL,
                    candidate_assertions=[TARGET_ASSERTION],
                )
                expected_source_fingerprint = sha256_text(canonical_json(snapshot))

                triage = client.post(
                    f"/api/applications/{application_id}/opportunity-fit-reviews",
                    json={
                        "schema_version": 2,
                        "resume_id": resume_id,
                        "jd_version_id": jd_version_id,
                        "jd_source_label": JD_SOURCE_LABEL,
                        "candidate_assertions": [TARGET_ASSERTION],
                        "idempotency_key": triage_idempotency_key,
                    },
                )
                api_statuses["triage"] = triage.status_code
                error_code = _safe_error_code(triage)
                if error_code:
                    error_codes["triage"] = error_code
                triage_body = triage.json()
                response_summaries["triage"] = _response_summary(triage_body)
                if triage.status_code != 201:
                    failure_category = "triage_http_error"
                    raise RuntimeError("opportunity fit triage API did not return 201")
                _validate_opportunity_fit_v2_stage_response(
                    triage_body,
                    application_id=application_id,
                    resume_id=resume_id,
                    expected_stage="triage",
                    expected_status="ready",
                    snapshot=snapshot,
                )
                if not isinstance(triage_body, dict):
                    raise RuntimeError("opportunity fit triage response was not an object")
                review_id = int(triage_body["review_id"])
                stage_id = int(triage_body["stage_id"])
                triage_stage = _safe_stage(triage_body)
                confirmation_token = triage_body.get("confirmation_token")
                confirmation_token_hash = _hash_token(confirmation_token)
                triage_stage["confirmation_token_hash"] = confirmation_token_hash

                confirmed = client.post(
                    f"/api/applications/{application_id}/opportunity-fit-reviews/{review_id}/triage/{stage_id}/confirm",
                    json={"confirmation_token": confirmation_token},
                )
                api_statuses["confirm"] = confirmed.status_code
                error_code = _safe_error_code(confirmed)
                if error_code:
                    error_codes["confirm"] = error_code
                confirmed_body = confirmed.json()
                response_summaries["confirm"] = _response_summary(confirmed_body)
                if confirmed.status_code != 200:
                    failure_category = "confirm_http_error"
                    raise RuntimeError("opportunity fit triage confirmation did not return 200")

                before = client.get(
                    f"/api/applications/{application_id}/opportunity-fit-reviews/{review_id}?schema_version=2"
                )
                api_statuses["history_before_deep"] = before.status_code
                error_code = _safe_error_code(before)
                if error_code:
                    error_codes["history_before_deep"] = error_code
                history_before_deep = _safe_history(before.json())
                if before.status_code != 200:
                    failure_category = "history_before_deep_http_error"
                    raise RuntimeError("opportunity fit pre-deep history did not return 200")

                deep = client.post(
                    f"/api/applications/{application_id}/opportunity-fit-reviews/{review_id}/deep-review",
                    json={
                        "schema_version": 2,
                        "resume_id": resume_id,
                        "jd_source_label": JD_SOURCE_LABEL,
                        "candidate_assertions": [TARGET_ASSERTION],
                        "idempotency_key": deep_idempotency_key,
                        "parent_triage_stage_id": stage_id,
                    },
                )
                api_statuses["deep"] = deep.status_code
                error_code = _safe_error_code(deep)
                if error_code:
                    error_codes["deep"] = error_code
                deep_body = deep.json()
                response_summaries["deep"] = _response_summary(deep_body)

                after = client.get(
                    f"/api/applications/{application_id}/opportunity-fit-reviews/{review_id}?schema_version=2"
                )
                api_statuses["history_after_deep"] = after.status_code
                error_code = _safe_error_code(after)
                if error_code:
                    error_codes["history_after_deep"] = error_code
                history_after_deep = _safe_history(after.json())
                if after.status_code != 200:
                    failure_category = "history_after_deep_http_error"
                    raise RuntimeError("opportunity fit post-deep history did not return 200")

                if deep.status_code == 201:
                    _validate_opportunity_fit_v2_stage_response(
                        deep_body,
                        application_id=application_id,
                        resume_id=resume_id,
                        expected_stage="deep_review",
                        expected_status="ready",
                        expected_parent_stage_id=stage_id,
                        snapshot=snapshot,
                    )
                else:
                    failure_category = f"deep_http_{deep.status_code}"
                    raise RuntimeError("opportunity fit deep review did not return 201")
                exit_code = 0
    except Exception as exc:
        stderr = type(exc).__name__
        if failure_category is None:
            failure_category = "network_timeout" if "timeout" in stderr.lower() else "process_error"
    finally:
        if application_id is not None:
            try:
                _cleanup_real_ai_smoke_records(isolated_data, application_id, resume_ids)
            except Exception:
                pass

        operation_records = _operation_records(report_dir)
        provider_records = _provider_records(report_dir)
        request_records = _read_request_audit(report_dir)
        if failure_category is None:
            for record in reversed(operation_records):
                category = record.get("error_category")
                if isinstance(category, str) and category:
                    failure_category = category
                    break

        triage_after = _stage_from_history(history_after_deep, "triage")
        deep_after = _stage_from_history(history_after_deep, "deep_review")
        triage_before = _stage_from_history(history_before_deep, "triage")
        deep_before = _stage_from_history(history_before_deep, "deep_review")
        inner = {
            "status": "passed" if exit_code == 0 else "failed",
            "stage": "opportunity_fit",
            "targeted_phase": "deep_review_request",
            "failure_category": failure_category,
            "error_codes": error_codes,
            "application_id": application_id,
            "event_id": event_id,
            "jd_version_id": triage_stage.get("jd_version_id"),
            "review_id": review_id,
            "triage_stage_id": stage_id,
            "triage_confirmation_token_hash": confirmation_token_hash,
            "triage_idempotency_key": triage_idempotency_key,
            "deep_request": {
                "review_id": review_id,
                "parent_triage_stage_id": stage_id,
                "idempotency_key": deep_idempotency_key,
                "source_fingerprint_sha256": expected_source_fingerprint,
            },
            "api_statuses": api_statuses,
            "response_summaries": response_summaries,
            "triage_confirmation_consumed": (
                triage_after.get("stage_status") == "confirmed"
                or triage_before.get("stage_status") == "confirmed"
            ),
            "confirmation_token_reuse_attempted": False,
            "deep_idempotency_reused_before_request": bool(deep_before),
            "deep_stage_exists_after_request": bool(deep_after),
            "source_fingerprint_checks": {
                "triage_matches_expected": triage_stage.get("source_fingerprint_sha256")
                == expected_source_fingerprint,
                "pre_deep_history_matches_expected": triage_before.get("source_fingerprint_sha256")
                == expected_source_fingerprint,
                "deep_matches_expected": deep_after.get("source_fingerprint_sha256")
                == expected_source_fingerprint
                if deep_after
                else False,
            },
            "operation_records": operation_records,
            "provider_records": provider_records,
            "request_fingerprints": [
                {
                    "operation": item.get("operation"),
                    "input_fingerprint_sha256": item.get("input_fingerprint_sha256", ""),
                    "schema_fingerprint_sha256": item.get("schema_fingerprint_sha256", ""),
                }
                for item in request_records
            ],
            "provider_request_id_hashes": [
                item.get("provider_request_id_hash", "")
                for item in provider_records
                if item.get("provider_request_id_hash")
            ],
            "client_timeout_seconds": 180,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "exception_category": _smoke_exception_category(
                RuntimeError(stderr) if stderr else None
            ),
        }
        _write_json(report_dir / "full-verify-inner-diagnostic.json", inner)
        summary = _build_summary(
            config_summary=config_summary
            or {
                "config_path": str(isolated_data / "config.json"),
                "offerpilot_data": str(isolated_data),
                "provider": "",
                "model": model,
            },
            child_data_dir=isolated_data,
            report_dir=report_dir,
            exit_code=exit_code,
            stdout="",
            stderr=stderr,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            inner_diagnostic=inner,
            formal_config_unchanged=source_hash_before
            == (source_config.read_bytes() if source_config.is_file() else b""),
        )
        summary.update(
            {
                "targeted_operation": "opportunity_fit",
                "targeted_phase": "deep_review_request",
                "client_timeout_seconds": 180,
                "application_id": application_id,
                "event_id": event_id,
                "jd_version_id": triage_stage.get("jd_version_id"),
                "review_id": review_id,
                "triage_stage_id": stage_id,
                "error_codes": error_codes,
                "api_statuses": api_statuses,
                "triage_confirmation_token_hash": confirmation_token_hash,
                "triage_idempotency_key": triage_idempotency_key,
                "deep_request": inner["deep_request"],
                "triage_confirmation_consumed": inner["triage_confirmation_consumed"],
                "confirmation_token_reuse_attempted": False,
                "deep_idempotency_reused_before_request": inner[
                    "deep_idempotency_reused_before_request"
                ],
                "deep_stage_exists_after_request": inner["deep_stage_exists_after_request"],
                "source_fingerprint_checks": inner["source_fingerprint_checks"],
                "response_summaries": response_summaries,
            }
        )
        _write_json(report_dir / "targeted-summary.json", summary)
        shutil.rmtree(isolated_data, ignore_errors=True)
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one isolated real-AI opportunity-fit Deep Review diagnostic."
    )
    parser.add_argument("--source-data", type=Path, default=resolve_data_dir())
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4-pro")
    args = parser.parse_args()
    summary = run_targeted_diagnostic(
        source_data=args.source_data,
        report_dir=args.report_dir.resolve(),
        model=args.model,
    )
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":")))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
