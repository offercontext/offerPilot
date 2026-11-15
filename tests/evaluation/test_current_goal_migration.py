"""Version migration and synthetic flow checks, not real-model acceptance."""

import json
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from offerpilot.evaluation.core_contracts import CaseSpecV2, SuiteV2
from offerpilot.evaluation.core_graders import grade_rule
from offerpilot.evaluation.core_planning import load_core_suite
from offerpilot.evaluation.core_registry import case_definition, required_rules
from offerpilot.evaluation.runner import ROOT


def test_old_case_and_suite_bytes_still_load_with_original_rules():
    old = CaseSpecV2.model_validate_json(
        (ROOT / "evals/cases/E09.memory_override.v2.json").read_bytes()
    )
    assert old.model_dump(mode="json", exclude_none=True) == case_definition(
        "E09.memory_override"
    )
    assert "source.used" in required_rules(old.execution_item_id, "real", old.case_version)
    suite, cases = load_core_suite(ROOT / "evals/suites/harness-core-v1.json")
    assert len(suite.execution_items) == 19
    assert cases[old.execution_item_id].case_version == 2


def test_new_suite_keeps_denominators_and_changes_only_override_case():
    suite, cases = load_core_suite(ROOT / "evals/suites/harness-core-business-v1.json")
    _, old = load_core_suite(ROOT / "evals/suites/harness-core-v1.json")
    assert len(cases) == 19
    assert sum("real" in c.supported_modes for c in cases.values()) == 13
    for item, case in cases.items():
        if item != "E09.memory_override":
            assert case == old[item]
    case = cases["E09.memory_override"]
    assert case.case_version == 3
    assert case.fixture_id == "synthetic-e09-current-goal-v1"
    assert "diagnostic.scripted_current_goal" in case.required_rules
    real = required_rules(case.execution_item_id, "real", case.case_version)
    assert "quality.current_goal" in real and "source.used" not in real
    assert "diagnostic.scripted_current_goal" not in real
    assert suite.suite_id == "harness-core-business-v1"


@pytest.mark.parametrize("old_suite", [True, False])
def test_suite_cannot_swap_case_versions_to_bypass_registered_rules(old_suite):
    name = "harness-core-v1" if old_suite else "harness-core-business-v1"
    data = json.loads((ROOT / f"evals/suites/{name}.json").read_bytes())
    item = next(i for i in data["execution_items"] if i["execution_item_id"] == "E09.memory_override")
    item["spec_path"] = f"cases/E09.memory_override.v{3 if old_suite else 2}.json"
    with pytest.raises(ValidationError, match="suite case version"):
        SuiteV2.model_validate(data)


@pytest.mark.parametrize("answer", ["无关文本。\n无关文本。", "只谈 CSS 页面布局。", "相关内容\n\n相关内容"])
def test_free_text_cannot_automatically_establish_real_current_goal_following(answer):
    evidence = dict(
        trial_key=dict(mode="real"),
        facts=dict(answer=answer, terminal_state="completed"),
    )
    assert grade_rule("quality.current_goal", "E09.memory_override", evidence)[0] is None
    assert grade_rule("diagnostic.scripted_current_goal", "E09.memory_override", evidence)[0] is None


def test_fixed_script_diagnostic_checks_transport_bytes_not_line_count():
    fixture = json.loads(
        (ROOT / "evals/fixtures/synthetic-e09-current-goal-v1.json").read_bytes()
    )
    evidence = dict(
        trial_key=dict(mode="deterministic"),
        facts=dict(answer=fixture["scripted_answer"], terminal_state="completed"),
    )
    result, reason = grade_rule("diagnostic.scripted_current_goal", "E09.memory_override", evidence)
    assert result is True and reason == "fixed_script_transport_only"
    evidence["facts"]["answer"] = "任意两行。\n不能证明目标。"
    assert grade_rule("diagnostic.scripted_current_goal", "E09.memory_override", evidence)[0] is False


def test_new_seed_and_script_use_business_goal_conflict_without_changing_withdrawal(tmp_path):
    from scripts.evaluation_subject.core_fixtures import prepare
    from scripts.evaluation_subject.core_bootstrap import ScriptModel
    from offerpilot.confirmed_memory.repository import ConfirmedMemoryRepository
    from offerpilot.db import init_database

    data = tmp_path / "new"
    data.mkdir()
    aliases = prepare(
        data, ROOT, "E09.memory_override", fixture_id="synthetic-e09-current-goal-v1"
    )
    repo = ConfirmedMemoryRepository(init_database(data / "data.db"))
    # Repository source/version is observed independently from the model answer.
    assert isinstance(aliases["memory_id"], str) and aliases["memory_version"] > 0
    with repo.sessions() as session:
        from sqlalchemy import text
        memory = session.execute(text("SELECT content FROM confirmed_memory_versions")).scalar()
    assert "CSS" in memory
    channel = SimpleNamespace(
        config=dict(epoch=1, trial_key=dict(execution_item_id="E09.memory_override"),
                    fixture_id="synthetic-e09-current-goal-v1"),
        attempt=lambda *_args: None,
    )
    answer = ScriptModel(channel).complete([], []).content
    assert "公开 API" in answer and "分层排查" in answer
    channel.config["fixture_id"] = "synthetic-core-v1"
    assert ScriptModel(channel).complete([], []).content == "已按本次要求调整。\n只保留两个短句。"


