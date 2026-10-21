"""Actual qualification forwarding plus command adoption; synthetic evidence only.

The existing refresh fixture isolates the historical process/origin exporter.
Raw guard seams here test adoption independently; path/runtime guards have their
own real filesystem tests in test_core_release_reads.py.
"""
from copy import deepcopy
import json

import pytest

from offerpilot.evaluation.core_release_reads import CommandReads, DependencyClosure
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_refresh import refresh_case as refresh_case


def activate(case):
    request = case.prepare()
    case.refresh.activate_refresh(request, case.authorize(request))


def raw_guard(case):
    closure = DependencyClosure()
    closure.tree(case.run)
    return closure.freeze()


def test_actual_qualification_capture_is_deep_copy_and_reads_once(refresh_case, monkeypatch):
    case = refresh_case
    activate(case)
    original_reader = case.refresh.read_run
    calls = []
    def counted(*args):
        calls.append(args)
        return original_reader(*args)
    monkeypatch.setattr(case.refresh, "read_run", counted)
    original = deepcopy(case.manifest.model_dump(mode="json"))
    count = len(case.state["rows"])
    captured = []
    def observe(directory, parsed, rows):
        captured.append((directory, len(rows)))
        parsed.variants["candidate"]["commit"] = "0" * 40
        rows.clear()
    value = case.refresh.resolve_baseline(case.store, "original", _capture=observe)
    assert len(calls) == 1 and captured == [(case.run, count)]
    assert case.manifest.model_dump(mode="json") == original
    assert len(case.state["rows"]) == count
    assert value["manifest_hash"] == value["sources"][0]["manifest_hash"]


def test_command_adopts_only_fully_qualified_models_and_cold_reads_next_command(refresh_case, monkeypatch):
    case = refresh_case
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    original_reader = case.refresh.read_run
    calls = []
    def counted(*args):
        calls.append(args)
        return original_reader(*args)
    monkeypatch.setattr(case.refresh, "read_run", counted)
    for _ in range(2):
        command = CommandReads()
        command.baseline(case.store, "original", lambda store, name: case.refresh.resolve_baseline(
            store, name, _capture=command.collect_qualified_run))
        parsed, rows = command.run(case.run, lambda *_: pytest.fail("strict read already qualified this run"))
        assert len(rows) == 114
        rows.clear()
        parsed.variants["candidate"]["commit"] = "0" * 40
        parsed_again, rows_again = command.run(case.run, lambda *_: pytest.fail("cache unexpectedly empty"))
        assert len(rows_again) == 114 and parsed_again == case.manifest
        command.check()
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["qualification_binding", "final_cas", "post_guard"])
def test_capture_after_later_failure_is_never_registered(refresh_case, monkeypatch, phase):
    case = refresh_case
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    monkeypatch.setattr(DependencyClosure, "for_run", lambda *_: raw_guard(case))
    if phase == "qualification_binding":
        original = case.refresh.qualification
        def invalid(*args, **kwargs):
            return original(*args, **kwargs) | {"activatable": False}
        monkeypatch.setattr(case.refresh, "qualification", invalid)
    elif phase == "final_cas":
        original = case.refresh.strict_state
        calls = []
        def changed(*args):
            state = original(*args)
            calls.append(state)
            return state if len(calls) == 1 else (state[0], state[1], {})
        monkeypatch.setattr(case.refresh, "strict_state", changed)
    command = CommandReads()
    collected = []
    def collect(*values):
        collected.append(values[0])
        command.collect_qualified_run(*values)
    summary = case.run / "summary.json"
    original_summary = summary.read_bytes()
    def strict(store, name):
        value = case.refresh.resolve_baseline(store, name, _capture=collect)
        if phase == "post_guard":
            summary.write_bytes(b"changed after original strict success")
        return value
    with pytest.raises(ValueError):
        command.baseline(case.store, "original", strict)
    assert collected == [case.run]
    assert command._pending_runs == {} and command._runs == {} and command._baselines == {}
    summary.write_bytes(original_summary)
    fallback = []
    command.run(case.run, lambda *_: fallback.append("full reader") or (case.manifest, case.state["rows"]))
    assert fallback == ["full reader"]


def test_cached_qualified_run_still_rejects_changed_bytes(refresh_case, monkeypatch):
    case = refresh_case
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    command = CommandReads()
    command.baseline(case.store, "original", lambda store, name: case.refresh.resolve_baseline(
        store, name, _capture=command.collect_qualified_run))
    (case.run / "summary.json").write_bytes(b"corrupt after qualification")
    with pytest.raises(ValueError, match="changed"):
        command.run(case.run, lambda *_: pytest.fail("must reject cached evidence mutation"))


