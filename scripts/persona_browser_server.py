"""Loopback-only synthetic persona browser QA server; never a provider harness.

Run from the checkout with --data-dir and --evidence-dir pointing to separate,
temporary directories (data must be empty). The served frontend is the production web build.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
# Must precede every product import, including LiteLLM's import-time cost map.
from tests import _offline_network_guard as network_guard  # noqa: E402


class Evidence:
    def __init__(self, directory: Path):
        self.path = directory / "fake-model-counts.json"
        self.lock = threading.Lock()
        self.counts = {"chat_calls": 0, "title_calls": 0}
        self._write()

    def _write(self) -> None:
        payload = {"synthetic": True, "provider_calls": 0, **self.counts,
                   "blocked_network_attempts": len(network_guard._denied)}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def increment(self, kind: str) -> None:
        with self.lock:
            self.counts[f"{kind}_calls"] += 1
            self._write()


class FakeModel:
    """Deterministic response reads only the admitted persona data envelope."""
    def __init__(self, evidence: Evidence, kind: str = "chat"):
        self.evidence = evidence
        self.kind = kind

    def complete(self, messages: list[Any], tools: list[Any],
                 response_format: dict[str, Any] | None = None) -> Any:
        from offerpilot.ai.types import Assistant

        self.evidence.increment(self.kind)
        if self.kind == "title":
            return Assistant(content="Synthetic persona QA")
        name = "Haru"
        for message in messages:
            # Projector can strip transient metadata, so also require the exact
            # independent envelope shape, not an incidental string in a chat.
            try:
                data = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            if (isinstance(data, dict) and data.get("source") == "persona_profile"
                    and data.get("fictional_background_is_fiction") is True
                    and isinstance(data.get("expression_data"), dict)):
                candidate = data["expression_data"].get("display_name")
                if isinstance(candidate, str):
                    name = candidate
                    break
        return Assistant(content=f"[Synthetic persona QA / 合成测试] {name} 已收到；无真实模型调用。")


def _fresh_directory(path: Path) -> Path:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("QA directories must not use symlinks")
    resolved = path.resolve()
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise ValueError("QA directory must be new or empty; refusing existing data")
    return resolved


def build_app(data_dir: Path, evidence_dir: Path, static_dir: Path) -> Any:
    data = _fresh_directory(data_dir)
    if any(parent.is_symlink() for parent in (evidence_dir, *evidence_dir.parents)):
        raise ValueError("QA evidence directory must not use symlinks")
    evidence = evidence_dir.resolve()
    if evidence.exists() and not evidence.is_dir():
        raise ValueError("QA evidence path must be a directory")
    # CI may already be writing server.log here; never read those files or
    # overwrite a previous count artifact. Only our fixed files are created.
    if any((evidence / name).exists() for name in
           ("fake-model-counts.json", "fake-model-counts.tmp")):
        raise ValueError("QA evidence count file already exists")
    if data == evidence or data in evidence.parents or evidence in data.parents:
        raise ValueError("Data and evidence directories must be separate, non-nested directories")
    static = static_dir.resolve()
    if not (static / "index.html").is_file():
        raise ValueError("Production frontend missing; run npm run build in web first")
    data.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    os.environ["OFFERPILOT_DATA"] = str(data)
    # Fresh explicit app data prevents loading the owner's default config/key.
    from offerpilot.api import create_app
    from offerpilot.config import Config, save_config
    from offerpilot.companion.contracts import CompanionPolicy, CompanionSettingsCommand

    # Dismiss the setup checklist without forging completed user/business work.
    # This synthetic config has no provider key, auth token or owner settings.
    save_config(data, Config(onboarding_dismissed=True))
    counts = Evidence(evidence)
    app = create_app(data_dir=data, static_dir=static,
                     chat_model=FakeModel(counts), title_model=FakeModel(counts, "title"))
    life = app.state.companion_repository
    current = life.read()
    life.update_settings(CompanionSettingsCommand(
        identity=current["identity"], expected_revision=current["revision"], command_id=uuid4(),
        settings=CompanionPolicy(enabled=False, proactivity="off"),
    ))
    app.state.persona_qa_evidence = counts

    @app.get("/__persona_browser__/health")
    def qa_health() -> dict[str, bool]:
        return {"synthetic": True, "fake_model": True,
                "isolated_data": True, "outbound_blocked": True}

    # create_app already mounted the production SPA catch-all. The sentinel
    # must win route ordering so a browser cannot mistake an HTML page for QA.
    app.router.routes.insert(0, app.router.routes.pop())
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--port", type=int, default=18763)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        app = build_app(args.data_dir, args.evidence_dir, args.static_dir)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
