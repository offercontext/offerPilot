"""Observed imports must be bound to archived source bytes rather than a later checkout."""

from pathlib import Path
import json

import pytest

from offerpilot.evaluation import core_refresh_sources as sources
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import rows


@pytest.fixture
def origins(manifest, tmp_path, monkeypatch):
    m = manifest.model_copy(deep=True)
    observed = {}
    archived = {}
    bridge = ROOT / "scripts/evaluation_subject/core_bootstrap.py"
    for arm, target in m.variants.items():
        checkout = Path(target["checkout"])
        (checkout / "src/offerpilot").mkdir(parents=True)
        package = checkout / "src/offerpilot/__init__.py"
        package.write_text(arm)
        factory = checkout / "src/offerpilot/api.py"
        factory.write_text("def create_app(): pass")
        files = {p.relative_to(checkout).as_posix(): file_hash(p) for p in (package, factory)}
        target["files_hash"] = digest(files)
        archived[arm] = dict(
            checkout=str(checkout.resolve()),
            files=files,
            snapshot={k: target[k] for k in ("commit", "diff_hash", "files_hash", "lock_hash")},
        )
        observed[arm] = dict(
            bytecode_write_disabled=True,
            bytecode_cache_prefix=str(tmp_path / arm / "unused-cache"),
            product_file=str(package),
            product_file_sha256=file_hash(package),
            product_factory_file=str(factory),
            product_factory_sha256=file_hash(factory),
            evaluator_file=str(bridge),
            evaluator_file_sha256=file_hash(bridge),
        )
    archive = dict(
        schema_version=3,
        kind="refresh-subject-source-files",
        run_manifest_hash=digest(m.model_dump(mode="json")),
        variants=archived,
    )
    directory = tmp_path / "origins"
    directory.mkdir()
    path = directory / "source-files.json"
    path.write_text(json.dumps(archive), encoding="utf-8")
    selected = [
        next(r for r in rows(m) if r.variant_id == arm).model_copy(
            update={"private_directory": str(tmp_path / arm)}
        )
        for arm in ("baseline", "candidate")
    ]
    monkeypatch.setattr(
        sources,
        "load",
        lambda private, *_: dict(errors={}, facts=dict(handshakes=[observed[private.name]])),
    )
    return m, selected, directory, observed, archive


def test_original_imports_survive_later_candidate_file_changes(origins):
    m, rows, directory, observed, _ = origins
    first = sources.source_origins(m, rows, directory)
    Path(observed["candidate"]["product_factory_file"]).write_text("later candidate implementation")
    assert sources.source_origins(m, rows, directory) == first


@pytest.mark.parametrize(
    "mutation",
    [
        "product_hash",
        "factory_hash",
        "foreign_checkout",
        "bridge",
        "archive_hash",
        "missing_origin",
        "cache_policy",
    ],
)
def test_unbound_observed_origin_is_rejected(origins, monkeypatch, mutation):
    m, rows, directory, observed, archive = origins
    h = observed["baseline"]
    if mutation == "product_hash":
        h["product_file_sha256"] = "0" * 64
    elif mutation == "factory_hash":
        h["product_factory_sha256"] = "0" * 64
    elif mutation == "foreign_checkout":
        h["product_file"] = observed["candidate"]["product_file"]
    elif mutation == "bridge":
        h["evaluator_file"] = observed["candidate"]["product_factory_file"]
    elif mutation == "archive_hash":
        archive["variants"]["baseline"]["files"]["src/offerpilot/api.py"] = "0" * 64
        (directory / "source-files.json").write_text(json.dumps(archive), encoding="utf-8")
    elif mutation == "cache_policy":
        h["bytecode_write_disabled"] = False
    else:
        monkeypatch.setattr(sources, "load", lambda *_: dict(errors={}, facts=dict(handshakes=[])))
    with pytest.raises(ValueError):
        sources.source_origins(m, rows, directory)


def test_source_archive_matches_full_plan_and_refuses_overwrite(origins, monkeypatch):
    m, _, directory, _, archive = origins
    monkeypatch.setattr(sources, "snapshot", lambda p: archive["variants"][p.name]["snapshot"])
    monkeypatch.setattr(
        sources,
        "git",
        lambda p, *_: b"\0".join(k.encode() for k in archive["variants"][p.name]["files"]) + b"\0",
    )
    output = directory / "new-source-files.json"
    result = sources.freeze_sources(m, output)
    assert result["run_manifest_hash"] == digest(m.model_dump(mode="json"))
    assert result["variants"] == archive["variants"]
    with pytest.raises(ValueError, match="new"):
        sources.freeze_sources(m, output)
