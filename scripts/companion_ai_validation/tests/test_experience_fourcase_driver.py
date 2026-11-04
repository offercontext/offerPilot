"""Actual managed HTTP path with an exclusively loopback fake provider."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import pytest

from scripts.companion_ai_validation import driver as base
from scripts.companion_ai_validation import experience_fourcase_driver as fourcase
from scripts.companion_ai_validation.tests import test_experience_driver as legacy_test


def test_fourcase_exact_budget_switch_history_and_actual_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy_test, "experience", fourcase)
    fake = legacy_test.LoopbackFake()
    recorded: list[dict[str, Any]] = []
    try:
        report = fourcase.run_matrix(tmp_path, fake.factory, record_response=recorded.append)
    finally:
        fake.close()
    assert report["passed"] and report["ordinary_count"] == 4
    assert report["pass_scope"] == "mechanism_only" and report["web_executed"] is False
    assert fake.operations == list(fourcase.OPERATIONS)
    assert len(fake.requests) == len(recorded) == 4
    assert [row["user_input"] for row in recorded] == list(fourcase.TEXTS)
    assert all(row["provenance"]["runtime_budget"] == {
        "model_calls": 1, "title_model_calls": 0, "tool_calls": 0,
    } for row in recorded)
    assert all(row["ordinary_turn_read_only"] and row["source_verified"] for row in recorded)
    cids = [row["provenance"]["conversation_id"] for row in recorded]
    assert cids[0] == cids[1] == cids[2] != cids[3]
    assert [row["expected_context"] is not None for row in recorded] == [False, True, False, False]
    assert report["groups"][0]["controls"] == []
    assert report["groups"][0]["responses"] == recorded
    assert all(report["groups"][0]["assertions"].values())
    for index, payload in enumerate(fake.requests):
        messages = payload["messages"]
        text = json.dumps(messages, ensure_ascii=False)
        assert ("persona_profile" in text) is (index == 1)
        for distinctive in ("晴岚", "星空书屋", "天文"):
            assert (distinctive in text) is (index == 1)
        assert messages[-1]["content"] == fourcase.TEXTS[index]
        assistants = [row for row in messages if row["role"] == "assistant"]
        assert len(assistants) == (0, 1, 2, 0)[index]
    assert all(row["provenance"]["persona_binding"]["revision"] == 1 for row in recorded)


@pytest.mark.parametrize("symlink", [False, True])
def test_unowned_root_rejected_before_provider(tmp_path: Path, symlink: bool) -> None:
    root = tmp_path / "root"
    root.mkdir()
    if symlink:
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    else:
        (root / "keep").write_text("untouched")

    def forbidden(_: Path) -> NoReturn:
        pytest.fail("provider factory must not run")

    with pytest.raises(base.MatrixFailure, match="fresh_root_required"):
        fourcase.run_matrix(root, forbidden)


def test_recording_failure_stops_without_remaining_paid_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy_test, "experience", fourcase)
    fake = legacy_test.LoopbackFake()

    def fail(_: dict[str, Any]) -> NoReturn:
        raise RuntimeError("fixture writer failure")

    try:
        with pytest.raises(base.MatrixFailure, match="a_default_off: response_record_failed"):
            fourcase.run_matrix(tmp_path, fake.factory, record_response=fail)
    finally:
        fake.close()
    assert fake.operations == [fourcase.OPERATIONS[0]]


def test_exact_finite_cases_and_setup_open_no_model_stage(tmp_path: Path) -> None:
    import time
    from uuid import uuid4
    from sqlalchemy import select
    from offerpilot.models import ChatMessage, PilotTurnMessage
    from scripts.companion_ai_validation import experience_driver as experience

    fake = legacy_test.LoopbackFake()
    (tmp_path / base.CLOCK_FILE).write_text(str(base.CLOCK_START))

    def no_stage(_: str) -> NoReturn:
        pytest.fail("finite/setup turn must not open a model stage")

    try:
        with base._api(tmp_path) as client:
            matrix = experience._Experience(tmp_path, client, no_stage, (), None)
            try:
                matrix.opt_in_retest()
                matrix.event("app_open")
                activity = matrix.view()["state"]["activity"]
                assert activity["template_id"] == "paper_boat" and activity["step_index"] == 0

                def finite(message: str) -> str:
                    before = matrix.snapshot(matrix.view())
                    admitted = base._request(client, "finite", "POST", "/api/pilot/runtime/v1/turns", {
                        "request_id": str(uuid4()), "conversation_id": 0, "message": message,
                        "context_type": "workspace", "mode": "general",
                    }, status=202)
                    deadline = time.monotonic() + base.PROCESS_TIMEOUT
                    while True:
                        state = base._request(client, "finite", "GET",
                                              f"/api/pilot/runtime/v1/turns/{admitted['turn_id']}")
                        if state["state"] in {"completed", "failed", "interrupted", "result_unknown",
                                              "waiting_confirmation", "waiting_clarification"}:
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert state["state"] == "completed"
                    assert {key: state["budget"][key] for key in (
                        "model_calls", "title_model_calls", "tool_calls",
                    )} == {"model_calls": 0, "title_model_calls": 0, "tool_calls": 0}
                    assert matrix.snapshot(matrix.view()) == before
                    with matrix.sessions() as session:
                        answer = session.scalar(select(ChatMessage.content).join(
                            PilotTurnMessage, PilotTurnMessage.message_id == ChatMessage.id,
                        ).where(PilotTurnMessage.turn_id == admitted["turn_id"],
                                ChatMessage.role == "assistant"))
                    assert answer
                    matrix.business_empty("finite")
                    return answer

                x03 = finite("正事先到这儿。你刚才说的小日常呢？")
                assert "哪一项" in x03 and "不能确定" in x03 and "第二折" not in x03
                x09 = finite("小周和小陈给他们的纸船取名“橘子号”。我只是转述。我们的船也叫这个吗？")
                assert "无法确认" in x09
                assert all(text not in x09 for text in ("还没", "没有起", "刚挑", "橘子号"))
                cid = matrix.control("等我回来一起起个名字", "wait")
                matrix.control("叫棉花糖号吧", "answer", cid)
                query = "还记得我们的小船叫什么吗？"
                assert "棉花糖号" in finite(query)
                base._request(client, "delete_source", "DELETE", f"/api/chat/conversations/{cid}")
                x11 = finite(query)
                assert "无法确认" in x11 and "棉花糖号" not in x11
                assert all(row["runtime_budget"]["model_calls"] == 0 for row in matrix.controls)
            finally:
                matrix.sessions.kw["bind"].dispose()
    finally:
        fake.close()
    assert fake.requests == []
    assert fake.operations == []
