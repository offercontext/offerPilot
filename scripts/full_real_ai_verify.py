from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlparse

from offerpilot.config import resolve_data_dir


_DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "offerpilot-full-real-ai-report"
_SAFE_HASH = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REQUEST_ID_HASH = re.compile(r"^[0-9a-f]{12,64}$")
_SAFE_CATEGORY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _prepare_temp_config(source_data: Path, isolated_data: Path, model: str) -> None:
    source_config_path = source_data / "config.json"
    raw = json.loads(source_config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("provider config must be an object")
    isolated = copy.deepcopy(raw)
    providers = isolated.get("providers")
    if isinstance(providers, list) and providers:
        active_id = isolated.get("active_provider_id")
        active = next(
            (
                item
                for item in providers
                if isinstance(item, dict) and item.get("id") == active_id
            ),
            None,
        )
        if active is None:
            active = next((item for item in providers if isinstance(item, dict)), None)
        if active is None:
            raise RuntimeError("provider config has no usable provider profile")
        active["model"] = model
    else:
        isolated["model"] = model
    isolated_data.mkdir(parents=True, exist_ok=True)
    (isolated_data / "config.json").write_text(
        json.dumps(isolated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_endpoint(value: Any) -> dict[str, Any]:
    try:
        parsed = urlparse(str(value or ""))
        if not parsed.scheme or not parsed.hostname:
            return {}
        return {
            "scheme": parsed.scheme,
            "host": parsed.hostname,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
        }
    except (TypeError, ValueError):
        return {}


def _safe_config_summary(config_path: Path, data_dir: Path) -> dict[str, Any]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("provider config must be an object")
    providers = raw.get("providers")
    active_id = raw.get("active_provider_id")
    profile: dict[str, Any] | None = None
    if isinstance(providers, list):
        for candidate in providers:
            if isinstance(candidate, dict) and candidate.get("id") == active_id:
                profile = candidate
                break
        if profile is None:
            profile = next((item for item in providers if isinstance(item, dict)), None)
    if profile is None:
        profile = raw
    return {
        "config_path": str(config_path),
        "offerpilot_data": str(data_dir),
        "active_provider_id": str(active_id or profile.get("id") or "default"),
        "provider": str(profile.get("provider") or ""),
        "model": str(profile.get("model") or raw.get("model") or ""),
        "supports_json_schema": bool(profile.get("supports_json_schema", False)),
        "endpoint": _safe_endpoint(profile.get("base_url") or raw.get("base_url")),
    }


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_request_audit(report_dir: Path) -> list[dict[str, Any]]:
    path = report_dir / "provider-request-audit.jsonl"
    if not path.is_file():
        return []
    allowed = {
        "kind",
        "operation",
        "provider_id",
        "provider_type",
        "model",
        "litellm_model",
        "endpoint",
        "input_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "request_body_bytes",
        "request_body_scope",
        "message_count",
        "message_bytes",
        "response_mode",
        "explicit_max_tokens",
        "explicit_timeout_seconds",
    }
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("kind") != "provider_request_metadata":
            continue
        record = {key: value[key] for key in allowed if key in value}
        fingerprint = record.get("input_fingerprint_sha256")
        if not isinstance(fingerprint, str) or not _SAFE_HASH.fullmatch(fingerprint):
            record.pop("input_fingerprint_sha256", None)
        schema_fingerprint = record.get("schema_fingerprint_sha256")
        if not isinstance(schema_fingerprint, str) or not _SAFE_HASH.fullmatch(schema_fingerprint):
            record.pop("schema_fingerprint_sha256", None)
        records.append(record)
    return records[-64:]


def _request_audit_is_complete(records: list[dict[str, Any]]) -> bool:
    required = {
        "kind",
        "provider_id",
        "provider_type",
        "model",
        "litellm_model",
        "endpoint",
        "input_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "request_body_bytes",
        "request_body_scope",
        "message_count",
        "message_bytes",
        "response_mode",
        "explicit_max_tokens",
        "explicit_timeout_seconds",
    }
    if not records:
        return False
    for record in records:
        if not required.issubset(record):
            return False
        if record["response_mode"] not in {"text_json", "json_schema"}:
            return False
        for field in ("message_count", "message_bytes", "request_body_bytes"):
            value = record[field]
            if type(value) is not int or value < 0:
                return False
    return True


def _read_operation_audit(report_dir: Path) -> list[dict[str, Any]]:
    path = report_dir / "full-verify-operation-audit.jsonl"
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        kind = value.get("kind")
        if kind == "api_request":
            record = {
                key: value.get(key)
                for key in (
                    "kind",
                    "operation",
                    "method",
                    "path",
                    "http_status",
                    "duration_ms",
                    "response_attempt_status",
                    "error_category",
                )
            }
            if not isinstance(record["operation"], str) or not _SAFE_CATEGORY.fullmatch(
                record["operation"]
            ):
                continue
            if not isinstance(record["method"], str) or not re.fullmatch(
                r"[A-Z]{3,7}", record["method"]
            ):
                continue
            if not isinstance(record["path"], str) or not record["path"].startswith("/"):
                continue
            if type(record["duration_ms"]) is not int or record["duration_ms"] < 0:
                continue
            if record["http_status"] is not None and (
                type(record["http_status"]) is not int or record["http_status"] < 100
            ):
                continue
            if record["error_category"] is not None and (
                not isinstance(record["error_category"], str)
                or not _SAFE_CATEGORY.fullmatch(record["error_category"])
            ):
                record["error_category"] = None
            records.append(record)
        elif kind == "provider_request_result":
            record = {
                key: value.get(key)
                for key in (
                    "kind",
                    "operation",
                    "provider_id",
                    "provider_type",
                    "model",
                    "status",
                    "elapsed_ms",
                    "http_status",
                    "provider_request_id_hash",
                    "failure_category",
                )
            }
            if record["provider_id"] is None:
                record.pop("provider_id")
            if not isinstance(record["operation"], str) or not _SAFE_CATEGORY.fullmatch(
                record["operation"]
            ):
                continue
            if record["status"] not in {"success", "error"}:
                continue
            if type(record["elapsed_ms"]) is not int or record["elapsed_ms"] < 0:
                continue
            request_id_hash = record["provider_request_id_hash"]
            if not isinstance(request_id_hash, str) or (
                request_id_hash and not _SAFE_REQUEST_ID_HASH.fullmatch(request_id_hash)
            ):
                record["provider_request_id_hash"] = ""
            category = record["failure_category"]
            if category is not None and (
                not isinstance(category, str) or not _SAFE_CATEGORY.fullmatch(category)
            ):
                record["failure_category"] = None
            records.append(record)
    return records[-128:]


def _safe_inner_diagnostic(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "status",
        "stage",
        "failure_category",
        "failure_categories",
        "structure_summaries",
        "evidence_counts",
        "repair_attempted",
        "retry_count",
        "duration_ms",
        "provider_request_id_hash",
        "exception_category",
    ):
        if key in value:
            result[key] = value[key]
    category = result.get("failure_category")
    if (
        not isinstance(category, str)
        or category == "none"
        or not _SAFE_CATEGORY.fullmatch(category)
    ):
        result.pop("failure_category", None)
    categories = result.get("failure_categories")
    if isinstance(categories, list):
        result["failure_categories"] = [
            item
            for item in categories
            if isinstance(item, str) and item != "none" and _SAFE_CATEGORY.fullmatch(item)
        ][:4]
    else:
        result.pop("failure_categories", None)
    request_id_hash = result.get("provider_request_id_hash")
    if not isinstance(request_id_hash, str) or not _SAFE_REQUEST_ID_HASH.fullmatch(request_id_hash):
        result.pop("provider_request_id_hash", None)
    if not isinstance(result.get("structure_summaries"), list):
        result.pop("structure_summaries", None)
    if not isinstance(result.get("evidence_counts"), dict):
        result.pop("evidence_counts", None)
    if not isinstance(result.get("repair_attempted"), bool):
        result.pop("repair_attempted", None)
    for key in ("retry_count", "duration_ms"):
        if key in result and (type(result[key]) is not int or result[key] < 0):
            result.pop(key, None)
    return result


def _exception_category(stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if "timeout" in text or "readtimeout" in text:
        return "network_timeout"
    if "provider" in text or "ai service" in text:
        return "provider_error"
    if "evidence" in text or "proposal" in text or "contract" in text:
        return "contract_or_quality"
    if "traceback" in text or "runtimeerror" in text:
        return "process_error"
    return "unknown"


def _last_completed_step(stdout: str) -> str:
    matches = re.findall(r"^ok ([A-Za-z0-9_-]+):", stdout, flags=re.MULTILINE)
    return matches[-1] if matches else "startup"


def _build_summary(
    *,
    config_summary: dict[str, Any],
    child_data_dir: Path,
    report_dir: Path,
    exit_code: int,
    stdout: str,
    stderr: str,
    elapsed_ms: int,
    inner_diagnostic: dict[str, Any] | None = None,
    formal_config_unchanged: bool | None = None,
) -> dict[str, Any]:
    request_records = _read_request_audit(report_dir)
    request_metadata_complete = _request_audit_is_complete(request_records)
    operation_records = _read_operation_audit(report_dir)
    actual_request = request_records[-1] if request_records else {}
    inner = _safe_inner_diagnostic(inner_diagnostic)
    failure_category = inner.get("failure_category")
    if not isinstance(failure_category, str) or not failure_category:
        inner_exception_category = inner.get("exception_category")
        failure_category = (
            inner_exception_category
            if isinstance(inner_exception_category, str) and inner_exception_category
            else (_exception_category(stdout, stderr) if exit_code else None)
        )
    categories = inner.get("failure_categories")
    if not isinstance(categories, list):
        categories = []
    if failure_category and failure_category not in categories:
        categories = [failure_category, *categories]
    request_id_hash = inner.get("provider_request_id_hash")
    if not isinstance(request_id_hash, str):
        request_id_hash = ""
    first_failed_operation = next(
        (
            record
            for record in operation_records
            if (
                record.get("kind") == "provider_request_result"
                and record.get("status") == "error"
            )
            or (
                record.get("kind") == "api_request"
                and (
                    record.get("error_category")
                    or (
                        isinstance(record.get("http_status"), int)
                        and record["http_status"] >= 500
                    )
                )
            )
        ),
        None,
    )
    if not request_id_hash and isinstance(first_failed_operation, dict):
        request_id_hash = str(first_failed_operation.get("provider_request_id_hash") or "")
    summary = {
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "stage": inner.get("stage") or "real_ai_http_verify",
        "last_completed_step": _last_completed_step(stdout),
        "provider": actual_request.get("provider_type") or config_summary.get("provider"),
        "provider_id": actual_request.get("provider_id") or config_summary.get("active_provider_id"),
        "model": actual_request.get("model") or config_summary.get("model"),
        "input_fingerprints": list(
            dict.fromkeys(
                record["input_fingerprint_sha256"]
                for record in request_records
                if isinstance(record.get("input_fingerprint_sha256"), str)
            )
        ),
        "schema_fingerprints": list(
            dict.fromkeys(
                record["schema_fingerprint_sha256"]
                for record in request_records
                if isinstance(record.get("schema_fingerprint_sha256"), str)
            )
        ),
        "provider_request_id_hash": request_id_hash,
        "failure_category": failure_category,
        "failure_categories": categories[:4],
        "elapsed_ms": max(0, int(elapsed_ms)),
        "provider_request_count": len(request_records),
        "request_metadata_complete": request_metadata_complete,
        "operation_count": len(operation_records),
        "operations": operation_records,
        "first_failed_operation": first_failed_operation,
        "repair_attempted": inner.get("repair_attempted", False),
        "retry_count": inner.get("retry_count", 0),
        "structure_summaries": inner.get("structure_summaries", []),
        "evidence_counts": inner.get("evidence_counts", {}),
        "child_env": {
            "OFFERPILOT_DATA": str(child_data_dir),
            "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
            "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(
                report_dir / "provider-request-audit.jsonl"
            ),
            "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(
                report_dir / "full-verify-operation-audit.jsonl"
            ),
        },
        "config": config_summary,
    }
    if exit_code == 0 and not request_metadata_complete:
        summary["status"] = "incomplete"
        summary["verification_error"] = "provider_request_metadata_incomplete"
    if formal_config_unchanged is not None:
        summary["formal_config_unchanged"] = formal_config_unchanged
    return summary


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _read_inner_diagnostic(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "full-verify-inner-diagnostic.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        process.terminate()


def run_full_verify(
    *,
    source_data: Path,
    static_dir: Path,
    report_dir: Path = _DEFAULT_REPORT_DIR,
    model: str = "deepseek-v4-pro",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    for artifact in (
        "full-real-ai-start.json",
        "full-real-ai-summary.json",
        "full-verify-inner-diagnostic.json",
        "material-proposal-diagnostic.json",
        "provider-request-audit.jsonl",
        "full-verify-operation-audit.jsonl",
    ):
        (report_dir / artifact).unlink(missing_ok=True)
    source_config = source_data / "config.json"
    source_hash_before = _sha256_file(source_config)
    isolated_data = Path(tempfile.mkdtemp(prefix="offerpilot-full-real-ai-"))
    process: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""
    exit_code = 1
    started = time.perf_counter()
    try:
        _prepare_temp_config(source_data, isolated_data, model)
        config_summary = _safe_config_summary(isolated_data / "config.json", isolated_data)
        _write_json(
            report_dir / "full-real-ai-start.json",
            {
                "status": "started",
                "stage": "real_ai_http_verify",
                "config": config_summary,
                "child_env": {
                    "OFFERPILOT_DATA": str(isolated_data),
                    "OFFERPILOT_FULL_VERIFY_REPORT_DIR": str(report_dir),
                    "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE": str(
                        report_dir / "provider-request-audit.jsonl"
                    ),
                    "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE": str(
                        report_dir / "full-verify-operation-audit.jsonl"
                    ),
                },
            },
        )
        child_env = os.environ.copy()
        child_env["OFFERPILOT_DATA"] = str(isolated_data)
        child_env["OFFERPILOT_FULL_VERIFY_REPORT_DIR"] = str(report_dir)
        child_env["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = str(
            report_dir / "provider-request-audit.jsonl"
        )
        child_env["OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE"] = str(
            report_dir / "full-verify-operation-audit.jsonl"
        )
        process = subprocess.Popen(
            ["uv", "run", "oc", "verify", "--profile", "real-ai", "--static-dir", str(static_dir)],
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            exit_code = int(process.returncode or 0)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            stderr = f"{stderr}\n{type(exc).__name__}"
            exit_code = 124
    except Exception as exc:
        stderr = f"{type(exc).__name__}"
        exit_code = 1
    finally:
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        source_hash_after = _sha256_file(source_config)
        config_summary = locals().get("config_summary") or {
            "config_path": str(isolated_data / "config.json"),
            "offerpilot_data": str(isolated_data),
            "provider": "",
            "model": model,
        }
        summary = _build_summary(
            config_summary=config_summary,
            child_data_dir=isolated_data,
            report_dir=report_dir,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=elapsed_ms,
            inner_diagnostic=_read_inner_diagnostic(report_dir),
            formal_config_unchanged=source_hash_before == source_hash_after,
        )
        _write_json(report_dir / "full-real-ai-summary.json", summary)
        shutil.rmtree(isolated_data, ignore_errors=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one redacted isolated Full real-AI verify.")
    parser.add_argument("--source-data", type=Path, default=resolve_data_dir())
    parser.add_argument("--static-dir", type=Path, default=Path("web/dist"))
    parser.add_argument("--report-dir", type=Path, default=_DEFAULT_REPORT_DIR)
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    summary = run_full_verify(
        source_data=args.source_data,
        static_dir=args.static_dir.resolve(),
        report_dir=args.report_dir.resolve(),
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":")))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
