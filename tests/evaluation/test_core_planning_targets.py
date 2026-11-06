"""Target scheduling keeps source trust, full probes, and final freshness."""
from copy import deepcopy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, get_ident
from types import SimpleNamespace

import pytest

from offerpilot.evaluation import core_planning as planning
from offerpilot.evaluation import core_release as release
from offerpilot.evaluation import core_release_reads as reads
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.runner import ROOT
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_release_reads import subject as subject


def test_source_and_runtime_overlap_with_complete_source_barrier(manifest, tmp_path, monkeypatch):
    original_source, original_runtime = planning.snapshot, planning.target_environment
    source_started = {name: Event() for name in manifest.variants}
    runtime_started = {name: Event() for name in manifest.variants}
    finished, calls, lock = set(), [], Lock()

    def source(path):
        name = path.name
        source_started[name].set()
        assert all(event.wait(5) for event in source_started.values())
        result = original_source(path)
        with lock:
            finished.add(name)
            calls.append(("source", name))
        return result

    def runtime(variant):
        with lock:
            assert finished == set(manifest.variants)
            calls.append(("runtime", variant.variant_id))
        runtime_started[variant.variant_id].set()
        assert all(event.wait(5) for event in runtime_started.values())
        return original_runtime(variant)

    monkeypatch.setattr(planning, "snapshot", source)
    monkeypatch.setattr(planning, "target_environment", runtime)
    assert planning.read_plan(tmp_path / "run/run-plan.json") == manifest
    assert sorted(calls) == [(stage, name) for stage in ("runtime", "source")
                             for name in ("baseline", "candidate")]


@pytest.mark.parametrize("stage", ["source", "runtime"])
def test_failure_joins_other_started_work_and_keeps_manifest_error_order(
    manifest, tmp_path, monkeypatch, stage
):
    originals = dict(source=planning.snapshot, runtime=planning.target_environment)
    started = {name: Event() for name in manifest.variants}
    release_peer, baseline_failed = Event(), Event()
    primary = ValueError("baseline source failed") if stage == "source" else subprocess.TimeoutExpired("metadata-only", 30)
    secondary = RuntimeError("candidate validation failed")
    runtime_calls = []

    def fail(value):
        name = value.name if stage == "source" else value.variant_id
        started[name].set()
        if name == "baseline":
            assert started["candidate"].wait(5)
            baseline_failed.set()
            raise primary
        assert release_peer.wait(5)
        raise secondary

    def runtime(variant):
        runtime_calls.append(variant.variant_id)
        return originals["runtime"](variant)

    monkeypatch.setattr(planning, "target_environment", runtime)
    monkeypatch.setattr(planning, "snapshot" if stage == "source" else "target_environment", fail)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(planning.read_plan, tmp_path / "run/run-plan.json")
        try:
            assert all(event.wait(5) for event in started.values())
            assert baseline_failed.wait(5)
            assert not future.done(), "failure escaped while a peer was still running"
        finally:
            release_peer.set()
        with pytest.raises(type(primary)) as raised:
            future.result(timeout=5)
    assert raised.value is primary and raised.value.__cause__ is secondary
    if stage == "source":
        assert runtime_calls == []


