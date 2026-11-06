"""Nine-item driver with an explicitly zero-cost, loopback-only execution surface.

The real 13-item parent manifest is retained. Simulation child identities use mode
deterministic and a separate run ID, so they cannot be imported as the parent results.
There is intentionally no paid execution mode or shared-account write path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
import socket
from typing import Any, Iterator
from uuid import uuid4

from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_contracts import RunGrant, RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_planning import create_plan, read_plan
from offerpilot.evaluation.core_registry import ITEMS
from offerpilot.evaluation.core_runner import run_trial
from offerpilot.evaluation.core_targeted_ledger import OfflineLedger, SELECTED, TargetedLedger
from offerpilot.evaluation.core_targeted_kernel import run_session
from offerpilot.evaluation.core_targeted_evidence import (
    read as read,
    checked_result as checked_result,
    check_request_bindings as check_request_bindings,
    probe_succeeded as probe_succeeded,
)
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json


@contextmanager
def loopback_only() -> Iterator[None]:
    originals = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
        socket.socket.sendto,
    )

    def allowed(address: Any) -> None:
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise PermissionError("offline driver refuses non-loopback networking")

    def connect(sock: Any, address: Any) -> Any:
        allowed(address)
        return originals[0](sock, address)

    def connect_ex(sock: Any, address: Any) -> Any:
        allowed(address)
        return originals[1](sock, address)

    def resolve(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        allowed((host, port))
        return originals[2](host, port, *args, **kwargs)

    def sendto(sock: Any, data: Any, *args: Any) -> Any:
        allowed(args[-1])
        return originals[3](sock, data, *args)

    setattr(socket.socket, "connect", connect)
    setattr(socket.socket, "connect_ex", connect_ex)
    socket.getaddrinfo = resolve
    setattr(socket.socket, "sendto", sendto)
    try:
        yield
    finally:
        setattr(socket.socket, "connect", originals[0])
        setattr(socket.socket, "connect_ex", originals[1])
        socket.getaddrinfo = originals[2]
        setattr(socket.socket, "sendto", originals[3])


def prepare(
    suite: Path,
    variants: Path,
    output: Path,
    selected: list[str],
    *,
    max_requests: int = 27,
    simulated_cny: float = 1,
    lifetime_seconds: int = 3600,
    faults: dict[str, str] | None = None,
) -> dict[str, Any]:
    if (
        len(selected) != 9
        or len(set(selected)) != 9
        or set(selected) != set(SELECTED)
        or type(max_requests) is not int
        or not 0 < max_requests <= 27
    ):
        raise ValueError("selection must be exactly the approved nine; requests <=27")
    faults = faults or {}
    if set(faults) - set(SELECTED) or set(faults.values()) - {
        "http_error",
        "timeout",
        "extra_reads",
    }:
        raise ValueError("invalid offline transport fault")
    if output.exists():
        raise ValueError("offline output already exists")
    output.mkdir(parents=True)
    profile = dict(
        profile_id="offline-simulation-not-priced",
        endpoint="https://offline.invalid/v1",
        model="eval-core",
        credential_ref="offline:no-credential",
        temperature=0,
        max_input_bytes=32000,
        max_output_tokens=1024,
        timeout_seconds=10,
        max_attempts_per_trial=8,
        fallback=[],
        thinking="disabled",
        price_currency="CNY",
        input_cny_per_million=1,
        output_cny_per_million=1,
        price_reference="simulation-only; artificial arithmetic, no price quote",
        price_checked_at="1970-01-01T00:00:00+00:00",
    )
    atomic_json(output / "dummy-profile.json", profile)
    manifest = create_plan(
        suite,
        variants,
        output / "parent",
        mode="real",
        repeat=1,
        provider_path=output / "dummy-profile.json",
    )
    if (
        manifest.suite_id != "harness-core-v1"
        or set(manifest.variants) != {"candidate"}
        or len(manifest.planned_trials) != 13
    ):
        raise ValueError("targeted driver requires the complete 13-item candidate parent")
    assert manifest.provider_profile_hash is not None
    grant = RunGrant(
        grant_id=uuid4(),
        profile_hash=manifest.provider_profile_hash,
        suite_hash=manifest.suite_hash,
        variants=["candidate"],
        repeats=1,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds),
        max_requests=max_requests,
        max_cny=simulated_cny,
        authorization_ref="OFFLINE SIMULATION ONLY; no provider authorization or real allocation",
    )
    atomic_json(output / "simulation-grant.json", grant.model_dump(mode="json"))
    plan = dict(
        schema_version=3,
        simulation_only=True,
        selected=selected,
        execution_directory=str(output.resolve()),
        parent_file="parent/run-plan.json",
        parent_hash=file_hash(output / "parent/run-plan.json"),
        grant_hash=file_hash(output / "simulation-grant.json"),
        real_budget_reservation=0,
        max_http_requests_including_probe=max_requests,
        automatic_retries=0,
        parent_denominator=13,
        faults=faults,
        release_eligible=False,
    )
    atomic_json(output / "targeted-plan.json", dict(plan=plan, plan_hash=digest(plan)))
    return plan


def execute(path: Path) -> dict[str, Any]:
    with loopback_only():
        return _execute(path)


def _execute(path: Path) -> dict[str, Any]:
    directory = path.parent.resolve()
    for child_path in directory.rglob("*"):
        if child_path.is_symlink() or not child_path.resolve().is_relative_to(directory):
            raise ValueError("offline session must not contain linked/escaped files")
    envelope = read(path)
    plan = envelope["plan"]
    if (
        digest(plan) != envelope["plan_hash"]
        or plan.get("schema_version") != 3
        or plan.get("simulation_only") is not True
        or plan.get("automatic_retries") != 0
        or plan.get("parent_denominator") != 13
        or plan.get("execution_directory") != str(directory)
        or plan.get("parent_file") != "parent/run-plan.json"
        or len(plan["selected"]) != 9
        or set(plan["selected"]) != set(SELECTED)
    ):
        raise ValueError("invalid offline plan/selection")
    if set(plan.get("faults", {})) - set(SELECTED) or set(plan.get("faults", {}).values()) - {
        "http_error",
        "timeout",
        "extra_reads",
    }:
        raise ValueError("invalid offline fault selection")
    parent_path = directory / "parent/run-plan.json"
    grant_path = directory / "simulation-grant.json"
    if file_hash(parent_path) != plan["parent_hash"] or file_hash(grant_path) != plan["grant_hash"]:
        raise ValueError("bound parent/grant changed")
    # Snapshot/environment mismatch refuses the entire execution before any send.
    manifest = read_plan(parent_path)
    grant = RunGrant.model_validate_json(grant_path.read_bytes())
    profile = manifest.provider_profile
    if (
        manifest.mode != "real"
        or manifest.repeats != 1
        or set(manifest.variants) != {"candidate"}
        or len(manifest.planned_trials) != 13
        or {p.execution_item_id for p in manifest.planned_trials}
        != {k for k, v in ITEMS.items() if v["real"]}
        or profile is None
        or profile.credential_ref != "offline:no-credential"
        or profile.endpoint != "https://offline.invalid/v1"
        or profile.model != "eval-core"
        or profile.max_attempts_per_trial != 8
        or grant.max_requests > 27
        or grant.max_requests != plan["max_http_requests_including_probe"]
        or grant.profile_hash != manifest.provider_profile_hash
        or grant.suite_hash != manifest.suite_hash
        or grant.variants != ["candidate"]
        or grant.repeats != 1
    ):
        raise ValueError("offline scope differs from approved envelope")
    backend = OfflineBackend(plan.get("faults", {}))
    return run_session(
        manifest,
        grant,
        directory,
        envelope["plan_hash"],
        plan["selected"],
        backend,
        lambda initialize: OfflineLedger(
            manifest, grant, directory, envelope["plan_hash"], initialize=initialize
        ),
    ).report


class OfflineBackend:
    simulation_only = True
    private_root = "simulation-private"
    report_filename = "offline-report.json"

    def __init__(self, faults: dict[str, str]) -> None:
        self.faults = faults

    def run(
        self,
        stage: str,
        manifest: RunManifestV2,
        child: PlannedTrial,
        private: Path,
        ledger: TargetedLedger,
    ) -> TrialResultV2:
        return run_trial(
            manifest,
            child,
            private,
            ledger,
            offline_simulation="probe" if stage == "probe" else "target",
            offline_fault=self.faults.get(stage),
        )
