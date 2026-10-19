"""Persistent simulation ledger; caller owns the kernel session lock for its lifetime."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from offerpilot.evaluation.core_contracts import RunGrant, RunManifestV2
from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_subject import RunLedger
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.runner import snapshot
from offerpilot.evaluation.reporting import atomic_json

SELECTED = (
    "E01.runtime_read",
    "E02.runtime_clarify",
    "E03.runtime_reject",
    "E04.runtime_edit",
    "E05.runtime_replay",
    "E06.legacy_saved",
    "E06.runtime_saved",
    "E06.legacy_missing",
    "E07.runtime_reattach",
)


def stage_trial(manifest: RunManifestV2, stage: str, simulation_only: bool) -> PlannedTrial:
    if stage != "probe" and stage not in SELECTED:
        raise ValueError("stage outside selected nine")
    item = SELECTED[0] if stage == "probe" else stage
    parent = next(p for p in manifest.planned_trials if p.execution_item_id == item)
    if simulation_only:
        return parent.model_copy(
            update={
                "run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:" + stage),
                "mode": "deterministic",
            }
        )
    if stage == "probe":
        return parent.model_copy(
            update={"run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":diagnostic:probe")}
        )
    return parent


class TargetedLedger(RunLedger):
    """Reuse pre-ACK reservation checks, retain all allowed sends across restarts."""

    def __init__(
        self,
        manifest: RunManifestV2,
        grant: RunGrant,
        directory: Path,
        binding: str,
        *,
        initialize: bool = False,
        simulation_only: bool = False,
        before_send: Callable[[dict[str, Any]], None] | None = None,
        after_record: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        if not 0 < grant.max_requests <= 27:
            raise ValueError("targeted global request limit must be <=27")
        super().__init__(manifest, grant, directory)
        # Serialize validation and external anchors as well as the base ledger write.
        # Keep the base ledger's lock independent; super().reserve acquires it.
        self.reserve_lock = threading.Lock()
        self.simulation_only = simulation_only
        self.before_send = before_send
        self.after_record = after_record
        self.binding = binding
        self.stage = ""
        self.tail = "0" * 64
        self.events: set[str] = set()
        self.stage_counts: dict[str, int] = {}
        path = directory / "run-budget.jsonl"
        anchor = directory / "ledger-anchor.json"
        if initialize:
            if path.exists() or anchor.exists():
                raise ValueError("existing ledger/anchor cannot be initialized again")
            with path.open("xb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            atomic_json(anchor, self.receipt())
        if not path.is_file() or not anchor.is_file() or path.is_symlink() or anchor.is_symlink():
            raise ValueError("ledger/anchor missing or linked; never reset reservations")
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("partial ledger; preserve reservation, no resume")
        for line in raw.splitlines():
            row = json.loads(line)
            actual = row.pop("row_hash")
            if (
                row["previous_hash"] != self.tail
                or digest(row) != actual
                or row["binding"] != binding
                or row["simulation_only"] is not self.simulation_only
                or type(row["allowed"]) is not bool
                or row["event_id"] in self.events
            ):
                raise ValueError("simulation ledger binding corrupt")
            amount = Decimal(row["reserve_cny"])
            if not amount.is_finite() or amount < 0:
                raise ValueError("simulation amount invalid")
            self.validate_event(row["stage"], row)
            profile = manifest.provider_profile
            assert profile is not None
            computed = (
                Decimal(row["input_bytes"]) * Decimal(str(profile.input_cny_per_million))
                + Decimal(row["output_cap"]) * Decimal(str(profile.output_cny_per_million))
            ) / 1000000
            if amount != computed:
                raise ValueError("simulation reservation arithmetic changed")
            self.events.add(row["event_id"])
            self.tail = actual
            row["row_hash"] = actual
            self.rows.append(row)
            if row["allowed"]:
                self.requests += 1
                self.reserved_cny += amount
                stage = row["stage"]
                self.stage_counts[stage] = self.stage_counts.get(stage, 0) + 1
        if self.requests > min(27, grant.max_requests) or self.reserved_cny > Decimal(
            str(grant.max_cny)
        ):
            raise ValueError("restored ledger exceeds simulation grant")
        if any(n > (1 if stage == "probe" else 8) for stage, n in self.stage_counts.items()):
            raise ValueError("restored simulation stage exceeds request limit")
        if json.loads(anchor.read_bytes()) != self.receipt():
            raise ValueError("ledger anchor mismatch or complete-line truncation")

    def receipt(self) -> dict[str, Any]:
        return dict(
            simulation_only=self.simulation_only,
            binding=self.binding,
            row_count=len(self.rows),
            tail=self.tail,
            requests=self.requests,
            retained_cny=str(self.reserved_cny),
        )

    def check_prefix(self, prefix: dict[str, Any]) -> None:
        count = prefix.get("row_count")
        if type(count) is not int or not 0 <= count <= len(self.rows):
            raise ValueError("state ledger prefix missing/truncated")
        rows = self.rows[:count]
        expected = dict(
            simulation_only=self.simulation_only,
            binding=self.binding,
            row_count=count,
            tail=rows[-1]["row_hash"] if rows else "0" * 64,
            requests=sum(r["allowed"] for r in rows),
            retained_cny=str(
                sum((Decimal(r["reserve_cny"]) for r in rows if r["allowed"]), Decimal(0))
            ),
        )
        if prefix != expected:
            raise ValueError("state and durable ledger prefix differ")

    def reserve(self, event: dict[str, Any]) -> None:
        with self.reserve_lock:
            if event.get("purpose") != "provider_send" or event.get("blocked"):
                return
            identity = digest([self.stage, event["trial_key"], event["epoch"], event["seq"]])
            if not self.stage or identity in self.events:
                raise ValueError("duplicate or unbound send; ACK refused")
            self.validate_event(self.stage, event)
            for variant in self.manifest.variants.values():
                current = snapshot(Path(variant["checkout"]))
                if any(current[k] != variant[k] for k in current):
                    raise ValueError("source snapshot changed before send")
            if self.stage_counts.get(self.stage, 0) >= (1 if self.stage == "probe" else 8):
                raise TimeoutError("simulation stage request limit exhausted")
            if self.before_send:
                self.before_send(self.receipt())
            super().reserve(event)

    def validate_event(self, stage: str, event: dict[str, Any]) -> None:
        if stage != "probe" and stage not in SELECTED:
            raise ValueError("stage outside selected nine")
        item = "E01.runtime_read" if stage == "probe" else stage
        parent = next(
            (p for p in self.manifest.planned_trials if p.execution_item_id == item), None
        )
        if parent is None:
            raise ValueError("foreign stage in simulation ledger")
        expected = stage_trial(self.manifest, stage, self.simulation_only).model_dump(
            mode="json",
            include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"},
        )
        if event["trial_key"] != expected or type(event["epoch"]) is not int or event["epoch"] < 1:
            raise ValueError("foreign simulation send identity")
        profile = self.manifest.provider_profile
        assert profile is not None
        if (
            type(event["input_bytes"]) is not int
            or not 0 <= event["input_bytes"] <= profile.max_input_bytes
            or type(event["output_cap"]) is not int
            or not 0 < event["output_cap"] <= profile.max_output_tokens
            or type(event["seq"]) is not int
            or event["seq"] < 1
        ):
            raise ValueError("invalid simulation send limits")
        if "event_id" in event and event["event_id"] != digest(
            [stage, event["trial_key"], event["epoch"], event["seq"]]
        ):
            raise ValueError("invalid persisted event identity")

    def record(self, row: dict[str, Any], event: dict[str, Any]) -> None:
        identity = digest([self.stage, event["trial_key"], event["epoch"], event["seq"]])
        row.update(
            simulation_only=self.simulation_only,
            binding=self.binding,
            stage=self.stage,
            epoch=event["epoch"],
            event_id=identity,
            previous_hash=self.tail,
            input_bytes=event["input_bytes"],
            output_cap=event["output_cap"],
        )
        row["row_hash"] = digest(row)
        # A failed durable write cannot produce an ACK. Partial lines refuse resume.
        with (self.directory / "run-budget.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.tail = row["row_hash"]
        self.events.add(identity)
        if row["allowed"]:
            self.stage_counts[self.stage] = self.stage_counts.get(self.stage, 0) + 1
        atomic_json(self.directory / "ledger-anchor.json", self.receipt())
        if self.after_record:
            self.after_record(dict(row), self.receipt())


class OfflineLedger(TargetedLedger):
    """Simulation adapter; persistence and request limits use the shared implementation."""

    def __init__(
        self,
        manifest: RunManifestV2,
        grant: RunGrant,
        directory: Path,
        binding: str,
        *,
        initialize: bool = False,
    ) -> None:
        super().__init__(
            manifest, grant, directory, binding, initialize=initialize, simulation_only=True
        )
