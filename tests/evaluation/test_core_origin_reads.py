"""Same strict storage load, real source rules, and command-only origin lifetimes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from offerpilot.evaluation import core_runner as runner, core_refresh_sources as sources
from offerpilot.evaluation import core_storage as storage
from offerpilot.evaluation.core_origin_reads import OriginReads
from offerpilot.evaluation.core_release_reads import CommandReads, DependencyClosure
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.registry import file_hash
from tests.evaluation.test_core_refresh_sources import origins as origins
from tests.evaluation.test_core_governance import manifest as manifest


@pytest.fixture
def sealed(origins, monkeypatch):
    m, saved, directory, observed, archive = origins
    for index, row in enumerate(saved):
        private = directory / "private" / row.variant_id
        private.mkdir(parents=True)
        row = row.model_copy(update={"private_directory": str(private)})
        key = dict(zip(("run_id", "variant_id", "execution_item_id", "mode", "repeat_index"), row.identity(), strict=True))
        h = observed[row.variant_id]
        h["source_loading"]["initial_empty_prefix"] = str(private / "unused-cache")
        h.update(checkout_sha=m.variants[row.variant_id]["commit"], interpreter=m.variants[row.variant_id]["interpreter"], **m.variants[row.variant_id]["runtime"])
        h["extra_observation"] = {"ordered": [2, 1], "unknown_field": True}
        (private / ".eval-trial.json").write_text(json.dumps(key), encoding="utf-8")
        evidence = dict(trial_key=key, facts=dict(handshakes=[h, deepcopy(h)]))
        saved[index] = row.model_copy(update={"evidence_hash": storage.seal(private, evidence)})
    (directory / "run-manifest.json").write_text(m.model_dump_json(), encoding="utf-8")
    (directory / "trial-results.json").write_text(json.dumps([r.model_dump(mode="json") for r in saved]), encoding="utf-8")
    monkeypatch.setattr(sources, "load", storage.load)
    return m, saved, directory, observed, archive


def executed(rows, saved):
    keys = {r.identity() for r in saved}
    return [r for r in rows if r.identity() in keys]


def test_strict_real_load_shared_origins_equal_default_double_load(sealed, monkeypatch):
    m, saved, directory, _, _ = sealed
    calls = []
    def counted(*args):
        calls.append(args)
        return storage.load(*args)
    monkeypatch.setattr(runner, "load", counted)
    monkeypatch.setattr(sources, "load", counted)
    default_m, default_rows = runner.read_run(directory)
    default = sources.source_origins(default_m, executed(default_rows, saved), directory)
    assert len(calls) == 4
    calls.clear()
    scope = OriginReads(directory, m, saved)
    shared_m, shared_rows = runner.read_run(directory, _capture_origin=scope.capture)
    shared = sources.source_origins(shared_m, executed(shared_rows, saved), directory, _origin=scope)
    assert len(calls) == 2
    assert default_rows == shared_rows and default == shared and digest(default) == digest(shared)
    assert shared["observations"][0]["handshakes"][0]["extra_observation"]["ordered"] == [2, 1]
    assert len(shared["observations"][0]["handshakes"]) == 2


@pytest.mark.parametrize("damage", ["fragment", "private_file"])
def test_original_storage_errors_retained_and_source_rules_reject(sealed, damage):
    m, saved, directory, _, _ = sealed
    row = saved[0]
    private = Path(row.private_directory)
    if damage == "fragment":
        (private / "fragment-calls.json").write_text("corrupt", encoding="utf-8")
        error = "calls"
    else:
        # A genuinely sealed private file whose bytes change afterwards.
        (private / "answer.txt").write_bytes(b"original")
        evidence = storage.load(private, row.evidence_hash)
        evidence["files"] = {"answer.txt": {"sha256": file_hash(private / "answer.txt")}}
        row = row.model_copy(update={"evidence_hash": storage.seal(private, evidence)})
        saved[0] = row
        (private / "answer.txt").write_bytes(b"tampered")
        error = "file:answer.txt"
    scope = OriginReads(directory, m, saved)
    raw = storage.load(private, row.evidence_hash)
    scope.capture(directory, m, row, raw)
    raw["errors"].clear()
    assert error in scope.evidence(directory, m, row)["errors"]
    with pytest.raises(ValueError, match="incomplete"):
        sources.source_origins(m, [row], directory, _origin=scope)


def test_capture_precedes_derived_handshake_error_and_is_deep_copy(sealed, monkeypatch):
    m, saved, directory, _, _ = sealed
    scope = OriginReads(directory, m, saved)
    original_load = storage.load
    raw_values = []
    def actual(*args):
        raw = original_load(*args)
        raw_values.append(raw)
        # This original metadata will trigger only the strict reader's derived error.
        raw["facts"]["handshakes"][0]["checkout_sha"] = "different"
        return raw
    monkeypatch.setattr(runner, "load", actual)
    runner.read_run(directory, _capture_origin=scope.capture)
    assert raw_values[0]["errors"]["handshake"] == "target_identity_missing_or_changed"
    value = scope.evidence(directory, m, saved[0])
    assert value["errors"] == {}
    value["facts"]["handshakes"].clear()
    assert len(scope.evidence(directory, m, saved[0])["facts"]["handshakes"]) == 2


@pytest.mark.parametrize("binding", ["manifest", "directory", "trial", "private", "hash", "duplicate", "expired"])
def test_scope_rejects_wrong_or_expired_capture(sealed, binding):
    m, saved, directory, _, _ = sealed
    scope = OriginReads(directory, m, saved)
    parsed, row, where = m.model_copy(deep=True), saved[0].model_copy(deep=True), directory
    raw = storage.load(Path(row.private_directory), row.evidence_hash)
    if binding == "manifest":
        parsed.variants["candidate"]["commit"] = "0" * 40
    elif binding == "directory":
        where = directory.parent
    elif binding == "trial":
        row = row.model_copy(update={"repeat_index": 3})
    elif binding == "private":
        row = row.model_copy(update={"private_directory": str(directory / "foreign")})
    elif binding == "hash":
        row = row.model_copy(update={"evidence_hash": "0" * 64})
    elif binding == "duplicate":
        scope.capture(directory, m, row, raw)
    else:
        scope.close()
    with pytest.raises(ValueError):
        scope.capture(where, parsed, row, raw)


def test_missing_capture_uses_original_load_and_closed_scope_rejects(sealed, monkeypatch):
    m, saved, directory, _, _ = sealed
    calls = []
    def actual(*args):
        calls.append(args)
        return storage.load(*args)
    monkeypatch.setattr(sources, "load", actual)
    scope = OriginReads(directory, m, saved)
    assert sources.source_origins(m, saved, directory, _origin=scope)
    assert len(calls) == 2
    scope.close()
    with pytest.raises(ValueError):
        sources.source_origins(m, saved, directory, _origin=scope)
    assert len(calls) == 2


def test_command_scope_requires_prior_frozen_manifest_and_collecting_interval(sealed, monkeypatch):
    m, saved, directory, _, _ = sealed
    closure = DependencyClosure.for_run(directory)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: closure)
    command = CommandReads()
    with pytest.raises(ValueError):
        with command.origin_scope(directory):
            pytest.fail("outside command interval")
    captured = []
    def baseline(*_):
        with command.origin_scope(directory) as scope:
            captured.append(scope)
            parsed, result = runner.read_run(directory, _capture_origin=scope.capture)
            sources.source_origins(parsed, executed(result, saved), directory, _origin=scope)
        assert scope.closed
        return {"sources": []}
    command.baseline(directory.parent / "store", "test", baseline)
    assert captured[0].closed
    with pytest.raises(ValueError):
        captured[0].evidence(directory, m, saved[0])


@pytest.mark.parametrize("change", ["manifest_before_scope", "rows_before_scope", "raw_after_load", "duplicate_scope", "qualification_failure"])
def test_command_origin_failure_never_adopts_metadata_or_results(sealed, monkeypatch, change):
    m, saved, directory, _, _ = sealed
    closure = DependencyClosure.for_run(directory)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: closure)
    command = CommandReads()
    captured = []
    def baseline(*_):
        if change == "manifest_before_scope":
            changed = m.model_dump(mode="json")
            changed["variants"]["candidate"]["commit"] = "0" * 40
            (directory / "run-manifest.json").write_text(json.dumps(changed), encoding="utf-8")
        if change == "rows_before_scope":
            (directory / "trial-results.json").write_text("[]", encoding="utf-8")
        with command.origin_scope(directory) as scope:
            captured.append(scope)
            parsed, result = runner.read_run(directory, _capture_origin=scope.capture)
            sources.source_origins(parsed, executed(result, saved), directory, _origin=scope)
            if change == "raw_after_load":
                (Path(saved[0].private_directory) / "fragment-calls.json").write_text("changed", encoding="utf-8")
            if change == "qualification_failure":
                raise ValueError("later qualification failed")
        if change == "duplicate_scope":
            with command.origin_scope(directory):
                pytest.fail("second qualification scope")
        return {"sources": []}
    with pytest.raises(ValueError):
        command.baseline(directory.parent / "store", "test", baseline)
    assert all(scope.closed for scope in captured)
    assert command._pending_runs == {} and command._runs == {} and command._baselines == {}
    assert not list(directory.glob("*receipt*"))
