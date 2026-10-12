import json
import sqlite3
import sys

import pytest

from offerpilot.evaluation.compare import read_run
from offerpilot.evaluation.evidence.storage import bind_fragments, seal
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.graders.p0 import GRADERS
from offerpilot.evaluation.reporting import aggregate, atomic_json
from offerpilot.evaluation.runner import evaluator_hash
from tests.evaluation.test_contracts import manifest, plan
from tests.evaluation.test_commit_observer import observed_db as observed_db, bundle


@pytest.mark.parametrize("damage", ["fragment", "result_identity", "trial_identity"])
def test_read_run_revalidates_evidence_and_preserves_other_failure(observed_db, damage) -> None:
    root = observed_db.parent / "run"
    first = plan(["business.zero_committed_dml", "evidence.required_complete"])
    second = first.model_copy(update={"execution_item_id": "second"})
    runtime = {"python": "synthetic", "sqlite": "synthetic", "dependencies": {}}
    m = manifest(first).model_copy(update={"evaluator_hash": evaluator_hash(), "planned_trials": [first, second],
        "variants": {"candidate": {"commit": "a" * 40, "interpreter": sys.executable, "target_runtime": runtime}}})
    clean = bundle(observed_db)
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='bad' WHERE id=1")
    failing = bundle(observed_db)
    atomic_json(root / "run-manifest.json", m.model_dump(mode="json"))
    for p, evidence in ((first, failing), (second, clean)):
        evidence = bind_fragments(evidence.model_copy(update={"facts": {**evidence.facts,
            "handshakes": [{"checkout_sha": "a" * 40, "interpreter": sys.executable, **runtime}]}}))
        identifier = f"candidate-{p.execution_item_id}-1"
        private = root / "private" / identifier
        private.mkdir(parents=True)
        seal(private, evidence)
        atomic_json(private / ".eval-trial.json", p.model_dump(mode="json", include={
            "run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}))
        r = grade_trial(p, evidence, GRADERS)
        assert r.verdict == ("fail" if p == first else "pass")
        atomic_json(root / "trials" / identifier / "result.json", r.model_dump(mode="json"))
    private = root / "private/candidate-second-1"
    if damage == "fragment":
        (private / "fragment-calls.json").unlink()
    elif damage == "trial_identity":
        (private / ".eval-trial.json").write_text("{}", encoding="utf-8")
    else:
        path = root / "trials/candidate-second-1/result.json"
        raw = json.loads(path.read_bytes())
        raw["execution_item_id"] = "wrong-trial"
        path.write_text(json.dumps(raw), encoding="utf-8")
    manifest_read, results = read_run(root)
    summary = aggregate(manifest_read, results)
    assert summary["exit_code"] == 1 and summary["planned"] == 2
    assert summary["counts"] == {"pass": 0, "fail": 1, "incomplete": 1}
