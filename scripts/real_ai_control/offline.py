"""Parent-owned no-fee qualification of the actual CLI and SDK boundary.

There is no paid activation path; every account and credential here is synthetic.
"""

from __future__ import annotations

from dataclasses import asdict
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from typing import Any

from offerpilot.evaluation.runner import process_tree
from .account import AccountSession, once, read, check_account, check_transactions
from .archive import EvidenceArchive
from .channel import Binding, ParentBroker
from .journal import ReservationJournal
from .private import work_path, remove_owned_config_copies
from .protocol import PROTOCOL, canonical, sha, write_packet
from .source import implementation, product_source
from .wire import WirePolicy


def run_probe(
    output: Path,
    *,
    watchdog_seconds: float = 900,
    fault: str = "first_model_failure",
    fake_key: str | None = None,
) -> dict[str, Any]:
    if not 0 < watchdog_seconds <= 900:
        raise ValueError("Explicit bounded outer watchdog required")
    output = output.absolute()
    output.mkdir(exist_ok=False)
    source = Path(__file__).resolve().parents[2]
    impl = implementation(source)
    product = product_source(source)
    once(output / "implementation.json", impl)
    once(output / "product-source.json", product)
    account = output / "fake-task-budget.json"
    once(
        account,
        dict(
            authorized_cny="57.605790",
            retained_cny="0",
            allocated_cny="0",
            unallocated_cny="57.605790",
            refund_cny="0",
            closed_runs=[],
            authorization_history=["offline fake"],
        ),
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    plan = dict(
        schema_version=3,
        mode="offline-mock",
        fault=fault,
        requests=64,
        cap_cny="12.39",
        watchdog_seconds=watchdog_seconds,
        ack_timeout_seconds=5,
        planned_stage_count=7,
        sdk_retries=0,
        physical_retries_per_logical_call=0,
        provider_timeout_seconds=45,
        startup_seconds=15,
        server_shutdown_seconds=30,
        original_api_waits_preserved=True,
        static_dir_omitted_for_offline_bootstrap=True,
    )
    once(output / "plan.json", plan)
    binding = Binding(
        run_id="offline-" + secrets.token_hex(8),
        plan_sha256=sha(canonical(plan)),
        source_sha256=sha(canonical(product)),
        account_sha256=sha(account.read_bytes()),
        quote_sha256=sha(b"offline fixture prices, not a market quote"),
        implementation_sha256=sha(canonical(impl)),
    )
    private_work = work_path(binding.run_id, output)
    result: dict[str, Any] = dict(
        binding=asdict(binding),
        private_work=str(private_work),
        owned_tree_exited=None,
        forced_kill=False,
        control_complete=False,
        real_provider_requests=0,
        real_account_accessed=False,
        execution_ready=False,
        release_eligible=False,
        allocation_closed=False,
        native_exit=None,
        raw_cli_exit=None,
    )
    import re

    key = fake_key or "sk-offline-" + secrets.token_hex(16)
    if not re.fullmatch(r"sk-offline-[a-zA-Z0-9-]{8,64}", key):
        raise ValueError("Offline-only synthetic key required")
    policy = WirePolicy(64000, 8192, "2", "8", expires)
    failed = threading.Event()
    started = time.monotonic()
    with AccountSession(
        account,
        output / "fake-account-journal",
        expected_sha256=binding.account_sha256,
        authorized_cny="57.605790",
        cap_cny="12.39",
        prior_journals=(),
    ) as session:
        with ReservationJournal(
            output / "physical-reservations.jsonl",
            plan_sha256=binding.plan_sha256,
            max_requests=64,
            max_cny="12.39",
            expires_at=expires,
        ) as journal:
            with (
                ExitStack() as resources,
                EvidenceArchive(output / "native-stderr-archive", secrets=(key,)) as diagnostics,
            ):
                owned_threads: list[threading.Thread] = []

                def native_failure(details: dict[str, Any]) -> None:
                    result["native_archive_failure"] = details
                    failed.set()

                diagnostics.failure_sink = native_failure
                native_body = diagnostics.body(
                    "native-stderr", links={"body_layer": "native stderr bytes"}
                )
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if k.upper()
                    in {
                        "SYSTEMROOT",
                        "WINDIR",
                        "PATH",
                        "PATHEXT",
                        "COMSPEC",
                        "USERPROFILE",
                        "APPDATA",
                        "LOCALAPPDATA",
                        "TEMP",
                        "TMP",
                        "PROGRAMDATA",
                        "PROGRAMFILES",
                        "PROGRAMFILES(X86)",
                        "HOMEDRIVE",
                        "HOMEPATH",
                    }
                }
                env.update(
                    PYTHONPATH=str(source / "src") + os.pathsep + str(source),
                    PYTHONIOENCODING="utf8",
                    LITELLM_LOCAL_MODEL_COST_MAP="True",
                    LITELLM_TELEMETRY="False",
                )
                tree = process_tree(
                    [sys.executable, "-B", "-m", "scripts.real_ai_control.child", "--offline"],
                    cwd=source,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                def emergency_cleanup() -> None:
                    evidence: dict[str, Any] = dict(owned_tree_exited=None, forced_kill=False)
                    try:
                        tree.wait(timeout=0.2)
                        evidence["owned_tree_exited"] = True
                    except BaseException:
                        try:
                            tree.kill()
                            evidence["forced_kill"] = True
                            evidence["native_exit"] = tree.wait(timeout=5)
                            evidence["owned_tree_exited"] = True
                        except BaseException as error:
                            evidence["error_type"] = type(error).__name__
                    finally:
                        if tree.process.stdin is not None:
                            tree.process.stdin.close()
                        tree.close(timeout=5)
                        for owned_thread in owned_threads:
                            owned_thread.join(timeout=5)
                        evidence["control_threads_exited"] = all(
                            not t.is_alive() for t in owned_threads
                        )
                        evidence["private_copy_cleanup"] = remove_owned_config_copies(
                            private_work, owned_tree_exited=evidence["owned_tree_exited"]
                        )
                        once(output / "emergency-owned-exit.json", evidence)

                resources.callback(emergency_cleanup)
                assert (
                    tree.process.stdin is not None
                    and tree.process.stdout is not None
                    and tree.process.stderr is not None
                )
                once(
                    output / "owned-start.json",
                    dict(
                        parent_pid=os.getpid(),
                        child_pid=tree.process.pid,
                        binding_sha256=binding.digest,
                        watchdog_seconds=watchdog_seconds,
                    ),
                )

                def stderr_capture() -> None:
                    try:
                        while True:
                            raw = tree.process.stderr.read1(65536)
                            if not raw:
                                break
                            native_body.feed(raw)
                        native_body.end(complete=True)
                    except BaseException as error:
                        result["native_stderr_error_type"] = type(error).__name__
                        failed.set()
                        if not native_body.finished:
                            try:
                                native_body.end(complete=False, error_type=type(error).__name__)
                            except BaseException:
                                pass

                stderr_thread = threading.Thread(target=stderr_capture, daemon=True)
                owned_threads.append(stderr_thread)
                stderr_thread.start()
                write_packet(
                    tree.process.stdin,
                    dict(
                        protocol=PROTOCOL,
                        kind="bootstrap",
                        seq=1,
                        data=dict(
                            mode="offline-mock",
                            fault=fault,
                            fake_key=key,
                            binding=asdict(binding),
                            price_valid_until=expires.isoformat(),
                            archive=str(output / "archive"),
                            work=str(private_work),
                            report=str(output / "child-report.json"),
                        ),
                    ),
                )
                checks = []

                def before_allow() -> None:
                    check_started = time.monotonic()
                    if sha(account.read_bytes()) != session.expected:
                        raise ValueError("Account changed before physical-send allowance")
                    check_account(read(account), authorized_cny="57.605790")
                    check_transactions((session.directory,))
                    if implementation(source) != impl or product_source(source) != product:
                        raise ValueError(
                            "Frozen product/dependencies/controller changed before allowance"
                        )
                    checks.append(time.monotonic() - check_started)

                broker = ParentBroker(
                    tree.process.stdout,
                    tree.process.stdin,
                    binding=binding,
                    policy=policy,
                    key=key,
                    journal=journal,
                    before_allow=before_allow,
                )

                def serve() -> None:
                    try:
                        broker.serve()
                    except BaseException as error:
                        result["control_error_type"] = type(error).__name__
                        if broker.fault is not None:
                            result["archive_control_fault"] = broker.fault
                            once(
                                output / "control-fault.json",
                                dict(binding_sha256=binding.digest, details=broker.fault),
                            )
                        failed.set()
                    finally:
                        tree.process.stdin.close()

                thread = threading.Thread(target=serve, daemon=True)
                owned_threads.append(thread)
                thread.start()
                try:
                    while True:
                        if failed.is_set():
                            raise RuntimeError("Private child control or archive failed")
                        remaining = watchdog_seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            raise TimeoutError("Whole-tree control watchdog exhausted")
                        try:
                            result["native_exit"] = tree.wait(timeout=min(0.2, remaining))
                            result["owned_tree_exited"] = True
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException as error:
                    result["error_type"] = type(error).__name__
                    result["raw_cli_exit"] = tree.process.poll()
                    try:
                        tree.kill()
                        result["forced_kill"] = True
                        result["native_exit"] = tree.wait(timeout=5)
                        result["owned_tree_exited"] = True
                    except BaseException as cleanup:
                        result["cleanup_error_type"] = type(cleanup).__name__
                finally:
                    tree.process.stdin.close()
                    thread.join(timeout=5)
                    stderr_thread.join(timeout=5)
                    result["control_reader_exited"] = not thread.is_alive()
                    result["native_stderr_reader_exited"] = not stderr_thread.is_alive()
                    tree.close(timeout=5)
                result["control_complete"] = (
                    broker.complete and not failed.is_set() and not thread.is_alive()
                )
                result["native_diagnostics"] = diagnostics.summary()
                result["admission_check_seconds"] = checks
                if result["raw_cli_exit"] is None and not result["forced_kill"]:
                    result["raw_cli_exit"] = result["native_exit"]
                result["journal"] = journal.verify()
                result["private_copy_cleanup"] = remove_owned_config_copies(
                    private_work, owned_tree_exited=result["owned_tree_exited"]
                )
                child_path = output / "child-report.json"
                if child_path.exists():
                    child = read(child_path)
                    result["child_report"] = child
                    terminal_matches = broker.done == dict(
                        evidence_sha256=sha(canonical(child)), native_exit=result["native_exit"]
                    )
                    proof = dict(
                        schema_version=3,
                        plan_sha256=binding.plan_sha256,
                        binding=asdict(binding),
                        owned_tree_exited=result["owned_tree_exited"],
                        forced_kill=result["forced_kill"],
                        control_complete=result["control_complete"] and terminal_matches,
                        ledger_complete=True,
                        evidence_complete=child["archive"]["complete"]
                        and diagnostics.summary()["complete"],
                        held_ledger=journal.verify(),
                        raw_cli_exit=result["raw_cli_exit"],
                        native_exit=result["native_exit"],
                        terminal_ack=broker.done,
                        control_eof=broker.complete,
                        control_reader_exited=not thread.is_alive(),
                        child_report=dict(
                            path=str(child_path), sha256=sha(child_path.read_bytes())
                        ),
                        child_archive=child["archive"],
                        native_diagnostics=diagnostics.summary(),
                        private_copy_cleanup=result["private_copy_cleanup"],
                    )
                    proof_path = output / "closure-proof.json"
                    once(proof_path, proof)
                    result["closure_proof"] = dict(
                        path=str(proof_path), sha256=sha(proof_path.read_bytes())
                    )
                    if (
                        all(
                            proof[k] is True
                            for k in (
                                "owned_tree_exited",
                                "control_complete",
                                "ledger_complete",
                                "evidence_complete",
                            )
                        )
                        and not proof["forced_kill"]
                    ):
                        session.close_allocation(
                            journal,
                            plan_sha256=binding.plan_sha256,
                            closure=result["closure_proof"],
                        )
                        result["allocation_closed"] = True
    result["elapsed_seconds"] = time.monotonic() - started
    result["fake_account_after"] = read(account)
    child = result.get("child_report", {})
    result["offline_qualification_passed"] = bool(
        fault == "first_model_failure"
        and result["allocation_closed"]
        and result["native_exit"] == 1
        and child.get("factory_count") == 2
        and child.get("server_count") == 2
        and child.get("unconfigured_503_observed") is True
        and result["journal"]["allowed_requests"] >= 1
        and child["stages"][0]["status"] == "failed"
        and all(r["status"] == "not_run" for r in child["stages"][1:])
    )
    once(output / "result.json", result)
    return result


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--offline":
        raise SystemExit("Only --offline <fresh-output> is enabled; paid execution is disabled")
    result = run_probe(Path(sys.argv[2]))
    print(
        {
            "offline_probe": sys.argv[2],
            "native_exit": result["native_exit"],
            "owned_tree_exited": result["owned_tree_exited"],
            "execution_ready": False,
        }
    )
    raise SystemExit(0 if result["offline_qualification_passed"] else 1)


if __name__ == "__main__":
    main()
