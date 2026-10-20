"""Read-only original-evaluator audit. Run with its interpreter, -I and -B.

No store recovery, model generation, or imports from the new evaluator package.
The new host validates the journal; this child validates original frozen inputs.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def active(created: datetime, expires: datetime, at: datetime) -> bool:
    return bool(
        created.tzinfo
        and expires.tzinfo
        and at.tzinfo
        and created <= at < expires
        and 0 < (expires - created).total_seconds() <= 30 * 86400
    )


def forbidden(*args: object, **kwargs: object) -> None:
    raise PermissionError("historical reader has no network permission")


def main() -> None:
    args = json.load(sys.stdin)
    checkout = Path(args["checkout"]).resolve()
    require(
        sys.dont_write_bytecode
        and sys.pycache_prefix == args["bytecode_prefix"]
        and not Path(args["bytecode_prefix"]).exists(),
        "historical reader must load source without existing caches",
    )
    sys.path.insert(0, str(checkout / "src"))
    sys.path.insert(1, str(checkout))
    socket.socket.connect = forbidden  # type: ignore[method-assign]
    socket.socket.connect_ex = forbidden  # type: ignore[method-assign]
    socket.create_connection = forbidden  # type: ignore[assignment]
    socket.getaddrinfo = forbidden  # type: ignore[assignment]
    import offerpilot
    from offerpilot.evaluation import core_runner, runner
    from offerpilot.evaluation.core_governance import (
        GatePolicyV2,
        PromotionApproval,
        ReviewBundle,
        product_hash,
        verified_reference,
    )
    from offerpilot.evaluation.registry import digest
    from offerpilot.evaluation.core_planning import read_plan

    require(
        Path(offerpilot.__file__).resolve().is_relative_to(checkout / "src")
        and Path(core_runner.__file__).resolve().is_relative_to(checkout / "src")
        and runner.evaluator_hash() == args["evaluator_hash"],
        "original evaluator identity differs",
    )
    record_path = Path(args["record_path"])
    require(sha(record_path) == args["record_sha256"], "original baseline record bytes changed")
    record = json.loads(record_path.read_bytes())
    approval = PromotionApproval.model_validate(record["approval"])
    require(
        verified_reference(approval.record_ref, approval.record_hash), "old human authority changed"
    )
    authority = json.loads(Path(approval.record_ref).read_bytes())
    request_path = Path(authority["request_ref"])
    require(
        authority.get("kind") == "explicit-human-maintainer-approval-record"
        and sha(request_path) == authority["request_hash"],
        "old authority request not originally bound",
    )
    request = json.loads(request_path.read_bytes())
    require(
        request.get("kind") == "maintainer-initial-baseline-approval-request"
        and request["proposed_baseline_id"] == record["baseline_id"]
        and Path(request["proposed_store"]).resolve() == Path(args["store"]).resolve()
        and request["gate_hash"] == record["gate_hash"] == approval.gate_hash
        and request["run_manifest_hash"] == record["manifest_hash"] == approval.run_manifest_hash
        and request["candidate_snapshot_hash"] == record["product_snapshot_hash"]
        and request["evaluator_hash"] == record["evaluator_hash"],
        "old request baseline binding differs",
    )
    captures = {}
    for key, ref in request["evidence"].items():
        p = Path(ref["path"])
        require(sha(p) == ref["sha256"], "original approval evidence bytes changed: " + key)
        captures[key] = dict(path=str(p.resolve()), sha256=sha(p))
    require(
        {"results", "run_manifest", "run_plan", "initial_gate"} <= set(captures),
        "original raw bytes were not pinned by the approval request",
    )
    primary = Path(record["run_directory"])
    for name, key in [
        ("trial-results.json", "results"),
        ("run-manifest.json", "run_manifest"),
        ("run-plan.json", "run_plan"),
    ]:
        require(
            Path(captures[key]["path"]) == (primary / name).resolve(),
            "old raw path was not approved",
        )
    gate = json.loads(Path(record["gate_path"]).read_bytes())
    require(
        digest(gate) == record["gate_hash"]
        and gate.get("gate_satisfied") is True
        and gate.get("exit_code") == 0
        and gate.get("mode") == "enforce"
        and gate["sources"] == record["sources"],
        "old approved gate differs",
    )
    frozen_policy = GatePolicyV2.model_validate(gate["frozen_policy"])
    require(
        digest(frozen_policy.model_dump(mode="json")) == gate["policy_hash"], "old policy changed"
    )
    bundle = ReviewBundle.model_validate(gate["frozen_review"])
    at = datetime.fromisoformat(record["created_at"])
    require(
        active(approval.created_at, approval.expires_at, at), "old approval invalid at record time"
    )
    accepted = [
        r
        for r in bundle.independent_reviews
        if r.disposition == "accepted"
        and r.candidate_snapshot_hash == record["product_snapshot_hash"]
        and r.evaluator_hash == record["evaluator_hash"]
        and active(r.created_at, r.expires_at, at)
        and verified_reference(r.record_ref, r.record_hash)
    ]
    require(bool(accepted), "old independent review unbound at historical record time")
    native = json.loads(Path(args["native_return"]).read_bytes())
    require(
        json.loads(Path(args["approval_file"]).read_bytes()) == approval.model_dump(mode="json"),
        "old approval file differs from the approved record",
    )
    expected_command = [
        str(Path(args["interpreter"]).parent / "oc.exe"),
        "eval",
        "baseline",
        "promote",
        "--run",
        record["run_directory"],
        "--gate",
        record["gate_path"],
        "--approval",
        args["approval_file"],
        "--id",
        record["baseline_id"],
        "--store",
        args["store"],
    ]
    actual_command = native.get("command", [])
    # Windows path separators/case are not command-argument differences.
    require(
        len(actual_command) == len(expected_command)
        and all(
            (Path(a).resolve() == Path(b).resolve()) if i in {0, 5, 7, 9, 13} else a == b
            for i, (a, b) in enumerate(zip(actual_command, expected_command, strict=True))
        ),
        "historical native command does not target the approved promotion",
    )
    start, finish = (datetime.fromisoformat(native[k]) for k in ("started_at", "finished_at"))
    require(
        native.get("actual_native_exit") == 0
        and native.get("native_process_returned") is True
        and start <= at <= finish
        and active(approval.created_at, approval.expires_at, start)
        and active(approval.created_at, approval.expires_at, finish)
        and any(
            active(r.created_at, r.expires_at, start) and active(r.created_at, r.expires_at, finish)
            for r in accepted
        )
        and native.get("human_record_hash") == approval.record_hash
        and native.get("approval_hash") == sha(Path(args["approval_file"])),
        "historical native interval does not support authorization validity",
    )
    refs = []
    approved = None
    for source in record["sources"]:
        directory = Path(source["directory"])
        m, rows = core_runner.read_run(
            directory
        )  # Original strict evaluator/hash and fragment checks.
        plan = read_plan(directory / "run-plan.json", check_targets=False)
        require(
            digest(m.model_dump(mode="json"))
            == source["manifest_hash"]
            == digest(plan.model_dump(mode="json"))
            and str(m.run_id) == source["run_id"],
            "old source manifest/plan differs",
        )
        refs.append(
            dict(
                directory=str(directory.resolve()),
                manifest_hash=source["manifest_hash"],
                evidence=[
                    dict(
                        identity=list(r.identity()),
                        evidence_hash=r.evidence_hash,
                        execution_status=r.execution_status,
                        verdict=r.verdict,
                    )
                    for r in rows
                ],
            )
        )
        if str(m.run_id) == source["run_id"] and source["manifest_hash"] == record["manifest_hash"]:
            require(
                product_hash(m) == record["product_snapshot_hash"], "old product identity differs"
            )
            target = m.variants["candidate"]
            require(
                Path(target["checkout"]).resolve() == checkout
                and Path(target["interpreter"]).resolve() == Path(args["interpreter"]).resolve(),
                "old reader does not use the approved checkout and interpreter",
            )
            group = [r for r in rows if r.variant_id == "candidate"]
            planned = [p for p in m.planned_trials if p.variant_id == "candidate"]
            require(
                len(group) == len(planned) == 57
                and all(r.execution_status == "completed" and r.verdict == "pass" for r in group),
                "old approved raw no longer supports the recorded result",
            )
            approved = dict(target=target, product_snapshot_hash=product_hash(m))
    require(approved is not None, "original approved arm absent")
    print(
        json.dumps(
            dict(
                schema_version=3,
                kind="original-evaluator-readonly-audit",
                source_loading_policy="empty-prefix-bytecode-disabled",
                original_record_sha256=sha(record_path),
                original_record_digest=digest(record),
                approval_request=dict(path=str(request_path.resolve()), sha256=sha(request_path)),
                original_raw_byte_binding=True,
                captures=captures,
                sources=refs,
                approved=approved,
                evaluator_hash=runner.evaluator_hash(),
                reader_file=str(Path(core_runner.__file__).resolve()),
                reader_file_sha256=sha(Path(core_runner.__file__)),
                interpreter=sys.executable,
                historical_time_basis="record.created_at proxy corroborated by a native command interval; journal has no committed_at",
                historical_record_time=at.isoformat(),
                native_interval=dict(start=start.isoformat(), finish=finish.isoformat()),
                network_requests=0,
                writes_permitted=False,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
