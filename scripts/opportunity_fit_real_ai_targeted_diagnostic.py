from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from offerpilot.ai.opportunity_fit_reviews import (
    OpportunityFitModelError,
    build_source_snapshot,
    validate_triage_v2,
)
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


def _safe_response_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"payload_type": type(value).__name__, "top_level_keys": []}
    summary: dict[str, Any] = {
        "payload_type": "object",
        "top_level_keys": sorted(key for key in value if isinstance(key, str))[:32],
    }
    proposal = value.get("proposal")
    if isinstance(proposal, dict):
        summary["proposal_keys"] = sorted(key for key in proposal if isinstance(key, str))[:32]
    return summary


def _classify_response_validation(
    body: object,
    snapshot: dict[str, Any],
) -> str:
    if not isinstance(body, dict):
        return "response_contract"
    expected_source_hash = sha256_text(canonical_json(snapshot))
    if body.get("source_fingerprint_sha256") != expected_source_hash:
        return "response_source_fingerprint"
    proposal = body.get("proposal")
    if not isinstance(proposal, dict):
        return "response_proposal_shape"
    if body.get("proposal_sha256") != sha256_text(canonical_json(proposal)):
        return "response_proposal_fingerprint"
    try:
        validate_triage_v2(proposal, snapshot)
    except OpportunityFitModelError as exc:
        category = exc.failure_category
        if isinstance(category, str) and category.replace("_", "").isalnum():
            return f"response_proposal_{category}"
        return "response_proposal_invalid"
    return "response_contract"


def _response_contract_checks(
    body: object,
    *,
    application_id: int,
    resume_id: int,
) -> dict[str, bool]:
    if not isinstance(body, dict):
        return {"root_object": False}
    expected_fields = {
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
        "created_at",
        "proposal",
        "confirmation_token",
    }
    ownership = all(
        type(body.get(field)) is int and body.get(field) == expected
        for field, expected in (("application_id", application_id), ("resume_id", resume_id))
    )
    metadata = (
        type(body.get("id")) is int
        and body["id"] > 0
        and type(body.get("review_id")) is int
        and body["review_id"] > 0
        and body.get("stage_id") == body.get("id")
        and body.get("schema_version") == 2
        and body.get("stage") == "triage"
        and body.get("stage_status") == "ready"
        and body.get("parent_triage_stage_id") is None
    )
    return {
        "root_fields": set(body) == expected_fields,
        "ownership": ownership,
        "metadata": metadata,
        "idempotency": isinstance(body.get("idempotency_key"), str)
        and bool(body["idempotency_key"]),
        "hashes": all(
            isinstance(body.get(field), str) and len(body[field]) == 64
            for field in ("source_fingerprint_sha256", "proposal_sha256")
        ),
        "created_at": isinstance(body.get("created_at"), str) and bool(body["created_at"]),
        "proposal_object": isinstance(body.get("proposal"), dict),
        "confirmation_token": isinstance(body.get("confirmation_token"), str)
        and bool(body["confirmation_token"]),
    }


def _target_operation_record(report_dir: Path) -> dict[str, Any]:
    records = [
        record
        for record in _read_operation_audit(report_dir)
        if record.get("kind") == "api_request"
        and record.get("operation") == "opportunity_fit"
        and record.get("path", "").endswith("/opportunity-fit-reviews")
    ]
    return records[-1] if records else {}


