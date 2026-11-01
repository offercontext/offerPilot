from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any, Iterator

import pytest

from scripts.companion_ai_validation import driver as base
from scripts.companion_ai_validation import experience_driver as experience


class LoopbackFake:
    """Test-only fake that cannot receive an external sender or endpoint."""
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.active: str | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                assert self.path == "/v1/chat/completions"
                assert owner.active and owner.active not in owner.operations
                assert self.headers.get("Authorization") == "Bearer fake-local-experience-only"
                length = int(self.headers["Content-Length"])
                assert 0 < length < 262144
                payload = json.loads(self.rfile.read(length))
                owner.operations.append(owner.active)
                owner.requests.append(payload)
                chunks = [{"role": "assistant", "content": "合成普通回复，仅验证机制。"}, {}]
                body = b"".join(b"data: " + json.dumps({
                    "id": "offline", "model": "offline-model", "created": 0,
                    "object": "chat.completion.chunk", "choices": [{"index": 0,
                        "delta": chunk, "finish_reason": "stop" if not chunk else None}],
                }).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def factory(self, data_dir: Path) -> base.Stage:
        from offerpilot.config import AIProviderProfile, Config, save_config

        @contextmanager
        def stage(operation: str) -> Iterator[None]:
            assert self.active is None
            assert operation == experience.OPERATIONS[len(self.operations)]
            self.active = operation
            save_config(data_dir, Config(providers=[AIProviderProfile(
                id="offline-fixture", label="Synthetic", provider="openai_compatible",
                model="offline-model", api_key="fake-local-experience-only",
                base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
                max_output_tokens=512, supports_json_schema=False,
            )], active_provider_id="offline-fixture", fallback_provider_id="",
                fallback_provider_ids=[], chat_auto_approve_writes=False))
            before = len(self.requests)
            try:
                yield
                assert len(self.requests) == before + 1
            finally:
                self.active = None
        return stage

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_twelve_real_runtime_turns_and_controls_have_exact_fake_budget(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    fake = LoopbackFake()
    recorded: list[dict[str, Any]] = []
    try:
        report = experience.run_matrix(root, fake.factory, record_response=recorded.append)
    finally:
        fake.close()
    assert report["passed"] and report["pass_scope"] == "mechanism_only"
    assert report["web_executed"] is False
    assert fake.operations == list(experience.OPERATIONS)
    assert len(recorded) == report["ordinary_count"] == len(fake.requests) == 12
    assert len(report["groups"]) == 5
    controls = [row for group in report["groups"] for row in group["controls"]]
    assert len(controls) == 7
    assert all(row["runtime_budget"] == {"model_calls": 0, "title_model_calls": 0,
                                          "tool_calls": 0} for row in controls)
    assert all(row["provenance"]["runtime_budget"] == {
        "model_calls": 1, "title_model_calls": 0, "tool_calls": 0} for row in recorded)
    assert all(row["ordinary_turn_read_only"] and row["source_verified"] for row in recorded)
    assert all(row["max_tokens"] == 512 and row["tools"] for row in fake.requests)
    assert all("fake-local-experience-only" not in json.dumps(row) for row in fake.requests)
    assert recorded[3]["provenance"]["persona_binding"]["revision"] == 1
    assert recorded[5]["provenance"]["persona_binding"]["revision"] == 0
    assert recorded[6]["provenance"]["persona_binding"]["revision"] == 1
    assert recorded[7]["source_facts_before_projection"] is None
    assert recorded[7]["expected_context"] is None
    assert recorded[7]["provenance"]["fixture_reference_not_projected"]["recent_shared_choice"]["value"] == "绿色"
    assert recorded[10]["source_facts_before_projection"]["recall_status"] == "source_unavailable"
    assert report["groups"][4]["assertions"]["withdrawal_abandoned"]
    serialized = json.dumps(recorded)
    assert "snapshot_cursor" not in serialized and "event_cursor" not in serialized
    assert len(serialized.encode()) <= 131072


def test_nonempty_root_is_rejected_without_starting_product(tmp_path: Path) -> None:
    (tmp_path / "unowned").write_text("keep")
    with pytest.raises(base.MatrixFailure, match="fresh_root_required"):
        experience.run_matrix(tmp_path, lambda _: None)  # type: ignore[arg-type, return-value]
    assert (tmp_path / "unowned").read_text() == "keep"


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    link = tmp_path / "link"
    link.symlink_to(owned, target_is_directory=True)
    with pytest.raises(base.MatrixFailure, match="fresh_root_required"):
        experience.run_matrix(link, lambda _: None)  # type: ignore[arg-type, return-value]


def test_case_contract_rejects_reordered_or_extra_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = json.loads(experience.CASE_PATH.read_text())
    path = tmp_path / "cases.json"
    original["cases"][0]["id"] = "x02"
    path.write_text(json.dumps(original))
    monkeypatch.setattr(experience, "CASE_PATH", path)
    with pytest.raises(base.MatrixFailure, match="cases: row"):
        experience.cases()


def test_group_failure_does_not_start_remaining_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered: list[str] = []

    @contextmanager
    def fake_api(data_dir: Path) -> Iterator[None]:
        entered.append(data_dir.name)
        yield

    class Engine:
        def dispose(self) -> None:
            pass

    class FakeMatrix:
        def __init__(self, *_args: Any) -> None:
            from types import SimpleNamespace
            self.sessions = SimpleNamespace(kw={"bind": Engine()})

        def run_group(self, group: str) -> dict[str, Any]:
            if group == "b":
                raise base.MatrixFailure("x04", "fixed_failure")
            return {"group": group, "responses": [], "controls": [], "assertions": {}}

    monkeypatch.setattr(base, "_api", fake_api)
    monkeypatch.setattr(experience, "_Experience", FakeMatrix)
    with pytest.raises(base.MatrixFailure, match="x04: fixed_failure"):
        experience.run_matrix(tmp_path, lambda _: None)  # type: ignore[arg-type, return-value]
    assert entered == ["a", "b"]
    assert not (tmp_path / "c").exists()
