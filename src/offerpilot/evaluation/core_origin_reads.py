"""Ephemeral projections from an actual strict storage read, never a validity cache."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, ContextManager

from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.registry import digest

CaptureOrigin = Callable[[Path, RunManifestV2, TrialResultV2, dict[str, Any]], None]


class OriginReads:
    """One qualification's metadata; its owner supplies previously frozen models."""

    def __init__(
        self, directory: Path, manifest: RunManifestV2, rows: list[TrialResultV2],
        *, _active: Callable[[], bool] | None = None,
    ) -> None:
        self.directory = directory.resolve()
        self._manifest_hash = digest(manifest.model_dump(mode="json"))
        self._expected = {row.identity(): (row.private_directory, row.evidence_hash) for row in rows}
        if len(self._expected) != len(rows) or set(self._expected) - {p.identity() for p in manifest.planned_trials}:
            raise ValueError("duplicate or foreign frozen origin trial")
        self._values: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
        self._active = _active
        self.closed = False

    def validate(self, directory: Path, manifest: RunManifestV2) -> None:
        if (self.closed or (self._active is not None and not self._active())
                or directory.resolve() != self.directory
                or digest(manifest.model_dump(mode="json")) != self._manifest_hash):
            raise ValueError("origin read scope expired or run manifest differs")

    def _binding(self, directory: Path, manifest: RunManifestV2, row: TrialResultV2) -> None:
        self.validate(directory, manifest)
        if self._expected.get(row.identity()) != (row.private_directory, row.evidence_hash):
            raise ValueError("origin trial/private/evidence binding differs")

    def capture(
        self, directory: Path, manifest: RunManifestV2, row: TrialResultV2, evidence: dict[str, Any]
    ) -> None:
        self._binding(directory, manifest, row)
        if row.identity() in self._values:
            raise ValueError("duplicate strict origin capture")
        # Copy before read_run adds any derived errors. Preserve every storage
        # error and the complete ordered handshakes, including unknown fields.
        self._values[row.identity()] = deepcopy(dict(
            errors=evidence.get("errors"),
            facts=dict(handshakes=evidence.get("facts", {}).get("handshakes", [])),
        ))

    def evidence(
        self, directory: Path, manifest: RunManifestV2, row: TrialResultV2
    ) -> dict[str, Any] | None:
        self._binding(directory, manifest, row)
        return deepcopy(self._values.get(row.identity()))

    def close(self) -> None:
        self.closed = True
        self._values.clear()
        self._expected.clear()


OriginScope = Callable[[Path], ContextManager[OriginReads]]