def _wire_evidence(tmp_path, mutation):
    from offerpilot.context_projector.contracts import canonical_json

    fixture = json.loads(
        (ROOT / "evals/fixtures/synthetic-e09-current-goal-v1.json").read_bytes()
    )
    unit = dict(id="memory-1", version=1, preference=fixture["memory"])
    selected, wire = deepcopy(unit), deepcopy(unit)
    for field, replacement in [
        ("id", "other-memory"), ("version", 2), ("preference", "替换过的偏好")
    ]:
        if mutation == f"selected_{field}":
            selected[field] = replacement
        if mutation == f"wire_{field}":
            wire[field] = replacement
    content = canonical_json(wire).decode()
    needle = wire["preference"].encode()
    start = content.encode().find(needle)
    current = fixture["current_request"] if mutation != "missing_current_request" else "其他请求"
    raw = json.dumps(dict(messages=[
        dict(role="user", content=content), dict(role="user", content=current)
    ]), ensure_ascii=False).encode()
    request_file = tmp_path / "request.json"
    request_file.write_bytes(raw)
    execution = dict(request_id="r1", turn_id="t1", generation=1, conversation_id=1)
    identity = dict(model_call_id="call-1", segment_id="s1", surface_fingerprint="surface-1")
    selection = dict(epoch=1, seq=1, category="sources", identity=identity,
                     sources=[dict(kind="confirmed_memory", source_id="memory", revision="rev1",
                                   content=json.dumps([selected], ensure_ascii=False))])
    final = dict(
        epoch=1, seq=2, category="final_input",
        identity={**identity, "execution": execution},
        request_file=request_file.name, payload_hash=sha256(raw).hexdigest(),
        observations=[dict(
            kind="confirmed_memory", source_id="memory", revision="rev1",
            field_path="messages[0].content",
            ranges=[dict(
                start=start, end=start+len(needle), content_hash=sha256(needle).hexdigest(),
                source_content_hash=sha256(needle).hexdigest(),
                unit_identity=dict(id=wire["id"], version=wire["version"]),
                unit_hash=sha256(canonical_json(wire)).hexdigest(),
                unit_binding="complete-json-unit",
            )],
        )],
    )
    if mutation == "wrong_call":
        final["identity"]["model_call_id"] = "different-call"
    if mutation == "wrong_execution":
        final["identity"]["execution"] = {**execution, "request_id": "different-request"}
    if mutation == "content_only":
        final["observations"][0]["ranges"][0]["unit_binding"] = "content-only"
    if mutation == "bad_range":
        final["observations"][0]["ranges"][0]["start"] = 0
    if mutation == "missing_file":
        request_file.unlink()
    if mutation == "wrong_hash":
        final["payload_hash"] = "0"*64
    return dict(
        facts=dict(aliases=dict(memory_id="memory-1", memory_version=1)),
        http=[dict(method="POST", path="/api/pilot/runtime/v1/turns",
                   body=dict(request_id="r1", message=fixture["current_request"]),
                   response=dict(turn_id="t1", execution_generation=1, conversation_id=1))],
        input_observations=[selection] if mutation == "missing_final" else [selection, final],
        files={request_file.name: dict(sha256=sha256(raw).hexdigest())},
    )


@pytest.mark.parametrize("mutation,expected", [
    ("complete", True), ("selected_id", False), ("selected_version", False),
    ("selected_preference", False), ("wire_id", False), ("wire_version", False),
    ("wire_preference", False), ("missing_current_request", False),
    ("wrong_call", None), ("wrong_execution", None), ("missing_final", None),
    ("missing_file", None), ("wrong_hash", None), ("content_only", False),
    ("bad_range", False),
])
def test_new_wire_prerequisite_rejects_wrong_stimulus_and_preserves_missing_evidence(
    tmp_path, mutation, expected
):
    evidence = _wire_evidence(tmp_path, mutation)
    result, _reason = grade_rule(
        "source.current_goal_final_input", "E09.memory_override", evidence,
        private_directory=str(tmp_path),
    )
    assert result is expected


def test_wrong_selected_memory_cannot_pass_new_selection_rule(tmp_path):
    evidence = _wire_evidence(tmp_path, "selected_id")
    assert grade_rule("source.current_goal_selected", "E09.memory_override", evidence)[0] is False
