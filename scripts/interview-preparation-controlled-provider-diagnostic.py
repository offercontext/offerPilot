from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from offerpilot.api import create_app
from offerpilot.config import Config, load_config, resolve_data_dir, save_config
from offerpilot.db import session_factory_for_data_dir
from offerpilot.diagnostics import read_recent_log_entries
from offerpilot.models import InterviewPreparationProposal
from offerpilot.smoke import _run_real_ai_interview_preparation_smoke, _running_server


CONTROLLED_PROPOSAL = {
    "preparation_directions": [
        {
            "id": "controlled-direction",
            "text": "Review the cited resume experience.",
            "evidence_refs": [
                {
                    "source": "resume",
                    "path": "/raw_text",
                    "excerpt": "Built reliable API services; input_snapshot is a literal term here.",
                }
            ],
        }
    ],
    "story_prompts": [],
    "review_points": [],
    "interviewer_questions": [],
    "items_to_clarify": [],
}

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLED_PROVIDER_CALLS = 3
_CONTROLLED_PROVIDER_TYPE = "openai_compatible"
_CONTROLLED_MODEL = "controlled-interview-preparation"


class _ControlledProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict[str, object]] = []

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        request_id = f"controlled-interview-preparation-{len(self.calls) + 1}"
        self.calls.append(
            {
                "request_body_bytes": content_length,
                "response_status": 200,
                "request_id_hash": hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:12],
            }
        )
        payload = {
            "id": request_id,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(CONTROLLED_PROPOSAL, ensure_ascii=False),
                    }
                }
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.send_header("X-Request-ID", request_id)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        return


_GENERATION_DIAGNOSTIC_PATTERN = re.compile(
    r"^interview_preparation_generation "
    r"category=(?P<category>\S+) "
    r"failure_categories=(?P<categories>\[[^ ]*\]) "
    r"structure_summaries=(?P<summaries>\[[^ ]*\]) "
    r"repair_attempted=(?P<repair>true|false) "
    r"retry_count=(?P<retry>\d+) "
    r"duration_ms=(?P<duration>\d+) "
    r"provider_request_id_hash=(?P<request_id_hash>\S*)$"
)


def _read_redacted_generation_diagnostics(data_dir: Path) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for entry in read_recent_log_entries(data_dir, limit=500):
        message = entry.get("message")
        if not isinstance(message, str):
            continue
        match = _GENERATION_DIAGNOSTIC_PATTERN.fullmatch(message)
        if match is None:
            continue
        try:
            categories = json.loads(match.group("categories"))
            structure_summaries = json.loads(match.group("summaries"))
        except json.JSONDecodeError:
            continue
        if not isinstance(categories, list) or any(
            not isinstance(category, str) or len(category) > 64 for category in categories
        ):
            continue
        if not isinstance(structure_summaries, list):
            continue
        category = match.group("category")
        diagnostics.append(
            {
                "failure_category": None if category == "none" else category[:64],
                "failure_categories": categories[:2],
                "structure_summaries": structure_summaries[:2],
                "repair_attempted": match.group("repair") == "true",
                "retry_count": min(int(match.group("retry")), 1),
                "duration_ms": int(match.group("duration")),
                "provider_request_id_hash": match.group("request_id_hash")[:64],
            }
        )
    return diagnostics