def _target_provider_record(report_dir: Path) -> dict[str, Any]:
    records = [
        record
        for record in _read_operation_audit(report_dir)
        if record.get("kind") == "provider_request_result"
        and record.get("operation") == "opportunity_fit"
    ]
    return records[-1] if records else {}


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
        "provider-request-audit.jsonl",
        "full-verify-operation-audit.jsonl",
    ):
        (report_dir / artifact).unlink(missing_ok=True)

    isolated_data = Path(tempfile.mkdtemp(prefix="offerpilot-opportunity-fit-targeted-"))
    source_config = source_data / "config.json"
    source_hash_before = source_config.read_bytes() if source_config.is_file() else b""
    previous_env = {
        key: os.environ.get(key)
        for key in (
            "OFFERPILOT_DATA",
            "OFFERPILOT_FULL_VERIFY_REPORT_DIR",
            "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE",
            "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE",
            "OFFERPILOT_FULL_VERIFY_OPERATION",
            "OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE",
            "NO_PROXY",
        )
    }
    started = time.perf_counter()
    application_id: int | None = None
    resume_ids: list[int] = []
    api_status: int | None = None
    response_summary: dict[str, Any] = {}
    failure_category: str | None = None
    stderr = ""
    exit_code = 1
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
                "targeted_phase": "triage_request",
                "client_timeout_seconds": 180,
                "config": config_summary,
                "child_env": {
                    "OFFERPILOT_DATA": str(isolated_data),
                    "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
                    "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(request_audit),
                    "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(operation_audit),
                },
                "source_fixture": "synthetic_resume_and_pasted_jd",
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
                        "text": "Built API services and led migration.",
                        "content_json": {
                            "raw_text": "Built API services and led migration.",
                            "skills": ["Python"],
                        },
                    },
                )
                if resume.status_code != 201:
                    raise RuntimeError("targeted resume setup failed")
                resume_id = int(resume.json()["id"])
                resume_ids.append(resume_id)
                resume_title = str(resume.json().get("title") or "Targeted Opportunity Resume")

                jd = client.post(
                    f"/api/applications/{application_id}/job-description/versions",
                    json={
                        "jd_text": TARGET_JD,
                        "source_url": None,
                        "expected_current_version_id": None,
                        "idempotency_key": "opportunity-fit-targeted-0001",
                    },
                )
                if jd.status_code != 201:
                    raise RuntimeError("targeted JD setup failed")
                jd_version_id = int(jd.json()["id"])
                snapshot = build_source_snapshot(
                    application_id=application_id,
                    company_name="targeted opportunity fit",
                    position_name="Verification Engineer",
                    resume_id=resume_id,
                    resume_title=resume_title,
                    resume_content={
                        "raw_text": "Built API services and led migration.",
                        "skills": ["Python"],
                    },
                    jd_text=TARGET_JD,
                    jd_source_label="Targeted pasted JD",
                    candidate_assertions=[TARGET_ASSERTION],
                )
                response = client.post(
                    f"/api/applications/{application_id}/opportunity-fit-reviews",
                    json={
                        "schema_version": 2,
                        "resume_id": resume_id,
                        "jd_version_id": jd_version_id,
                        "jd_source_label": "Targeted pasted JD",
                        "candidate_assertions": [TARGET_ASSERTION],
                        "idempotency_key": "f36f6d0b-1d1e-4e9a-aec1-9fef6b2f3b90",
                    },
                )
                api_status = response.status_code
                response_body = response.json()
                response_summary = _safe_response_summary(response_body)
                if response.status_code != 201:
                    failure_category = "http_error"
                    raise RuntimeError("opportunity fit triage API did not return 201")
                if isinstance(response_body, dict):
                    response_summary["fingerprints"] = {
                        "source_matches": response_body.get("source_fingerprint_sha256")
                        == sha256_text(canonical_json(snapshot)),
                        "proposal_matches": isinstance(response_body.get("proposal"), dict)
                        and response_body.get("proposal_sha256")
                        == sha256_text(canonical_json(response_body["proposal"])),
                    }
                    response_summary["contract_checks"] = _response_contract_checks(
                        response_body,
                        application_id=application_id,
                        resume_id=resume_id,
                    )
                try:
                    _validate_opportunity_fit_v2_stage_response(
                        response_body,
                        application_id=application_id,
                        resume_id=resume_id,
                        expected_stage="triage",
                        expected_status="ready",
                        snapshot=snapshot,
                    )
                except RuntimeError:
                    checks = response_summary.get("contract_checks")
                    if isinstance(checks, dict):
                        failed_check = next(
                            (key for key, passed in checks.items() if passed is False),
                            None,
                        )
                        failure_category = (
                            f"response_{failed_check}"
                            if isinstance(failed_check, str)
                            else _classify_response_validation(response_body, snapshot)
                        )
                    else:
                        failure_category = _classify_response_validation(response_body, snapshot)
                    raise
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
        operation = _target_operation_record(report_dir)
        provider = _target_provider_record(report_dir)
        request_records = _read_request_audit(report_dir)
        target_request = request_records[-1] if request_records else {}
        if failure_category is None and operation.get("error_category"):
            failure_category = str(operation["error_category"])
        inner = {
            "status": "passed" if exit_code == 0 else "failed",
            "stage": "opportunity_fit",
            "targeted_phase": "triage_request",
            "failure_category": failure_category,
            "provider_request_id_hash": provider.get("provider_request_id_hash", ""),
            "duration_ms": operation.get("duration_ms") or provider.get("elapsed_ms", 0),
            "api_status": api_status,
            "response_summary": response_summary,
            "operation": operation,
            "provider": {
                "provider_id": provider.get("provider_id"),
                "provider_type": provider.get("provider_type"),
                "model": provider.get("model"),
                "elapsed_ms": provider.get("elapsed_ms"),
                "provider_request_id_hash": provider.get("provider_request_id_hash", ""),
            },
            "input_fingerprint_sha256": target_request.get("input_fingerprint_sha256", ""),
            "schema_fingerprint_sha256": target_request.get("schema_fingerprint_sha256", ""),
            "client_timeout_seconds": 180,
            "exception_category": _smoke_exception_category(
                RuntimeError(stderr) if stderr else None
            ),
        }
        _write_json(report_dir / "full-verify-inner-diagnostic.json", inner)
        summary = _build_summary(
            config_summary=locals().get("config_summary") or {
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
        summary["targeted_operation"] = "opportunity_fit"
        summary["targeted_phase"] = "triage_request"
        summary["client_timeout_seconds"] = 180
        summary["api_status"] = api_status
        summary["response_summary"] = response_summary
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
        description="Run one isolated real-AI opportunity-fit triage diagnostic."
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
