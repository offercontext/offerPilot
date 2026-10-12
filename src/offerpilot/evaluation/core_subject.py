from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any

from offerpilot.evaluation.core_contracts import RunGrant, RunManifestV2
from offerpilot.evaluation.runner import ROOT, Subject, process_environment, process_tree
from scripts.evaluation_subject import core_observer as observer


class RunLedger:
    """Reserve before send; unknown/stream usage keeps the entire reservation."""

    def __init__(self, manifest: RunManifestV2, grant: RunGrant | None, directory: Path) -> None:
        self.manifest, self.grant, self.directory = manifest, grant, directory
        self.lock = threading.Lock()
        self.requests = 0
        self.reserved_cny = Decimal(0)
        self.rows: list[dict[str, Any]] = []

    def reserve(self, event: dict[str, Any]) -> None:
        if event.get("purpose") != "provider_send" or self.manifest.mode != "real":
            return
        if event.get("blocked"):
            return
        profile = self.manifest.provider_profile
        assert profile and self.grant
        amount = (
            Decimal(event["input_bytes"]) * Decimal(str(profile.input_cny_per_million))
            + Decimal(event["output_cap"]) * Decimal(str(profile.output_cny_per_million))
        ) / Decimal(1000000)
        with self.lock:
            blocked = (
                self.requests >= min(self.grant.max_requests, self.manifest.run_max_requests)
                or self.reserved_cny + amount > Decimal(str(self.grant.max_cny))
                or (
                    hasattr(self.grant, "expires_at")
                    and self.grant.expires_at <= datetime.now(timezone.utc)
                )
            )
            self.rows.append(
                {
                    "trial_key": event["trial_key"],
                    "seq": event["seq"],
                    "reserve_cny": str(amount),
                    "allowed": not blocked,
                }
            )
            if not blocked:
                self.requests += 1
                self.reserved_cny += amount
            with (self.directory / "run-budget.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(self.rows[-1]) + "\n")
                f.flush()
                os.fsync(f.fileno())
            if blocked:
                raise TimeoutError("authorized run request/fee reservation exhausted")


class CoreSubject(Subject):
    def __init__(
        self,
        variant: Any,
        config: dict[str, Any],
        private: Path,
        events: list[dict[str, Any]],
        deadline: float,
        ledger: RunLedger,
    ) -> None:
        self.config, self.private, self.events, self.deadline, self.ledger = (
            config,
            private,
            events,
            deadline,
            ledger,
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.errors: list[str] = []
        self.closed = False
        self.lock = threading.Lock()
        self.stderr = (private / f"epoch-{config['epoch']}.stderr").open("w", encoding="utf-8")
        self.tree = process_tree(
            [variant.interpreter, "-u", str(ROOT / "scripts/evaluation_subject/core_bootstrap.py")],
            cwd=variant.checkout,
            env=process_environment(Path(variant.checkout), private),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.process = self.tree.process
        self.send(config)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        seq = 0
        allowed = {
            "prepared",
            "ready",
            "call_attempt",
            "error",
            "fault_reached",
            "negative_control_reached",
            "shutdown_complete",
            "model_observation",
            "executor_observation",
        }
        try:
            assert self.process.stdout
            with (self.private / f"epoch-{self.config['epoch']}.control.jsonl").open(
                "w", encoding="utf-8"
            ) as log:
                for line in iter(lambda: self.process.stdout.readline(1024 * 1024 + 1), ""):
                    if not line.endswith("\n") or len(line.encode()) > 1024 * 1024:
                        raise ValueError("partial/oversized control message")
                    event = json.loads(line)
                    if (
                        event.get("schema_version") != 1
                        or event.get("trial_key") != self.config["trial_key"]
                        or event.get("epoch") != self.config["epoch"]
                        or event.get("seq") != seq + 1
                        or event.get("type") not in allowed
                    ):
                        raise ValueError("invalid control identity")
                    seq += 1
                    if event["type"] == "call_attempt":
                        db = self.private / "data.db"
                        active = False
                        if db.exists():
                            with observer.connect(db, True) as conn:
                                active = (
                                    conn.execute(
                                        "SELECT 1 FROM sqlite_master WHERE name='_eval_core_meta'"
                                    ).fetchone()
                                    is not None
                                )
                        event["observer_high_water"] = (
                            observer.read(db)["high_water"] if active else 0
                        )
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                    os.fsync(log.fileno())
                    self.events.append(event)
                    if event["type"] == "call_attempt":
                        calls = [c for c in self.events if c["type"] == "call_attempt"]
                        if (
                            sum(
                                c.get("purpose") in {"chat", "title", "provider_send"}
                                for c in calls
                            )
                            > self.config["limits"]["model_calls"]
                            or sum(c.get("purpose") == "tool" for c in calls)
                            > self.config["limits"]["tool_attempts"]
                        ):
                            raise TimeoutError("trial call protection exhausted")
                        if (
                            self.config.get("provider_profile")
                            and sum(c.get("purpose") == "provider_send" for c in calls)
                            > self.config["provider_profile"]["max_attempts_per_trial"]
                        ):
                            raise TimeoutError("provider trial attempt cap exhausted")
                        self.ledger.reserve(event)
                    held_stream_barrier = (
                        event["type"] == "fault_reached"
                        and self.config["trial_key"]["execution_item_id"] == "E07.runtime_reattach"
                        and event.get("boundary") in {"model_entered", "http_before_send"}
                    )
                    held_budget_barrier = (
                        event["type"] == "fault_reached"
                        and self.config["trial_key"]["execution_item_id"]
                        in {"E08.runtime_unknown", "E08.runtime_deadline"}
                        and event.get("boundary") in {"accepted_primary_tool", "model_entered"}
                    )
                    if (
                        event["type"] not in {"error", "shutdown_complete"}
                        and not held_stream_barrier
                        and not held_budget_barrier
                    ):
                        self.send({"command": "ack", "seq": seq})
                    self.messages.put(event)
        except Exception as exc:
            self.errors.append("control:" + type(exc).__name__)
            self.messages.put(
                {
                    "type": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc) if isinstance(exc, TimeoutError) else type(exc).__name__,
                }
            )