def _read_redacted_request_metadata(path: Path) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    if not path.is_file():
        return metadata
    allowed_fields = {
        "kind",
        "provider_id",
        "provider_type",
        "endpoint",
        "model",
        "litellm_model",
        "message_count",
        "message_bytes",
        "request_body_bytes",
        "request_body_scope",
        "input_fingerprint_sha256",
        "schema_fingerprint_sha256",
        "response_mode",
        "explicit_max_tokens",
        "explicit_timeout_seconds",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("kind") != "provider_request_metadata":
            continue
        metadata.append({key: value for key, value in record.items() if key in allowed_fields})
    return metadata


def _validate_redacted_request_metadata(
    metadata: list[dict[str, Any]],
    *,
    expected_calls: int,
    expected_provider_type: str,
    expected_model: str,
) -> None:
    if expected_calls <= 0:
        raise RuntimeError("expected calls must be positive")
    if len(metadata) != expected_calls:
        raise RuntimeError(
            f"provider request metadata count {len(metadata)} does not match expected calls {expected_calls}"
        )
    for record in metadata:
        if record.get("kind") != "provider_request_metadata":
            raise RuntimeError("provider request metadata kind was invalid")
        if not isinstance(record.get("provider_id"), str) or not record["provider_id"]:
            raise RuntimeError("provider request metadata provider id was invalid")
        if record.get("provider_type") != expected_provider_type:
            raise RuntimeError("provider request metadata provider type was invalid")
        if record.get("model") != expected_model:
            raise RuntimeError("provider request metadata model was invalid")
        if record.get("litellm_model") != f"openai/{expected_model}":
            raise RuntimeError("provider request metadata LiteLLM model was invalid")
        endpoint = record.get("endpoint")
        if not isinstance(endpoint, dict):
            raise RuntimeError("provider request metadata endpoint was invalid")
        if endpoint.get("scheme") != "http" or endpoint.get("host") not in {
            "127.0.0.1",
            "localhost",
        }:
            raise RuntimeError("provider request metadata endpoint was not loopback")
        port = endpoint.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeError("provider request metadata endpoint port was invalid")
        if record.get("request_body_scope") != "serialized_provider_payload_without_auth_or_endpoint":
            raise RuntimeError("provider request metadata body scope was invalid")
        for field in ("explicit_max_tokens", "explicit_timeout_seconds"):
            if field not in record:
                raise RuntimeError(f"provider request metadata {field} was missing")
        for field in ("message_count", "message_bytes", "request_body_bytes"):
            value = record.get(field)
            if type(value) is not int or value < 0:
                raise RuntimeError(f"provider request metadata {field} was invalid")
        for field in ("input_fingerprint_sha256", "schema_fingerprint_sha256"):
            value = record.get(field)
            if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
                raise RuntimeError("provider request metadata fingerprint was invalid")
        if record.get("response_mode") != "text_json":
            raise RuntimeError("provider request metadata response mode was invalid")


def _validate_controlled_responses(
    responses: list[dict[str, object]], *, expected_calls: int
) -> None:
    if len(responses) != expected_calls or expected_calls <= 0:
        raise RuntimeError("Provider responses did not match expected calls")
    for response in responses:
        if response.get("response_status") != 200:
            raise RuntimeError("Provider responses did not all succeed")
        body_bytes = response.get("request_body_bytes")
        if type(body_bytes) is not int or body_bytes <= 0:
            raise RuntimeError("Provider response request body metadata was invalid")
        request_id_hash = response.get("request_id_hash")
        if not isinstance(request_id_hash, str) or not re.fullmatch(r"[0-9a-f]{12}", request_id_hash):
            raise RuntimeError("Provider response request id metadata was invalid")


def _count_resume_string_leaves(value: object, *, budget: list[int]) -> int:
    if budget[0] <= 0:
        return 0
    budget[0] -= 1
    if isinstance(value, str):
        return 1 if value.strip() else 0
    if isinstance(value, dict):
        return sum(_count_resume_string_leaves(item, budget=budget) for item in value.values())
    if isinstance(value, list):
        return sum(_count_resume_string_leaves(item, budget=budget) for item in value)
    return 0


def _redacted_evidence_catalog_counts(data_dir: Path) -> dict[str, int]:
    session_factory = session_factory_for_data_dir(data_dir)
    try:
        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(InterviewPreparationProposal).order_by(InterviewPreparationProposal.id)
                )
            )
    finally:
        engine = session_factory.kw.get("bind")
        if engine is not None:
            engine.dispose()

    counts = {
        "snapshots": 0,
        "jd_sources": 0,
        "resume_facts": 0,
        "knowledge_evidence": 0,
    }
    for row in rows:
        try:
            snapshot = json.loads(row.input_snapshot_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        counts["snapshots"] += 1
        jd = snapshot.get("jd")
        if isinstance(jd, dict) and isinstance(jd.get("text"), str) and jd["text"].strip():
            counts["jd_sources"] += 1
        resume = snapshot.get("resume")
        if isinstance(resume, dict):
            counts["resume_facts"] += _count_resume_string_leaves(
                resume.get("content_json"), budget=[10000]
            )
        evidence = snapshot.get("knowledge_evidence")
        if isinstance(evidence, list):
            counts["knowledge_evidence"] += sum(isinstance(item, dict) for item in evidence)
    return counts


def _controlled_config(source_data: Path, provider_url: str) -> Config:
    config = load_config(source_data).model_copy(deep=True)
    active = config.active_provider()
    controlled = active.model_copy(
        update={
            "provider": "openai_compatible",
            "api_key": "controlled-diagnostic-key",
            "base_url": provider_url,
            "model": "controlled-interview-preparation",
            "enabled": True,
            "supports_json_schema": False,
        }
    )
    config.providers = [controlled]
    config.active_provider_id = controlled.id
    config.fallback_provider_ids = []
    config.base_url = controlled.base_url
    config.api_key = controlled.api_key
    config.model = controlled.model
    return config


def run_diagnostic(source_data: Path, static_dir: Path | None) -> dict[str, Any]:
    _ControlledProviderHandler.calls = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), _ControlledProviderHandler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    started = time.perf_counter()
    previous_no_proxy = os.environ.get("NO_PROXY")
    previous_request_audit = os.environ.get("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    request_metadata: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="offerpilot-interview-preparation-controlled-") as temp_dir:
            data_dir = Path(temp_dir)
            request_audit_path = data_dir / "provider-request-audit.jsonl"
            os.environ["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = str(request_audit_path)
            provider_url = f"http://127.0.0.1:{provider.server_port}/v1"
            save_config(data_dir, _controlled_config(source_data, provider_url))
            app = create_app(data_dir=data_dir, static_dir=static_dir)
            with _running_server(app) as base_url:
                with httpx.Client(base_url=base_url, timeout=60.0) as client:
                    created = client.post(
                        "/api/applications",
                        json={
                            "company_name": "controlled diagnostic",
                            "position_name": "interview preparation",
                            "status": "applied",
                        },
                    )
                    created.raise_for_status()
                    application_id = int(created.json()["id"])
                    resume_ids: list[int] = []
                    _run_real_ai_interview_preparation_smoke(
                        client, [], application_id, resume_ids
                    )
                    diagnostics = _read_redacted_generation_diagnostics(data_dir)
                    _validate_controlled_responses(
                        _ControlledProviderHandler.calls,
                        expected_calls=_CONTROLLED_PROVIDER_CALLS,
                    )
                    request_metadata = _read_redacted_request_metadata(request_audit_path)
                    _validate_redacted_request_metadata(
                        request_metadata,
                        expected_calls=_CONTROLLED_PROVIDER_CALLS,
                        expected_provider_type=_CONTROLLED_PROVIDER_TYPE,
                        expected_model=_CONTROLLED_MODEL,
                    )
                    evidence_catalog_counts = _redacted_evidence_catalog_counts(data_dir)
                    client.delete(f"/api/applications/{application_id}")
                    for resume_id in resume_ids:
                        client.delete(f"/api/resumes/{resume_id}")
    finally:
        if previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = previous_no_proxy
        if previous_request_audit is None:
            os.environ.pop("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", None)
        else:
            os.environ["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"] = previous_request_audit
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    return {
        "status": "passed",
        "stage": "interview_preparation",
        "provider": "controlled-local-openai-compatible",
        "endpoint": "http://127.0.0.1:<ephemeral>/v1",
        "model": "controlled-interview-preparation",
        "provider_calls": len(_ControlledProviderHandler.calls),
        "responses": _ControlledProviderHandler.calls,
        "diagnostics": diagnostics,
        "request_metadata": request_metadata,
        "evidence_catalog_counts": evidence_catalog_counts,
        "elapsed_ms": max(0, int((time.perf_counter() - started) * 1000)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a redacted interview-preparation provider-boundary diagnostic.")
    parser.add_argument("--source-data", type=Path, default=resolve_data_dir())
    parser.add_argument("--static-dir", type=Path, default=Path("web/dist"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_diagnostic(args.source_data, args.static_dir)
    encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="ascii")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
