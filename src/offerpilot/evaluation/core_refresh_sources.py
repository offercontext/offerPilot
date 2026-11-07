"""Freeze per-file subject identities before execution; bind new observed origins to them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_origin_reads import OriginReads
from offerpilot.evaluation.core_refresh_contracts import read, require
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import ROOT, git, snapshot


def freeze_sources(manifest: RunManifestV2, path: Path) -> dict[str, Any]:
    path = safe_path(path.absolute())
    if "baseline" in manifest.variants:
        require(
            not path.is_relative_to(Path(manifest.variants["baseline"]["checkout"]).resolve()),
            "source archive cannot write into the baseline checkout",
        )
    require(not path.exists(), "source archive must be new")
    variants = {}
    for arm, target in manifest.variants.items():
        checkout = Path(target["checkout"]).resolve()
        before = snapshot(checkout)
        require(
            all(target[k] == v for k, v in before.items()), "source changed before origin archive"
        )
        names = set(
            git(checkout, "ls-files", "-z").split(b"\0")
            + git(checkout, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        ) - {b""}
        files = {}
        for raw in sorted(names):
            name = os.fsdecode(raw).replace("\\", "/")
            source = checkout / name
            files[name] = file_hash(source) if source.is_file() else "missing"
        require(
            digest(files) == target["files_hash"] and snapshot(checkout) == before,
            "source archive does not match frozen plan",
        )
        variants[arm] = dict(checkout=str(checkout), snapshot=before, files=files)
    value = dict(
        schema_version=3,
        kind="refresh-subject-source-files",
        run_manifest_hash=digest(manifest.model_dump(mode="json")),
        variants=variants,
    )
    atomic_json(path, value)
    return value


def source_origins(
    manifest: RunManifestV2, rows: list[TrialResultV2], directory: Path,
    *, _origin: OriginReads | None = None,
) -> dict[str, Any]:
    """Only new sealed handshakes and archived identities; no historical backfilling."""
    if _origin is not None:
        _origin.validate(directory, manifest)
    archive = read(directory / "source-files.json")
    require(
        archive.get("schema_version") == 3
        and archive.get("kind") == "refresh-subject-source-files"
        and archive["run_manifest_hash"] == digest(manifest.model_dump(mode="json"))
        and set(archive["variants"]) == set(manifest.variants),
        "source archive is for a different run",
    )
    for arm, target in manifest.variants.items():
        frozen = archive["variants"][arm]
        require(
            digest(frozen["files"]) == target["files_hash"]
            and frozen["snapshot"]
            == {k: target[k] for k in ("commit", "diff_hash", "files_hash", "lock_hash")}
            and Path(frozen["checkout"]).resolve() == Path(target["checkout"]).resolve(),
            "subject source archive changed",
        )
    origins = []
    bridge = ROOT / "scripts/evaluation_subject/core_bootstrap.py"
    for row in rows:
        evidence = _origin.evidence(directory, manifest, row) if _origin is not None else None
        if evidence is None:
            evidence = load(Path(row.private_directory), row.evidence_hash)
        require(not evidence.get("errors"), "refresh private evidence incomplete")
        handshakes = evidence.get("facts", {}).get("handshakes", [])
        if row.execution_status == "completed":
            require(bool(handshakes), "refresh product origin was not observed")
        checkout = Path(manifest.variants[row.variant_id]["checkout"]).resolve()
        files = archive["variants"][row.variant_id]["files"]
        for h in handshakes:
            policy = h.get("source_loading", {})
            prefix = Path(policy.get("initial_empty_prefix", ""))
            launcher = ROOT / "scripts/evaluation_subject/source_launcher.py"
            expected_roots = sorted({str(checkout / "src"), str(ROOT / "src"), str(ROOT / "scripts")})
            require(
                h["bytecode_write_disabled"] is True
                and h["bytecode_cache_prefix"] is None
                and prefix.is_absolute()
                and prefix.is_relative_to(Path(row.private_directory).resolve())
                and not prefix.exists()
                and policy.get("policy") == "bound-source-loader-v1"
                and policy.get("protected_roots") == expected_roots
                and policy.get("installed_before_project_imports") is True
                and policy.get("guard_active") is True
                and policy.get("sourceless_bound_modules_blocked") is True
                and policy.get("bytecode_writes_disabled") is True
                and policy.get("dependency_cache_prefix") is None
                and Path(policy.get("launcher_file", "")).resolve() == launcher.resolve()
                and policy.get("launcher_sha256") == file_hash(launcher),
                "refresh source-only import policy was not observed",
            )
            product, factory = (
                Path(h["product_file"]).resolve(),
                Path(h["product_factory_file"]).resolve(),
            )
            require(
                product.is_relative_to(checkout / "src")
                and factory.is_relative_to(checkout / "src"),
                "observed product is outside its frozen checkout",
            )
            require(
                files.get(product.relative_to(checkout).as_posix()) == h["product_file_sha256"]
                and files.get(factory.relative_to(checkout).as_posix())
                == h["product_factory_sha256"]
                and Path(h["evaluator_file"]).resolve() == bridge.resolve()
                and file_hash(bridge) == h["evaluator_file_sha256"],
                "refresh product/evaluator origin differs",
            )
        origins.append(dict(trial_key=list(row.identity()), handshakes=handshakes))
    return dict(schema_version=3, observations=origins, archived_sources_hash=digest(archive))
