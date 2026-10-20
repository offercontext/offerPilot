from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import queue
import secrets
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
from typing import Any, cast
from uuid import uuid4

from offerpilot.evaluation.adapters import HTTPDriver, DriverError, ProductFailure
from offerpilot.evaluation.contracts import (
    ApprovalEvidence, CaseSpec, EvidenceBundle, ObservationCoverage, PlannedTrial,
    RunManifest, TrialResult, VariantConfig,
)
from offerpilot.evaluation.evidence import observer
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.graders.p0 import GRADERS
from offerpilot.evaluation.registry import digest, file_hash, load_suite, fault_detected
from offerpilot.evaluation.reporting import aggregate, atomic_json, write_report

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "scripts/evaluation_subject/bootstrap.py"


class UnsupportedSubject(DriverError):
    pass


def process_tree(argv: list[str], **kwargs: Any) -> Any:
    spec = importlib.util.spec_from_file_location("fixed_process_tree", BRIDGE.parent / "process_tree.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProcessTree(argv, **kwargs)


def git(checkout: Path, *args: str) -> bytes:
    # Snapshot reads must not invoke fsmonitor, diff/textconv, or clean filters.
    # Preserve normal user EOL configuration, while dropping ambient Git routing.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    prefix = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "diff.external="]
    try:
        try:
            keys = subprocess.check_output(
                prefix + ["config", "--null", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$"],
                cwd=checkout, env=env, stderr=subprocess.PIPE, timeout=30,
            ).split(b"\0")
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1:
                raise
            keys = []
        for raw in keys:
            if raw:
                key = os.fsdecode(raw)
                prefix += ["-c", key + ("=false" if key.endswith(".required") else "=")]
        if args and args[0] == "diff":
            args = (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
        return subprocess.check_output(
            prefix + list(args), cwd=checkout, env=env, stderr=subprocess.PIPE, timeout=30
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git snapshot read unavailable") from exc


def snapshot(checkout: Path) -> dict[str, Any]:
    tracked = git(checkout, "ls-files", "-z").split(b"\0")
    untracked = git(checkout, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    files = {}
    for raw in sorted(set(tracked + untracked) - {b""}):
        name = os.fsdecode(raw).replace("\\", "/")
        path = checkout / name
        files[name] = file_hash(path) if path.is_file() else "missing"
    return {"commit": git(checkout, "rev-parse", "HEAD").decode().strip(),
            "diff_hash": digest([git(checkout, "diff", "--binary", "HEAD").hex(), files]),
            "lock_hash": file_hash(checkout / "uv.lock"), "files_hash": digest(files)}


def evaluator_hash() -> str:
    paths = [*Path(__file__).parent.rglob("*.py"), *BRIDGE.parent.rglob("*.py"),
             *(ROOT / "evals").rglob("*.json")]
    return digest({str(p.relative_to(ROOT)): file_hash(p) for p in paths})


def environment() -> dict[str, Any]:
    return {"python": sys.version, "executable": sys.executable, "os": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}


def source_cache_prefix(parent: Path | None = None) -> Path:
    """A never-created cache namespace: ignore existing pyc while prohibiting new writes."""
    root = parent.absolute() if parent is not None else Path(tempfile.gettempdir()).resolve()
    path = root / ("source-only-" + uuid4().hex)
    if path.exists():
        raise ValueError("source-only cache namespace already exists")
    return path


def process_environment(checkout: Path, data: Path) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update({"PYTHONPATH": str(checkout / "src"), "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(source_cache_prefix(data)),
                "PYTHONUNBUFFERED": "1", "OFFERPILOT_DATA": str(data),
                "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_TELEMETRY": "False"})
    return env


def subject_command(interpreter: str, checkout: Path, bridge: Path,
                    environment: dict[str, str]) -> list[str]:
    """Install the current evaluator's source guard before importing either checkout."""
    config = json.dumps(dict(checkout=str(checkout), evaluator=str(ROOT), bridge=str(bridge)))
    return [interpreter, "-I", "-B", "-u", "-X", "utf8", "-X",
            "pycache_prefix=" + environment["PYTHONPYCACHEPREFIX"],
            str(ROOT / "scripts/evaluation_subject/source_launcher.py"), config]


def target_environment(variant: VariantConfig) -> dict[str, Any]:
    code = ("import sys,sqlite3,json,importlib.metadata; print(json.dumps({"
            "'python':sys.version,'sqlite':sqlite3.sqlite_version,'dependencies':{"
            "d.metadata['Name']:d.version for d in importlib.metadata.distributions()}}))")
    raw = subprocess.check_output([variant.interpreter, "-I", "-B", "-X",
        "pycache_prefix=" + str(source_cache_prefix()), "-c", code], cwd=variant.checkout,
        env=process_environment(Path(variant.checkout), Path(variant.checkout) / ".codex"),
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    return cast(dict[str, Any], json.loads(raw))


class Subject:
    def __init__(self, variant: VariantConfig, config: dict[str, Any], private: Path,
                 events: list[dict[str, Any]], deadline: float) -> None:
        self.config, self.private, self.events, self.deadline = config, private, events, deadline
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.errors: list[str] = []
        self.closed = False
        self.lock = threading.Lock()
        self.stderr = (private / f"epoch-{config['epoch']}.stderr").open("w", encoding="utf-8")
        environment = process_environment(Path(variant.checkout), private)
        self.tree = process_tree(subject_command(variant.interpreter, Path(variant.checkout), BRIDGE, environment),
            cwd=variant.checkout, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding="utf-8", bufsize=1)
        self.process = self.tree.process
        self.send(config)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def send(self, value: dict[str, Any]) -> None:
        with self.lock:
            assert self.process.stdin
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _read(self) -> None:
        seq = 0
        try:
            assert self.process.stdout
            with (self.private / f"epoch-{self.config['epoch']}.control.jsonl").open(
                "w", encoding="utf-8") as log:
                while True:
                    line = self.process.stdout.readline(1024 * 1024 + 1)
                    if not line:
                        break
                    if len(line.encode()) > 1024 * 1024 or not line.endswith("\n"):
                        raise ValueError("oversized or partial control message")
                    event = json.loads(line)
                    if (event.get("schema_version") != 1 or event.get("trial_key") != self.config["trial_key"]
                            or event.get("epoch") != self.config["epoch"] or event.get("seq") != seq + 1
                            or event.get("type") not in {"prepared", "ready", "call_attempt", "error",
                                "fault_reached", "negative_control_reached", "shutdown_complete", "model_observation", "executor_observation"}):
                        raise ValueError("invalid control message identity or sequence")
                    seq += 1
                    if (self.private / "data.db").exists() and event["type"] == "call_attempt":
                        event["observer_high_water"] = observer.read(self.private / "data.db")["high_water"]
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                    os.fsync(log.fileno())
                    self.events.append(event)
                    if event["type"] == "call_attempt":
                        calls = [e for e in self.events if e["type"] == "call_attempt"]
                        models = sum(c.get("purpose") in {"chat", "title"} for c in calls)
                        tools = sum(c.get("purpose") == "tool" for c in calls)
                        if models > self.config["limits"]["model_calls"] or tools > self.config["limits"]["tool_attempts"]:
                            raise TimeoutError("evaluator call watchdog")
                    if event["type"] not in {"shutdown_complete", "error"}:
                        self.send({"command": "ack", "seq": seq})
                    self.messages.put(event)
        except Exception as exc:
            self.errors.append(f"control:{type(exc).__name__}")
            self.messages.put({"type": "error", "message": str(exc), "error_type": type(exc).__name__})

    def wait(self, kind: str, timeout: float = 30) -> dict[str, Any]:
        limit = min(self.deadline, time.monotonic() + timeout)
        while time.monotonic() < limit:
            try:
                event = self.messages.get(timeout=min(0.1, max(0.001, limit - time.monotonic())))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise DriverError("subject exited before control event")
                continue
            if event["type"] == kind:
                return event
            if event["type"] == "error":
                if event.get("error_type") == "TimeoutError":
                    raise TimeoutError(event.get("message", "evaluator control watchdog"))
                if event.get("error_type") == "UnsupportedCapability":
                    raise UnsupportedSubject("target capability unsupported")
                raise DriverError("subject error: " + event.get("message", "unknown"))
        raise TimeoutError(f"subject did not produce {kind}")

    def stop(self, *, crash: bool = False) -> None:
        if self.closed:
            return
        graceful = False
        self.owned_tree_exit_proven = False
        shutdown_deadline = min(self.deadline, time.monotonic() + self.config["limits"]["shutdown_seconds"])
        try:
            if self.process.poll() is None:
                if crash:
                    self.tree.kill()
                else:
                    self.send({"command": "request_shutdown"})
                    self.wait("shutdown_complete", max(0, shutdown_deadline - time.monotonic()))
                    graceful = True
            self.tree.wait(timeout=max(0.01, shutdown_deadline - time.monotonic()))
            self.owned_tree_exit_proven = True
        except Exception:
            self.errors.append("shutdown_timeout")
            try:
                self.tree.kill()
            except Exception:
                self.errors.append("kill_failed")
            try:
                self.tree.wait(timeout=10)
                self.owned_tree_exit_proven = True
            except Exception:
                self.errors.append("exit_unverified")
        finally:
            self.reader.join(timeout=5)
            if self.reader.is_alive():
                self.errors.append("control_not_closed")
            if not crash and (not graceful or self.process.returncode != 0):
                self.errors.append("abnormal_shutdown")
            try:
                self.tree.close()
            except Exception:
                self.errors.append("tree_close_failed")
            self.closed = self.process.poll() is not None and not any(
                e in self.errors for e in ("exit_unverified", "tree_close_failed", "control_not_closed"))
            self.stderr.close()
            for stream in (self.process.stdin, self.process.stdout) if self.closed else ():
                if stream:
                    stream.close()


def operation_facts(db: Path, operation_id: str | None, conversation_id: int) -> dict[str, Any]:
    with observer.connect(db, readonly=True) as conn:
        conn.execute("BEGIN")
        conversation = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        facts: dict[str, Any] = {"conversation": dict(conversation) if conversation else None}
        if operation_id:
            op = conn.execute("SELECT * FROM write_operations WHERE id=?", (operation_id,)).fetchone()
            messages = conn.execute("SELECT * FROM chat_messages WHERE operation_id=? ORDER BY id",
                                    (operation_id,)).fetchall()
            facts.update(operation=dict(op) if op else None, messages=[dict(m) for m in messages])
        else:
            facts["query_operation_count"] = conn.execute(
                "SELECT count(*) FROM write_operations WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
        return facts


def run_trial(plan: PlannedTrial, spec: CaseSpec, variant: VariantConfig, private: Path,
              expected_snapshot: dict[str, Any]) -> tuple[TrialResult, EvidenceBundle]:
    started = time.monotonic()
    deadline = started + spec.evaluator_limits.wall_seconds
    token = secrets.token_urlsafe(32)
    private.mkdir(parents=True, exist_ok=False)
    trial_key = plan.model_dump(mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"})
    atomic_json(private / ".eval-trial.json", trial_key)
    events: list[dict[str, Any]] = []
    http: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    initial: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    milestones: dict[str, Any] = {}
    errors: dict[str, str] = {}
    approval: ApprovalEvidence | None = None
    coverage: ObservationCoverage | None = None
    subject: Subject | None = None
    driver: HTTPDriver | None = None
    status = "completed"
    conversation_id = 0
    op_id: str | None = None
    expected_source = {k: v for k, v in expected_snapshot.items() if k != "target_runtime"}

    def start(epoch: int) -> Subject:
        if snapshot(Path(variant.checkout)) != expected_source:
            raise ValueError("target snapshot changed")
        config = {"command": "prepare", "trial_key": trial_key, "epoch": epoch,
                  "checkout": variant.checkout, "data_dir": str(private), "token": token,
                  "fault": variant.fault, "limits": spec.evaluator_limits.model_dump()}
        return Subject(variant, config, private, events, deadline)

    def handshake(s: Subject) -> HTTPDriver:
        ready = s.wait("ready", spec.evaluator_limits.startup_seconds)
        path = Path(ready["product_file"]).resolve()
        if (ready["checkout_sha"] != expected_snapshot["commit"] or ready["bridge_version"] != "p0-v1"
                or not path.is_relative_to(Path(variant.checkout).resolve() / "src")
                or Path(ready["data_dir"]).resolve() != private.resolve()
                or Path(ready["database_file"]).resolve() != (private / "data.db").resolve()
                or Path(ready["interpreter"]).resolve() != Path(variant.interpreter).resolve()
                or not ready["address"].startswith("http://127.0.0.1:")
                or spec.adapter not in ready["capabilities"]):
            raise ValueError("subject handshake mismatch")
        if {k: ready[k] for k in ("python", "sqlite", "dependencies")} != (
            expected_snapshot.get("target_runtime") or target_environment(variant)
        ):
            raise ValueError("actual target runtime differs from frozen environment")
        facts.setdefault("handshakes", []).append(ready)
        return HTTPDriver(ready["address"], token, deadline, http)

    try:
        subject = start(1)
        prepared = subject.wait("prepared", max(0, deadline - time.monotonic()))
        initial, facts["aliases"] = prepared["initial"], prepared["aliases"]
        coverage = ObservationCoverage.model_validate(prepared["coverage"])
        driver = handshake(subject)
        message = "列出云岚数据测试开发在 2026 年 10 月的面试，只查询" if plan.case_id == "E01" else "将云岚数据测试开发 2026 年 10 月技术面试从10点改到15点"
        body = {"conversation_id": 0, "context_type": "application",
                "context_ref": str(facts["aliases"]["target_application"]), "message": message,
                "page_context": {"view": "calendar", "label": "面试日程",
                    "entity": {"kind": "application", "id": str(facts["aliases"]["target_application"]),
                               "label": "云岚数据／测试开发"}}}
        if spec.adapter == "runtime-v1":
            body["request_id"] = str(uuid4())
            submitted = driver.request("POST", "/api/pilot/runtime/v1/turns", body)
            conversation_id = submitted["conversation_id"]
            turn_id = submitted["turn_id"]
            waiting = driver.wait_turn(turn_id, {"completed"} if plan.case_id == "E01" else {"waiting_confirmation"})
            response = waiting.get("terminal", {}).get("response", {})
            facts["runtime_before"] = waiting
            facts["budget_snapshot"] = waiting.get("budget")
            if waiting.get("state") in {"failed", "cancelled", "interrupted"}:
                raise ProductFailure("non-success product terminal")
        else:
            turn_id = None
            submitted = driver.request("POST", "/api/chat", body)
            conversation_id = submitted["conversation_id"]
            response = submitted
        pending = response.get("pending_action")
        if plan.case_id == "E01":
            facts["query_answer"] = json.loads(response["message"])
            facts["terminal_pending"] = pending
            if waiting["state"] != "completed":
                raise DriverError("query did not complete")
        else:
            if not pending:
                raise ProductFailure("missing expected pending proposal")
            op_id = pending["operation_id"]
            raw_args = pending.get("args", pending.get("arguments"))
            original = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(original, dict):
                raise DriverError("pending arguments unsupported")
            from offerpilot.evaluation.graders.p0 import instant
            if (pending.get("tool_name") != "update_application_event"
                    or original.get("id") != facts["aliases"]["technical_interview"]
                    or instant(original.get("scheduled_at")) != instant("2026-10-14T15:00:00+08:00")):
                raise ProductFailure("wrong original proposal")
            milestones["before_confirmation"] = observer.read(private / "data.db")
            effective = {**original, "scheduled_at": "2026-10-14T16:00:00+08:00"}
            confirm = {"conversation_id": conversation_id, "operation_id": op_id,
                       "confirmation_token": pending["confirmation_token"], "approved": True,
                       "edited_args": {"scheduled_at": effective["scheduled_at"]}}
            if turn_id:
                confirm["request_id"] = str(uuid4())
            approval = ApprovalEvidence(pending=pending, original_proposal=original, approved=True,
                effective_args=effective, request_hash=digest(confirm), operation_id=op_id,
                conversation_id=conversation_id, turn_id=turn_id,
                generation=submitted.get("execution_generation"))
            atomic_json(private / "approval.json", approval.model_dump(mode="json"))
            atomic_json(private / "confirm-request.json", confirm)
            facts["confirmation_request"] = confirm
            decision_seq = observer.marker(private / "data.db", "approval_decided", {
                "request_hash": approval.request_hash, "operation_id": op_id,
                "approval_file_hash": file_hash(private / "approval.json")})
            approval = approval.model_copy(update={"decision_seq": decision_seq})
            path = f"/api/pilot/runtime/v1/turns/{turn_id}/confirm" if turn_id else "/api/chat/confirm"
            if turn_id:
                accepted = driver.request("POST", path, confirm)
                final_turn = driver.wait_turn(turn_id, {"completed"})
                facts["runtime_after"] = final_turn
                facts["terminal_pending"] = final_turn.get("terminal", {}).get("response", {}).get("pending_action")
                if (final_turn["state"] != "completed" or accepted["turn_id"] != turn_id
                        or accepted["execution_generation"] != submitted["execution_generation"] + 1):
                    raise ProductFailure("runtime confirmation generation/state mismatch")
                driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn_id}/snapshot")
            else:
                failures: list[Exception] = []
                http_finished = threading.Event()
                def confirm_inflight() -> None:
                    try:
                        driver.request("POST", path, confirm)  # type: ignore[union-attr]
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        http_finished.set()
                inflight = threading.Thread(target=confirm_inflight)
                inflight.start()
                # Observe HTTP completion as well as the barrier: a product
                # rejection is a failure, a missed injector is an evaluator gap.
                fault = None
                while time.monotonic() < deadline and fault is None:
                    if http_finished.is_set():
                        premise = operation_facts(private / "data.db", op_id, conversation_id)
                        milestones["persisted"] = premise
                        atomic_json(private / "fault-premise.json", premise)
                        if failures:
                            raise failures[0]
                        raise DriverError("confirm completed without persistence barrier")
                    try:
                        fault = subject.wait("fault_reached", 0.2)
                    except TimeoutError:
                        continue
                if fault is None:
                    raise TimeoutError("persistence barrier watchdog")
                faults.append(fault)
                premise = operation_facts(private / "data.db", op_id, conversation_id)
                milestones["persisted"] = premise
                atomic_json(private / "fault-premise.json", premise)
                if (not fault.get("persisted") or premise["operation"]["status"] != "committed"
                        or sorted(m["role"] for m in premise["messages"]) != ["assistant", "tool"]):
                    subject.stop(crash=True)
                    raise ProductFailure("saved receipt persistence contract violated")
                subject.stop(crash=True)
                inflight.join(timeout=5)
                if inflight.is_alive():
                    raise TimeoutError("inflight HTTP did not exit")
                driver.close()
                if subject.errors:
                    raise DriverError("pre-crash control gap")
                epoch = observer.next_epoch(private / "data.db")
                subject = start(epoch)
                coverage = coverage.model_copy(update={"epochs": [1, epoch]})
                driver = handshake(subject)
                facts["replays"] = [driver.request("POST", path, confirm), driver.request("POST", path, confirm)]
                facts["terminal_pending"] = next((r.get("pending_action") for r in facts["replays"]
                                                   if r.get("pending_action") is not None), None)
        facts["driver_completed"] = True
        observer.marker(private / "data.db", "driver_finished", {"terminal_received": True})
    except Exception as exc:
        status = "unsupported" if isinstance(exc, UnsupportedSubject) else "product_failed" if isinstance(exc, ProductFailure) else "interrupted" if isinstance(exc, TimeoutError) else "setup_error" if not initial else "eval_error"
        errors["driver"] = type(exc).__name__
        if isinstance(exc, ProductFailure):
            facts["product_failures"] = [str(exc)]
        if isinstance(exc, TimeoutError):
            errors["timeout_stage"] = str(exc)
        facts["termination_source"] = "evaluator_watchdog" if isinstance(exc, TimeoutError) else "unknown"
        facts["termination_events"] = [{"source": facts["termination_source"], "reason": type(exc).__name__}]
    finally:
        if subject:
            subject.stop()
            if subject.errors:
                errors["control"] = ",".join(subject.errors)
        if driver:
            driver.close()
    final: dict[str, Any] = {}
    deltas, markers = [], []
    if (private / "data.db").exists() and coverage:
        try:
            if subject and subject.closed:
                observer.marker(private / "data.db", "observation_closed", {"writers_exited": True})
            observer.backup(private / "data.db", private / "sealed.db")
            raw = observer.read(private / "sealed.db")
            final, deltas, markers = raw["snapshot"], raw["deltas"], raw["markers"]
            coverage = coverage.model_copy(update={"end_seq": raw["high_water"],
                "status": "gap" if errors or raw["gaps"] else "closed_after_crash" if faults else "closed",
                "gaps": raw["gaps"] + list(errors)})
            if conversation_id:
                facts.update(operation_facts(private / "sealed.db", op_id, conversation_id))
        except Exception as exc:
            errors["observer"] = type(exc).__name__
    facts["control_closed"] = bool(subject and subject.closed and not subject.errors)
    facts["fault_witnesses"] = [e for e in events + markers
        if (e.get("type") == "negative_control_reached" or e.get("name") == "negative_control_reached")
        and e.get("fault_id") == variant.fault and e.get("trial_key") == trial_key
        and e.get("epoch") in {1, 2}]
    if snapshot(Path(variant.checkout)) != expected_source:
        errors["snapshot"] = "target_snapshot_changed"
    calls = [e for e in events if e["type"] == "call_attempt"]
    for event in events:
        if event["type"] == "model_observation":
            facts["query_result"] = event["query_result"]
        if event["type"] == "executor_observation":
            facts["effective_args"] = event["effective_args"]
    facts["consumption"] = {"wall_seconds": time.monotonic() - started,
        "model_attempts": sum(c["purpose"] in {"chat", "title"} for c in calls),
        "executor_attempts": sum(c["purpose"] == "execute_primary" for c in calls),
        "tool_pipeline_attempts": sum(c["purpose"] == "tool" for c in calls),
        "real_calls": "not_applicable", "tokens": "not_applicable", "cost": "not_applicable"}
    facts["consumption"].update({"coverage_status": coverage.status if coverage else None,
                                "coverage_gaps": list(errors) + (coverage.gaps if coverage else ["missing_coverage"]),
                                "product_budget": facts.get("budget_snapshot")})
    if not facts["control_closed"]:
        for name in ("model_attempts", "executor_attempts", "tool_pipeline_attempts"):
            facts["consumption"][name + "_observed_lower_bound"] = facts["consumption"][name]
            facts["consumption"][name] = None
    files = {p.name: {"bytes": p.stat().st_size, "sha256": file_hash(p)}
             for p in private.iterdir() if p.is_file() and p.name != "evidence.json"}
    evidence = EvidenceBundle(initial=initial, milestones=milestones, final=final, deltas=deltas,
        markers=markers, approval=approval, facts=facts, http=http, calls=calls, faults=faults,
        coverage=coverage, files=files, fragment_errors=errors)
    from offerpilot.evaluation.evidence.storage import seal, load
    evidence = seal(private, evidence)
    evidence = load(private)
    return grade_trial(plan, evidence, GRADERS, execution_status=status,
                       faulted=bool(evidence.facts.get("fault_witnesses"))), evidence


def run(suite_path: Path, variants: list[VariantConfig], output: Path,
        mode: str = "deterministic") -> dict[str, Any]:
    if mode != "deterministic":
        raise ValueError("P0 real mode is unsupported")
    suite, specs = load_suite(suite_path, ROOT / "evals")
    if not variants or len({v.variant_id for v in variants}) != len(variants):
        raise ValueError("missing or duplicate variants")
    if len(variants) == 1 and variants[0].variant_id != "candidate":
        raise ValueError("single-variant runs must use candidate")
    if len({Path(v.checkout).resolve() for v in variants}) != len(variants):
        raise ValueError("A/B variants must use distinct checkouts")
    for v in variants:
        checkout = Path(v.checkout).resolve(strict=True)
        interpreter = Path(v.interpreter).absolute()
        if not interpreter.is_file() or not interpreter.parent.resolve().is_relative_to(checkout / ".venv"):
            raise ValueError("interpreter must belong to target checkout .venv")
        if v.variant_id == "baseline" and v.fault != "none":
            raise ValueError("negative controls only apply to candidate")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid4()
    atomic_json(output / ".eval-run.json", {"run_id": str(run_id)})
    snapshots = {v.variant_id: snapshot(Path(v.checkout)) for v in variants}
    for v in variants:
        snapshots[v.variant_id]["target_runtime"] = target_environment(v)
    trials = []
    for item in suite.execution_items:
        for variant in sorted(variants, key=lambda v: v.variant_id):
            trials.append(PlannedTrial(run_id=run_id, variant_id=variant.variant_id,
                execution_item_id=item.execution_item_id, mode="deterministic", repeat_index=1,
                case_id=item.case_id, case_version=item.case_version,
                spec_hash=file_hash(ROOT / "evals" / item.spec_path),
                fixture_hash=file_hash(ROOT / "evals/fixtures/synthetic-interviews-v1.json"),
                coverage_kind=item.coverage_kind, required_rules=specs[item.execution_item_id].required_rules))
    manifest = RunManifest(run_id=run_id, suite_id=suite.suite_id, suite_hash=file_hash(suite_path),
        plan_kind="diagnostic" if any(v.fault != "none" for v in variants) else "p0",
        config_hash=digest([v.model_dump() for v in variants]), evaluator_hash=evaluator_hash(),
        variants={v.variant_id: {**v.model_dump(), **snapshots[v.variant_id]} for v in variants},
        environment=environment(), planned_trials=trials)
    atomic_json(output / "run-manifest.json", manifest.model_dump(mode="json"))
    results = []
    frozen_evaluator = manifest.evaluator_hash
    for trial in trials:
        variant = next(v for v in variants if v.variant_id == trial.variant_id)
        identifier = f"{trial.variant_id}-{trial.execution_item_id}-{trial.repeat_index}"
        result, _ = run_trial(trial, specs[trial.execution_item_id], variant,
                              output / "private" / identifier, snapshots[variant.variant_id])
        result = result.model_copy(update={"evidence_refs": [f"private/{identifier}/{p}" for p in result.evidence_refs]})
        if evaluator_hash() != frozen_evaluator:
            raise ValueError("evaluator changed during run; retained plan is incomplete")
        results.append(result)
        atomic_json(output / "trials" / identifier / "result.json", result.model_dump(mode="json"))
    summary = aggregate(manifest, results)
    if manifest.plan_kind == "diagnostic":
        candidate_fault = next(v.fault for v in variants if v.variant_id == "candidate")
        summary["negative_control_detected"] = any(fault_detected(candidate_fault, r.model_dump()) for r in results
                                                  if r.variant_id == "candidate")
        if not summary["negative_control_detected"] and summary["exit_code"] == 0:
            summary["exit_code"] = 2
    atomic_json(output / "summary.json", summary)
    if len(variants) == 2:
        from offerpilot.evaluation.compare import compare
        atomic_json(output / "comparison.json", compare(manifest, results, manifest, results))
    write_report(output / "report.md", summary, manifest)
    return summary