@pytest.mark.parametrize("failure", ["checksum", "evaluator"])
def test_invalid_identity_never_accesses_targets(manifest, tmp_path, monkeypatch, failure):
    path = tmp_path / "run/run-plan.json"
    raw = json.loads(path.read_bytes())
    if failure == "checksum":
        raw["plan_hash"] = "0" * 64
    else:
        raw["manifest"]["evaluator_hash"] = "0" * 64
        raw["plan_hash"] = digest(raw["manifest"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(planning, "snapshot", lambda *_: pytest.fail("source accessed"))
    monkeypatch.setattr(planning, "target_environment", lambda *_: pytest.fail("target launched"))
    with pytest.raises(ValueError, match="checksum|evaluator"):
        planning.read_plan(path)


def test_identity_only_read_does_not_access_targets(manifest, tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "snapshot", lambda *_: pytest.fail("source accessed"))
    monkeypatch.setattr(planning, "target_environment", lambda *_: pytest.fail("target launched"))
    assert planning.read_plan(tmp_path / "run/run-plan.json", check_targets=False) == manifest


def test_single_candidate_keeps_sequential_checks_without_pool(manifest, tmp_path, monkeypatch):
    path = tmp_path / "run/run-plan.json"
    raw = json.loads(path.read_bytes())
    raw["manifest"]["variants"].pop("baseline")
    raw["manifest"]["planned_trials"] = [p for p in raw["manifest"]["planned_trials"] if p["variant_id"] == "candidate"]
    raw["manifest"]["execution_order"] = [p for p in raw["manifest"]["execution_order"] if p[1] == "candidate"]
    raw["plan_hash"] = digest(raw["manifest"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    source, runtime = planning.snapshot, planning.target_environment
    calls, thread = [], get_ident()

    def check_source(value):
        assert get_ident() == thread
        calls.append("source")
        return source(value)

    def check_runtime(value):
        assert get_ident() == thread
        calls.append("runtime")
        return runtime(value)

    monkeypatch.setattr(planning, "snapshot", check_source)
    monkeypatch.setattr(planning, "target_environment", check_runtime)
    monkeypatch.setattr(planning, "ThreadPoolExecutor", lambda **_: pytest.fail("unnecessary pool"), raising=False)
    assert set(planning.read_plan(path).variants) == {"candidate"}
    assert calls == ["source", "runtime"]


def test_equal_checkout_and_interpreter_still_receive_two_checks(manifest, tmp_path, monkeypatch):
    path = tmp_path / "run/run-plan.json"
    raw = json.loads(path.read_bytes())
    raw["manifest"]["variants"]["candidate"] = raw["manifest"]["variants"]["baseline"] | {
        "variant_id": "candidate"}
    raw["plan_hash"] = digest(raw["manifest"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    source, runtime = planning.snapshot, planning.target_environment
    source_calls, runtime_calls = [], []

    def check_source(value):
        source_calls.append(str(value))
        return source(value)

    def check_runtime(value):
        runtime_calls.append((value.variant_id, value.checkout, value.interpreter))
        return runtime(value)

    monkeypatch.setattr(planning, "snapshot", check_source)
    monkeypatch.setattr(planning, "target_environment", check_runtime)
    assert set(planning.read_plan(path).variants) == {"baseline", "candidate"}
    assert len(source_calls) == 2 and len(set(source_calls)) == 1
    assert {r[0] for r in runtime_calls} == {"baseline", "candidate"}
    assert len({r[1:] for r in runtime_calls}) == 1


def test_double_failure_preserves_original_primary_and_secondary_causes(manifest, tmp_path, monkeypatch):
    entered = {name: Event() for name in manifest.variants}
    causes = {"baseline": OSError("baseline original cause"), "candidate": LookupError("candidate original cause")}
    errors = {name: ValueError(name + " runtime failure") for name in manifest.variants}

    def runtime(value):
        name = value.variant_id
        entered[name].set()
        assert all(e.wait(5) for e in entered.values())
        raise errors[name] from causes[name]

    monkeypatch.setattr(planning, "target_environment", runtime)
    with pytest.raises(ValueError) as raised:
        planning.read_plan(tmp_path / "run/run-plan.json")
    assert raised.value is errors["baseline"]
    seen = set()

    def visit(error):
        if not isinstance(error, BaseException) or id(error) in seen:
            return
        seen.add(id(error))
        for child in (error.__cause__, error.__context__, *error.args):
            visit(child)

    visit(raised.value)
    assert {id(e) for e in [*errors.values(), *causes.values()]} <= seen


@pytest.mark.parametrize("stage", ["source", "runtime"])
def test_failure_does_not_cache_or_adopt_target_results(manifest, tmp_path, monkeypatch, stage):
    source, runtime = planning.snapshot, planning.target_environment
    source_calls, runtime_calls, invalid = [], [], [True]

    def check_source(value):
        source_calls.append(value.name)
        result = source(value)
        return result | {"files_hash": "0" * 64} if invalid[0] and stage == "source" and value.name == "candidate" else result

    def check_runtime(value):
        runtime_calls.append(value.variant_id)
        result = runtime(value)
        return {"changed": True} if invalid[0] and stage == "runtime" and value.variant_id == "candidate" else result

    monkeypatch.setattr(planning, "snapshot", check_source)
    monkeypatch.setattr(planning, "target_environment", check_runtime)
    with pytest.raises(ValueError, match="source|runtime"):
        planning.read_plan(tmp_path / "run/run-plan.json")
    assert len(source_calls) == 2
    assert len(runtime_calls) == (0 if stage == "source" else 2)
    invalid[0] = False
    assert planning.read_plan(tmp_path / "run/run-plan.json") == manifest
    assert len(source_calls) == 4
    assert len(runtime_calls) == (2 if stage == "source" else 4)


@pytest.mark.parametrize("mutation", ["source", "runtime"])
def test_full_target_read_does_not_replace_final_freshness(subject, tmp_path, monkeypatch, mutation):
    checkout, source = subject
    runtime = {"version": 1}
    monkeypatch.setattr(planning, "target_environment", lambda *_: deepcopy(runtime))
    monkeypatch.setattr(reads, "target_environment", lambda *_: deepcopy(runtime))
    config = tmp_path / "guard-variants.json"
    config.write_text(json.dumps([dict(variant_id="candidate", checkout=str(checkout), interpreter=sys.executable)]), encoding="utf-8")
    directory = tmp_path / "guard-plan"
    frozen = planning.create_plan(ROOT / "evals/suites/harness-core-v1.json", config, directory)
    closure = reads.DependencyClosure()
    closure.source(frozen.variants["candidate"])
    closure.freeze()
    closure.activate_sources()
    closure.check()
    assert planning.read_plan(directory / "run-plan.json") == frozen
    if mutation == "source":
        source.write_text("value = 2\n", encoding="utf-8")
    else:
        runtime["version"] = 2
    value = {"header": "synthetic-unit-only"}
    monkeypatch.setattr(release, "_read_header", lambda *_: value)
    with pytest.raises(ValueError, match="source/runtime changed"):
        release._finish_reads(tmp_path / "synthetic-preflight.json", value,
                              SimpleNamespace(check=closure.check))
