import json

import pytest

from offerpilot.evaluation import core_planning as planning
from offerpilot.evaluation.runner import ROOT


def test_frozen_interleaved_repeats_keep_full_denominator_and_mode_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planning,
        "snapshot",
        lambda path: {
            "commit": "a" * 40,
            "diff_hash": "b" * 64,
            "lock_hash": "c" * 64,
            "files_hash": "d" * 64,
        },
    )
    monkeypatch.setattr(
        planning,
        "target_environment",
        lambda variant: {"python": "test", "sqlite": "test", "dependencies": {}},
    )
    config = tmp_path / "variants.json"
    config.write_text(
        json.dumps(
            [
                {"variant_id": name, "checkout": str(tmp_path / name), "interpreter": "unused"}
                for name in ("baseline", "candidate")
            ]
        )
    )
    manifest = planning.create_plan(
        ROOT / "evals/suites/harness-core-v1.json", config, tmp_path / "out", repeat=3
    )
    assert len(manifest.planned_trials) == 114
    assert [
        (p.repeat_index, p.execution_item_id, p.variant_id) for p in manifest.planned_trials[:4]
    ] == [
        (1, "E01.runtime_read", "baseline"),
        (1, "E01.runtime_read", "candidate"),
        (1, "E02.runtime_clarify", "baseline"),
        (1, "E02.runtime_clarify", "candidate"),
    ]
    loaded = planning.read_plan(tmp_path / "out/run-plan.json")
    assert loaded == manifest
    with pytest.raises(ValueError, match="already exists"):
        planning.create_plan(ROOT / "evals/suites/harness-core-v1.json", config, tmp_path / "out")
    raw = json.loads((tmp_path / "out/run-plan.json").read_bytes())
    raw["manifest"]["planned_trials"].pop()
    (tmp_path / "out/run-plan.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        planning.read_plan(tmp_path / "out/run-plan.json")


def test_real_is_never_enabled_by_grant_omission(tmp_path):
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="explicit user grant"):
        planning.validate_grant(SimpleNamespace(mode="real"), None)
