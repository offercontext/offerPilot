"""New output must never alias the immutable original store, product or raw run."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess

import pytest

from offerpilot.evaluation.runner import ROOT
from offerpilot.evaluation import core_refresh_history as history
from offerpilot.evaluation import core_refresh_sources as sources
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_refresh import refresh_case as refresh_case
from tests.evaluation.test_core_refresh import bytes_at
from tests.evaluation.test_core_refresh_history import setup_history
from tests.evaluation.test_core_refresh_sources import origins as origins


@contextmanager
def directory_alias(link, target):
    assert link.absolute().parent == target.absolute().parent
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield link
    finally:
        # Only remove this fixture's alias itself, never its target or any tree.
        assert link.absolute().parent == target.absolute().parent
        link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.parametrize(
    "location", ["store", "checkout", "raw", "traversal", "store_junction", "checkout_junction"]
)
def test_prepare_refuses_readonly_output_alias_before_creating_or_executing(
    refresh_case, tmp_path, monkeypatch, location
):
    c = refresh_case
    checkout = Path(c.manifest.variants["baseline"]["checkout"])
    checkout.mkdir()
    (checkout / "unchanged.txt").write_text("immutable test product")
    (tmp_path / "outside").mkdir()
    before, product_before = bytes_at(c.store), bytes_at(checkout)
    monkeypatch.setattr(
        c.refresh, "audit_history", lambda *_: pytest.fail("must reject before executing reader")
    )

    def attempt(output):
        with pytest.raises(ValueError):
            c.refresh.prepare_refresh(
                c.store,
                "original",
                "g1",
                c.run,
                ROOT / "evals/policies/core-initial-baseline-v5.json",
                c.review,
                tmp_path / "experiment.json",
                tmp_path / "history.json",
                output,
            )
        assert not output.exists()

    if location.endswith("junction"):
        target = c.store if location == "store_junction" else checkout
        with directory_alias(tmp_path / "alias", target) as alias:
            attempt(alias / "attempt")
    else:
        output = {
            "store": c.store / "attempt",
            "checkout": checkout / "attempt",
            "raw": tmp_path / "old-run/attempt",
            "traversal": tmp_path / "outside/../store/attempt",
        }[location]
        attempt(output)
    assert bytes_at(c.store) == before and bytes_at(checkout) == product_before


@pytest.mark.parametrize("location", ["store", "checkout", "traversal", "junction"])
def test_history_receipt_boundary_is_checked_before_native_child(tmp_path, monkeypatch, location):
    store, record, descriptor = setup_history(tmp_path, monkeypatch)
    checkout = tmp_path / "old-checkout"
    before = bytes_at(store), bytes_at(checkout)

    def prohibit_native():
        monkeypatch.setattr(
            history.subprocess,
            "run",
            lambda *_a, **_kw: pytest.fail("native reader must not start"),
        )

    if location == "junction":
        with directory_alias(tmp_path / "alias", store) as alias:
            prohibit_native()
            with pytest.raises(ValueError):
                history.audit_history(store, record, descriptor, alias / "attempt")
    else:
        prohibit_native()
        (tmp_path / "outside").mkdir()
        output = {
            "store": store / "attempt",
            "checkout": checkout / "attempt",
            "traversal": tmp_path / "outside/../store/attempt",
        }[location]
        with pytest.raises(ValueError):
            history.audit_history(store, record, descriptor, output)
        assert not output.exists()
    assert (bytes_at(store), bytes_at(checkout)) == before


@pytest.mark.parametrize("location", ["baseline", "traversal", "junction"])
def test_source_archive_refuses_baseline_or_linked_destination(origins, tmp_path, location):
    m, _, _, _, _ = origins
    checkout = Path(m.variants["baseline"]["checkout"])
    before = bytes_at(checkout)
    if location == "junction":
        with directory_alias(tmp_path / "alias", checkout) as alias:
            with pytest.raises(ValueError):
                sources.freeze_sources(m, alias / "new.json")
    else:
        (tmp_path / "outside").mkdir()
        output = (
            checkout / "new.json"
            if location == "baseline"
            else tmp_path / "outside/../baseline/new.json"
        )
        with pytest.raises(ValueError):
            sources.freeze_sources(m, output)
        assert not output.exists()
    assert bytes_at(checkout) == before
