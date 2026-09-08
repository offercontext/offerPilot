from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from offerpilot.api import create_app
from offerpilot.config import resolve_data_dir
from offerpilot.models import ApplicationMaterialKit
from offerpilot.repositories.json_contract import canonical_json
from offerpilot.smoke import (
    _cleanup_real_ai_smoke_records,
    _full_verify_client,
    _read_material_proposal_smoke_diagnostic,
    _running_server,
    _safe_inner_log_diagnostics,
    _smoke_exception_category,
    _validate_material_proposal_smoke_response,
)
from scripts.full_real_ai_verify import (
    _build_summary,
    _prepare_temp_config,
    _safe_config_summary,
    _write_json,
)
from offerpilot.db import session_factory_for_data_dir


TARGET_JD = "Evidence QA Engineer: build reliable API quality workflows."


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
        "material-proposal-diagnostic.json",
        "provider-request-audit.jsonl",
        "full-verify-operation-audit.jsonl",
    ):
        (report_dir / artifact).unlink(missing_ok=True)

    isolated_data = Path(tempfile.mkdtemp(prefix="offerpilot-material-proposal-targeted-"))
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
    exit_code = 1
    stderr = ""
    try:
        _prepare_temp_config(source_data, isolated_data, model)
        config_summary = _safe_config_summary(isolated_data / "config.json", isolated_data)
        operation_audit = report_dir / "full-verify-operation-audit.jsonl"
        request_audit = report_dir / "provider-request-audit.jsonl"
        _write_json(
            report_dir / "targeted-start.json",
            {
                "status": "started",
                "stage": "material_proposal",
                "operation": "material_proposal",
                "config": config_summary,
                "child_env": {
                    "OFFERPILOT_DATA": str(isolated_data),
                    "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
                    "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(request_audit),
                    "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(operation_audit),
                },
                "source_fixture": "synthetic_resume_and_material_kit",
            },
        )
        os.environ["OFFERPILOT_DATA"] = str(isolated_data)
        os.environ["OFFERPILOT_FULL_VERIFY_REPORT_DIR"] = str(report_dir)
        os.environ["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = str(request_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE"] = str(operation_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION"] = "material_proposal"
        os.environ["OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE"] = "material_proposal"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

        app = create_app(data_dir=isolated_data)
        with _running_server(app) as base_url:
            with _full_verify_client(base_url, timeout_seconds=180.0) as client:
                created = client.post(
                    "/api/applications",
                    json={
                        "company_name": "targeted material proposal",
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
                        "title": "Targeted Material Resume",
                        "text": "Built API services. Led migration.",
                        "content_json": {
                            "experience": [
                                {
                                    "company": "Acme Labs",
                                    "highlights": ["Built API services", "Led migration"],
                                }
                            ],
                            "skills": ["Python"],
                            "raw_text": "Built API services. Led migration.",
                        },
                    },
                )
                if resume.status_code != 201:
                    raise RuntimeError("targeted resume setup failed")
                resume_id = int(resume.json()["id"])
                resume_ids.append(resume_id)

                jd = client.post(
                    f"/api/applications/{application_id}/job-description/versions",
                    json={
                        "jd_text": TARGET_JD,
                        "source_url": None,
                        "expected_current_version_id": None,
                        "idempotency_key": "material-proposal-targeted-0001",
                    },
                )
                if jd.status_code != 201:
                    raise RuntimeError("targeted JD setup failed")
                jd_version_id = int(jd.json()["id"])

                session_factory = session_factory_for_data_dir(isolated_data)
                try:
                    with session_factory() as session:
                        session.add(
                            ApplicationMaterialKit(
                                application_id=application_id,
                                resume_id=resume_id,
                                jd_snapshot=TARGET_JD,
                                jd_version_id=jd_version_id,
                                status="draft",
                                content_json=canonical_json(
                                    {"summary": "Evidence-backed API quality material kit"}
                                ),
                            )
                        )
                        session.commit()
                finally:
                    bind = session_factory.kw.get("bind")
                    if bind is not None:
                        bind.dispose()

                proposal = client.post(
                    f"/api/applications/{application_id}/material-revision-proposals",
                    json={
                        "instructions": "Prefer only safe evidence-backed changes.",
                        "user_assertions": ["I led the migration."],
                    },
                )
                api_status = proposal.status_code
                if proposal.status_code != 201:
                    raise RuntimeError("material proposal API did not return 201")
                _validate_material_proposal_smoke_response(proposal.json())
                exit_code = 0
    except Exception as exc:
        stderr = type(exc).__name__
    finally:
        if application_id is not None:
            try:
                _cleanup_real_ai_smoke_records(isolated_data, application_id, resume_ids)
            except Exception:
                pass
        response_diagnostic = _read_material_proposal_smoke_diagnostic(report_dir)
        inner_diagnostics = _safe_inner_log_diagnostics(isolated_data)
        last = response_diagnostic or (inner_diagnostics[-1] if inner_diagnostics else {})
        generation_diagnostic = next(
            (
                diagnostic
                for diagnostic in reversed(inner_diagnostics)
                if diagnostic.get("kind") == "material_proposal"
            ),
            {},
        )
        inner = {
            "status": "passed" if exit_code == 0 else "failed",
            "stage": "material_proposal",
            "exception_category": _smoke_exception_category(
                RuntimeError(stderr) if stderr else None
            ),
            "failure_category": last.get("failure_category"),
            "failure_categories": last.get("failure_categories", []),
            "structure_summaries": last.get("structure_summaries", []),
            "evidence_counts": last.get("evidence_counts", {}),
            "repair_attempted": last.get("repair_attempted", False),
            "retry_count": last.get("retry_count", 0),
            "duration_ms": last.get("duration_ms") or generation_diagnostic.get("duration_ms", 0),
            "provider_request_id_hash": last.get("provider_request_id_hash")
            or generation_diagnostic.get("provider_request_id_hash", ""),
        }
        if response_diagnostic:
            inner["material_proposal_diagnostic"] = response_diagnostic
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
        summary["targeted_operation"] = "material_proposal"
        summary["material_api_status"] = api_status
        if response_diagnostic:
            summary["material_proposal_diagnostic"] = response_diagnostic
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
        description="Run one isolated real-AI material-proposal API diagnostic."
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
