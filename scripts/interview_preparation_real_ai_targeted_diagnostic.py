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
from offerpilot.smoke import (
    _cleanup_real_ai_smoke_records,
    _full_verify_client,
    _run_real_ai_interview_preparation_smoke,
    _running_server,
    _safe_inner_log_diagnostics,
    _smoke_exception_category,
)
from scripts.full_real_ai_verify import (
    _build_summary,
    _prepare_temp_config,
    _safe_config_summary,
    _write_json,
)


TARGET_CASE = "Design an API migration with safe rollback and observability."


def run_targeted_diagnostic(
    *,
    source_data: Path,
    report_dir: Path,
    model: str = "deepseek-v4-pro",
    jd_text: str = TARGET_CASE,
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
    isolated_data = Path(tempfile.mkdtemp(prefix="offerpilot-interview-preparation-targeted-"))
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
    exit_code = 1
    stdout = ""
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
                "stage": "interview_preparation",
                "operation": "interview_preparation",
                "config": config_summary,
                "child_env": {
                    "OFFERPILOT_DATA": str(isolated_data),
                    "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
                    "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(request_audit),
                    "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(operation_audit),
                },
                "jd_case": "second_full_verify_case",
            },
        )
        os.environ["OFFERPILOT_DATA"] = str(isolated_data)
        os.environ["OFFERPILOT_FULL_VERIFY_REPORT_DIR"] = str(report_dir)
        os.environ["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = str(request_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE"] = str(operation_audit)
        os.environ["OFFERPILOT_FULL_VERIFY_OPERATION"] = "interview_preparation"
        os.environ["OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE"] = "interview_preparation"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

        app = create_app(data_dir=isolated_data)
        with _running_server(app) as base_url:
            with _full_verify_client(base_url, timeout_seconds=180.0) as client:
                created = client.post(
                    "/api/applications",
                    json={
                        "company_name": "targeted interview preparation",
                        "position_name": "Verification Engineer",
                        "status": "applied",
                    },
                )
                if created.status_code != 201:
                    raise RuntimeError("targeted application setup failed")
                application_id = int(created.json()["id"])
                _run_real_ai_interview_preparation_smoke(
                    client,
                    [],
                    application_id,
                    resume_ids,
                    cases=[jd_text],
                )
                exit_code = 0
                client.delete(f"/api/applications/{application_id}")
                _cleanup_real_ai_smoke_records(isolated_data, application_id, resume_ids)
    except Exception as exc:
        stderr = type(exc).__name__
        if application_id is not None:
            try:
                _cleanup_real_ai_smoke_records(isolated_data, application_id, resume_ids)
            except Exception:
                pass
    finally:
        inner_diagnostics = _safe_inner_log_diagnostics(isolated_data)
        last = inner_diagnostics[-1] if inner_diagnostics else {}
        inner = {
            "status": "passed" if exit_code == 0 else "failed",
            "stage": "interview_preparation",
            "exception_category": _smoke_exception_category(
                RuntimeError(stderr) if stderr else None
            ),
            "failure_category": last.get("failure_category"),
            "failure_categories": last.get("failure_categories", []),
            "structure_summaries": last.get("structure_summaries", []),
            "repair_attempted": last.get("repair_attempted", False),
            "retry_count": last.get("retry_count", 0),
            "duration_ms": last.get("duration_ms", 0),
            "provider_request_id_hash": last.get("provider_request_id_hash", ""),
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
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            inner_diagnostic=inner,
            formal_config_unchanged=source_hash_before
            == (source_config.read_bytes() if source_config.is_file() else b""),
        )
        summary["targeted_operation"] = "interview_preparation"
        summary["targeted_jd_case"] = "second_full_verify_case"
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
        description="Run one isolated real-AI interview-preparation operation diagnostic."
    )
    parser.add_argument("--source-data", type=Path, default=resolve_data_dir())
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--jd-text", default=TARGET_CASE)
    args = parser.parse_args()
    summary = run_targeted_diagnostic(
        source_data=args.source_data,
        report_dir=args.report_dir.resolve(),
        model=args.model,
        jd_text=args.jd_text,
    )
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":")))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