def test_readonly_batch_returns_same_qualified_models_without_second_read(refresh_case, monkeypatch):
    case = refresh_case
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    original_reader = case.refresh.read_run
    count = []
    def strict(*args):
        count.append(args)
        return original_reader(*args)
    monkeypatch.setattr(case.refresh, "read_run", strict)
    for _ in range(2):
        command = CommandReads()
        record, checked = command.baseline_and_runs(case.store, "original",
            lambda store, name: case.refresh.resolve_baseline(store, name, _capture=command.collect_qualified_run),
            [case.run, case.run], lambda *_: pytest.fail("qualified rows were already strictly read"))
        assert record["source_variant"] == "baseline" and set(checked) == {case.run}
        assert checked[case.run][0] == case.manifest and len(checked[case.run][1]) == 114
        checked[case.run][1].clear()
        assert len(command.run(case.run, lambda *_: pytest.fail("missing adopted data"))[1]) == 114
        command.check()
    assert len(count) == 2


@pytest.mark.parametrize("failure", ["extra_run", "union"])
def test_readonly_batch_never_adopts_partial_success(refresh_case, monkeypatch, failure):
    case = refresh_case
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    monkeypatch.setattr(DependencyClosure, "for_run", lambda *_: raw_guard(case))
    command = CommandReads()
    summary = case.run / "summary.json"
    before = summary.read_bytes()
    def baseline(store, name):
        record = case.refresh.resolve_baseline(store, name, _capture=command.collect_qualified_run)
        if failure == "union":
            summary.write_bytes(b"changed before joint postguard")
        return record
    def rejected(*_):
        raise ValueError("additional strict run rejected")
    with pytest.raises(ValueError):
        command.baseline_and_runs(case.store, "original", baseline,
            [case.run] if failure == "union" else [case.run.parent / "extra"], rejected)
    assert command._pending_runs == {} and command._runs == {} and command._baselines == {}
    summary.write_bytes(before)


@pytest.mark.parametrize("failure", [None, "qualification_binding", "final_cas", "post_guard"])
def test_scoped_origins_flow_through_full_114_qualification_and_close(refresh_case, monkeypatch, failure):
    """Actual qualification/resolve/CAS with synthetic read/history/origin seams.

    Real sealed load and source rules are independently exercised in
    test_core_origin_reads; this fixture proves the full 114 forwarding path.
    """
    case = refresh_case
    (case.run / "trial-results.json").write_text(json.dumps([
        row.model_dump(mode="json") for row in case.state["rows"]]), encoding="utf-8")
    activate(case)
    monkeypatch.setattr(DependencyClosure, "for_baseline", lambda *_: raw_guard(case))
    original_reader = case.refresh.read_run
    calls, scopes = [], []
    def observed(directory, *, _capture_origin=None):
        calls.append(directory)
        parsed, result = original_reader(directory)
        assert _capture_origin is not None
        for row in result:
            _capture_origin(directory, parsed, row, dict(errors={}, facts=dict(handshakes=[{"trial": list(row.identity())}])))
        return parsed, result
    monkeypatch.setattr(case.refresh, "read_run", observed)
    def origins(parsed, result, directory, *, _origin=None):
        assert _origin is not None
        scopes.append(_origin)
        assert len(result) == 114
        assert all(_origin.evidence(directory, parsed, row)["facts"]["handshakes"] == [{"trial": list(row.identity())}] for row in result)
        return dict(synthetic_origin_test=True)
    monkeypatch.setattr(case.refresh, "source_origins", origins)
    if failure == "qualification_binding":
        original = case.refresh.qualification
        def invalid(*args, **kwargs):
            return original(*args, **kwargs) | {"activatable": False}
        monkeypatch.setattr(case.refresh, "qualification", invalid)
    elif failure == "final_cas":
        original_state = case.refresh.strict_state
        states = []
        def changed(*args):
            value = original_state(*args)
            states.append(value)
            return value if len(states) == 1 else (value[0], value[1], {})
        monkeypatch.setattr(case.refresh, "strict_state", changed)
    for _ in range(2 if failure is None else 1):
        command = CommandReads()
        def strict(store, name):
            value = case.refresh.resolve_baseline(store, name,
                _capture=command.collect_qualified_run, _origin_scope=command.origin_scope)
            if failure == "post_guard":
                (case.run / "summary.json").write_bytes(b"changed after scoped qualification")
            return value
        if failure is None:
            _, checked = command.baseline_and_runs(case.store, "original", strict,
                [case.run], lambda *_: pytest.fail("duplicate strict run read"))
            assert len(checked[case.run][1]) == 114
            command.check()
        else:
            with pytest.raises(ValueError):
                command.baseline_and_runs(case.store, "original", strict,
                    [case.run], lambda *_: pytest.fail("no partial adoption"))
            assert command._runs == {} and command._baselines == {}
        assert command._pending_runs == {} and command._origin_scopes == []
        assert command._origin_baseline is None and all(scope.closed for scope in scopes)
    assert len(calls) == (2 if failure is None else 1)
