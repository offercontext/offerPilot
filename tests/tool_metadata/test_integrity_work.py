"""Bound repeated seal work without caching or weakening mutation detection."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from offerpilot.ai.tool_runtime import metadata as module
from offerpilot.ai.tool_runtime.contracts import ProviderToolContract
from tests.tool_metadata.factories import synthetic_provider_contract
from tests.tool_metadata.test_bundle import _bundle


def test_one_provider_snapshot_check_does_not_repeat_its_recursive_seal(monkeypatch):
    contract = synthetic_provider_contract()
    expected = module._bundle_view_integrity_snapshot(contract)
    calls = []
    original = ProviderToolContract._ensure_provider_integrity

    def counted(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(ProviderToolContract, "_ensure_provider_integrity", counted)
    assert module._bundle_view_matches_integrity_snapshot(contract, expected)
    assert calls == [contract]


def test_direct_view_read_checks_each_registered_view_once_and_still_rejects_other_view_drift(
    monkeypatch,
):
    bundle, _, _ = _bundle()
    provider = bundle.provider_view()
    discovery = bundle.discovery_view()
    token = object.__getattribute__(provider, "bundle_instance_token")
    issued = object.__getattribute__(token, "_issued")
    roots = {id(value) for value, snapshot in issued.values()}
    visits = []
    original = module._bundle_view_matches_integrity_snapshot

    def counted(value, expected):
        if id(value) in roots:
            visits.append(id(value))
        return original(value, expected)

    monkeypatch.setattr(module, "_bundle_view_matches_integrity_snapshot", counted)
    assert provider.ordered_contracts
    assert Counter(visits) == Counter(roots)
    entries = object.__getattribute__(discovery, "ordered_entries")
    replacement = tuple(list(entries))
    assert replacement == entries and replacement is not entries
    object.__setattr__(discovery, "ordered_entries", replacement)
    # A prior successful read cannot authorize another read after any view drifts.
    with pytest.raises(ValueError, match="integrity|drift"):
        provider.ordered_contracts


def test_successful_provider_check_does_not_cache_equal_nested_replacement():
    contract = synthetic_provider_contract()
    expected = module._bundle_view_integrity_snapshot(contract)
    assert module._bundle_view_matches_integrity_snapshot(contract, expected)
    parameters = contract.parameters
    entries = object.__getattribute__(parameters, "_entries")
    replacement = tuple(list(entries))
    assert replacement == entries and replacement is not entries
    object.__setattr__(parameters, "_entries", replacement)
    with pytest.raises(ValueError, match="integrity|drift"):
        module._bundle_view_matches_integrity_snapshot(contract, expected)


def test_target_view_drift_during_other_view_scan_is_still_rejected(tmp_path, monkeypatch):
    bundle, _, _ = _bundle()
    provider = bundle.provider_view()
    token = object.__getattribute__(provider, "bundle_instance_token")
    issued = object.__getattribute__(token, "_issued")
    values = [view for view, snapshot in issued.values()]
    assert values[0] is provider and values[-1] is not provider
    paused, resume = threading.Event(), threading.Event()
    target_checked = threading.Event()
    original = module._bundle_view_matches_integrity_snapshot

    def barrier(value, expected):
        if value is values[-1]:
            paused.set()
            assert resume.wait(3)
        result = original(value, expected)
        if value is provider:
            target_checked.set()
        return result

    monkeypatch.setattr(module, "_bundle_view_matches_integrity_snapshot", barrier)

    def read():
        try:
            provider.ordered_contracts
            return None
        except ValueError:
            return "ValueError"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert paused.wait(3)
            checked_before_pause = target_checked.is_set()
            contracts = object.__getattribute__(provider, "ordered_contracts")
            replacement = tuple(list(contracts))
            assert replacement == contracts and replacement is not contracts
            object.__setattr__(provider, "ordered_contracts", replacement)
        finally:
            resume.set()
        outcome = future.result(timeout=3)
    (tmp_path / "scan-mutation-observation.json").write_text(json.dumps(dict(
        target_checked_before_pause=checked_before_pause, outcome=outcome,
    )))
    assert outcome == "ValueError"


@pytest.mark.parametrize("operation", ["capture", "match"])
def test_provider_drift_during_snapshot_field_reads_is_still_rejected(
    tmp_path, monkeypatch, operation,
):
    contract = synthetic_provider_contract()
    expected = module._bundle_view_integrity_snapshot(contract)
    payload = contract.payload
    paused, resume = threading.Event(), threading.Event()

    class ReadProbe:
        @staticmethod
        def __getattribute__(value, name):
            if value is contract and name == "_parameters_snapshot":
                paused.set()
                assert resume.wait(3)
            return object.__getattribute__(value, name)

    # Only the metadata reader is instrumented. The Provider seal implementation
    # keeps its real object access and complete recursive content/identity checks.
    monkeypatch.setattr(module, "object", ReadProbe, raising=False)

    def read():
        try:
            if operation == "capture":
                module._bundle_view_integrity_snapshot(contract)
            else:
                assert module._bundle_view_matches_integrity_snapshot(contract, expected)
            return None
        except ValueError:
            return "ValueError"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert paused.wait(3)
            entries = object.__getattribute__(payload, "_entries")
            replacement = tuple(list(entries))
            assert replacement == entries and replacement is not entries
            object.__setattr__(payload, "_entries", replacement)
        finally:
            resume.set()
        outcome = future.result(timeout=3)
    (tmp_path / "provider-last-check-observation.json").write_text(json.dumps(dict(
        operation=operation, outcome=outcome,
    )))
    assert outcome == "ValueError"
