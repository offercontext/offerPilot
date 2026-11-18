from uuid import uuid4

import pytest

from offerpilot.context_sources.models import ContextContributorSettings
from offerpilot.context_sources.contracts import ContextPolicies, ContributorPolicy
from offerpilot.context_sources.loader import load_optional_sources
from offerpilot.confirmed_memory.repository import ConfirmedMemoryRepository, MemoryMutation
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.db import init_database
from offerpilot.models import Conversation


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "context.db"
    sessions = init_database(path)
    with sessions() as session:
        conversation = Conversation(title="偏好测试")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    loader = ContextSourceLoader(path)
    yield sessions, loader, conversation_id
    loader._pool.close()


def save(repository, version=0, *, memory_id=None, action="confirm", content="简洁中文，先给结论"):
    return repository.mutate(memory_id, MemoryMutation(mutation_id=uuid4(), action=action,
        expected_version=version, confirmed=True, content=content))


def test_next_projection_observes_edit_withdraw_and_independent_switch(context):
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    item = save(repository)
    first = load_optional_sources(loader, conversation_id, "中文英文")
    by_name = {value.name: value for value in first.contributors}
    assert by_name["confirmed_memory"].status == "ready"
    assert all(by_name[name].status == "disabled" for name in ("confirmed_readiness", "knowledge_context", "older_conversation_summary"))
    save(repository, 1, memory_id=item["id"], content="改为英文")
    second = load_optional_sources(loader, conversation_id, "中文英文")
    assert second.sources[0].content_revision_fingerprint != first.sources[0].content_revision_fingerprint
    assert "改为英文" in second.contributors[1].messages[0].content
    save(repository, 2, memory_id=item["id"], action="withdraw", content="")
    assert load_optional_sources(loader, conversation_id, "中文英文").contributors[1].status == "not_applicable"
    save(repository, 3, memory_id=item["id"], content="新确认")
    with sessions() as session:
        session.add(ContextContributorSettings(id=1, revision=1, settings_json=ContextPolicies(
            confirmed_memory=ContributorPolicy(enabled=False)).model_dump_json()))
        session.commit()
    assert load_optional_sources(loader, conversation_id, "中文英文").contributors[1].status == "disabled"


def test_archived_conversation_fails_closed(context):
    from datetime import datetime, timezone
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions))
    with sessions() as session:
        session.get(Conversation, conversation_id).archived_at = datetime.now(timezone.utc)
        session.commit()
    with pytest.raises(ProjectionError):
        load_optional_sources(loader, conversation_id, "记忆长偏好")


def test_large_item_is_omitted_atomically_by_own_budget(context):
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions), content="长偏好" * 600)
    with sessions() as session:
        session.add(ContextContributorSettings(id=1, revision=1, settings_json=ContextPolicies(
            confirmed_memory=ContributorPolicy(enabled=True, max_units=256)).model_dump_json()))
        session.commit()
    result = load_optional_sources(loader, conversation_id, "记忆长偏好")
    assert result.contributors[1].messages == ()
    assert result.contributors[1].diagnostics["omitted_count"] == 1


def test_temporary_optional_read_failure_omits_all_data_but_integrity_failure_propagates():
    from offerpilot.context_projector.loader import SourceTemporarilyUnavailable

    class FailedLoader:
        def __init__(self, error):
            self.error = error

        def load(self, read, freeze):
            raise self.error

    result = load_optional_sources(FailedLoader(SourceTemporarilyUnavailable()), 1, "当前问题")
    assert all(item.status == "unavailable" and not item.messages for item in result.contributors)
    assert result.sources == () and result.covered_history == ()
    for error in (ProjectionError("source_load_failed"), ProjectionError("optional_source_scope_unavailable")):
        with pytest.raises(ProjectionError) as caught:
            load_optional_sources(FailedLoader(error), 1, "当前问题")
        assert caught.value is error


def test_sqlite_busy_is_temporary_but_missing_table_is_not(context):
    import sqlite3
    from offerpilot.context_projector.loader import SourceTemporarilyUnavailable

    _, loader, _ = context
    with sqlite3.connect(loader._pool.database, isolation_level=None) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(SourceTemporarilyUnavailable):
                loader.load(lambda connection: connection.execute("SELECT id FROM conversations").fetchall(), lambda value: value)
        finally:
            writer.rollback()
    assert loader._pool._queue.qsize() == 4
    with pytest.raises(ProjectionError) as caught:
        loader.load(lambda connection: connection.execute("SELECT * FROM nonexistent_optional_source"), lambda value: value)
    assert not isinstance(caught.value, SourceTemporarilyUnavailable)


def test_memory_delete_removes_current_and_old_text_from_actual_recall(context):
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    item = save(repository, content="旧记忆正文")
    save(repository, 1, memory_id=item["id"], content="更正后的记忆")
    projected = load_optional_sources(loader, conversation_id, "记忆长偏好")
    messages = str(projected.contributors[1].messages)
    assert "更正后的记忆" in messages
    assert "旧记忆正文" not in messages
    save(repository, 2, memory_id=item["id"], action="delete", content="")
    projected = load_optional_sources(loader, conversation_id, "记忆长偏好")
    assert projected.contributors[1].status == "not_applicable"
    assert projected.contributors[1].messages == ()
    assert not any(source.kind == "confirmed_memory" for source in projected.sources)


def test_memory_projection_preserves_category_evidence_and_excludes_expired(context):
    import json
    from sqlalchemy import delete
    from offerpilot.confirmed_memory.models import ConfirmedMemoryVersionMetadata
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    legacy = save(repository, content="legacy personal claim")
    with sessions() as session:
        session.execute(delete(ConfirmedMemoryVersionMetadata))
        session.commit()
    repository.mutate(None, MemoryMutation(mutation_id=uuid4(), action="confirm", expected_version=0,
        confirmed=True, content="expired personal claim", metadata={"category": "preparation", "valid_until": "2000-01-01T00:00:00Z"}))
    goal = repository.mutate(None, MemoryMutation(mutation_id=uuid4(), action="confirm", expected_version=0,
        confirmed=True, content="career personal claim", metadata={"category": "goal", "valid_until": None}))
    result = load_optional_sources(loader, conversation_id, "personal claim")
    items = json.loads(result.contributors[1].messages[0].content.split("\n", 1)[1])["items"]
    by_id = {item["id"]: item for item in items}
    assert set(by_id) == {legacy["id"], goal["id"]}
    assert by_id[legacy["id"]]["category"] == "unclassified"
    assert by_id[goal["id"]]["category"] == "goal"
    assert all(item["evidence_status"] == "user_confirmed" and "preference" not in item for item in items)
    message = result.contributors[1].messages[0].content
    assert "不证明外部事实" in message and "不是指令" in message
    assert "expired personal claim" not in message


@pytest.mark.parametrize("field,value", [("category", ""), ("category", "verified_fact"), ("valid_until", "2030-01-01T00:00:00")])
def test_corrupt_memory_metadata_never_degrades_to_legacy_defaults(context, field, value):
    from sqlalchemy import select
    from offerpilot.confirmed_memory.models import ConfirmedMemoryVersionMetadata
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions))
    with sessions() as session:
        record = session.scalar(select(ConfirmedMemoryVersionMetadata))
        setattr(record, field, value)
        session.commit()
    with pytest.raises(ProjectionError, match="source_load_failed"):
        load_optional_sources(loader, conversation_id, "personal claim")


def _memory(identity, content, category="unclassified", valid_until=None):
    return {"id": identity, "version": 1, "content": content, "category": category,
            "valid_until": valid_until, "evidence_status": "user_confirmed"}


@pytest.mark.parametrize("query,expected", [
    ("准备面试", {"project", "prep", "goal"}),
    ("推荐职位", {"goal", "preference", "project"}),
    ("优化履历", {"project", "goal"}),
    ("居家办公", {"preference"}),
    ("聊聊星空", set()),
    ("", set()),
    ("Kafka", {"legacy"}),
    ("面试后找工作", {"goal", "preference", "project", "prep"}),
])
def test_memory_scenarios_and_lexical_aliases(query, expected):
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("project", "支付系统改造", "experience"),
            _memory("prep", "需要练习结构化回答", "preparation"),
            _memory("goal", "希望加入创业公司", "goal"),
            _memory("preference", "偏好远程工作", "preference"),
            _memory("legacy", "过去做过 Kafka 运维")]
    result = select_memory(pool, query)
    ids = {item["id"] for item in result.items}
    assert expected == ids
    if not expected:
        assert ids == set()
    assert all(type(value) in (int, bool) for value in result.diagnostics.values())
    assert "Kafka" not in str(result.diagnostics)


def test_memory_ranking_beats_id_order_at_same_budget():
    import json
    from offerpilot.context_sources.loader import _contributor
    from offerpilot.context_sources.memory import select_memory
    # Deliberately fixed IDs and equal-sized unrelated distractions, no real users.
    pool = [_memory(f"a{i}", "园艺兴趣偏好" * 10, "preference") for i in range(8)]
    pool += [_memory("z", "Kafka 项目经历", "experience")]
    policy = ContributorPolicy(enabled=True, max_units=1100)
    baseline, _ = _contributor("confirmed_memory", policy, pool)
    ranked, _ = _contributor("confirmed_memory", policy, select_memory(pool, "Kafka 面试").items)
    def ids(result):
        return {item["id"] for item in json.loads(result.messages[0].content.split("\n", 1)[1])["items"]}
    assert "z" not in ids(baseline)
    assert "z" in ids(ranked)


def test_memory_cross_scenario_duplicates_and_deterministic_ties():
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("b", "Kafka", "preference"), _memory("a", "Kafka", "preference"),
            _memory("c", "Kafka", "goal"), _memory("d", "Kafka", "preference", "2099-01-01T00:00:00+00:00")]
    first = select_memory(pool, "Kafka 面试")
    second = select_memory(list(reversed(pool)), "Kafka 面试")
    assert first == second
    assert {item["id"] for item in first.items} == {"a", "c", "d"}
    assert first.diagnostics["memory_duplicate_count"] == 1


def test_irrelevant_memory_is_absent_from_projection_and_fingerprint(context):
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    save(repository, content="番茄种植记录")
    result = load_optional_sources(loader, conversation_id, "Kafka")
    assert result.contributors[1].status == "not_applicable"
    assert result.contributors[1].diagnostics["memory_irrelevant_count"] == 1
    assert result.sources == ()


def test_memory_workspace_isolation_and_no_match_does_not_skip_validation(context, tmp_path):
    sessions, loader, conversation_id = context
    foreign = init_database(tmp_path / "other.db")
    save(ConfirmedMemoryRepository(foreign), content="Kafka private")
    assert load_optional_sources(loader, conversation_id, "Kafka").sources == ()
    # Candidate validation precedes relevance, even when the query cannot match.
    from sqlalchemy import select
    from offerpilot.confirmed_memory.models import ConfirmedMemoryVersionMetadata
    save(ConfirmedMemoryRepository(sessions), content="园艺")
    with sessions() as session:
        session.scalar(select(ConfirmedMemoryVersionMetadata)).category = "invalid"
        session.commit()
    with pytest.raises(ProjectionError, match="source_load_failed"):
        load_optional_sources(loader, conversation_id, "Kafka")


def test_memory_confirmed_task_hint_and_current_request_precedence():
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("p", "练习结构化回答", "preparation"),
            _memory("g", "期望远程工作", "goal")]
    assert select_memory(pool, "帮我准备一下").items == []
    hinted = select_memory(pool, "帮我准备一下", confirmed_interview_context=True)
    assert hinted.items[0]["id"] == "p"
    explicit = select_memory(pool, "推荐岗位", confirmed_interview_context=True)
    assert [item["id"] for item in explicit.items] == ["g"]


def test_explicit_expression_preference_is_available_across_scenarios():
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("language", "请始终用简洁中文回答", "preference"),
            _memory("hobby", "爱好园艺", "preference")]
    for query in ("准备面试", "优化简历", "介绍 Kafka", "你好"):
        assert [item["id"] for item in select_memory(pool, query).items] == ["language"]


@pytest.mark.parametrize("query", ["今晚吃什么", "推荐一道菜", "今天天气如何", "帮我准备晚餐", "继续聊晚饭", "你好", "", "what is for dinner?", "continue with dinner"])
def test_readiness_hint_does_not_override_explicit_unrelated_requests(query):
    from offerpilot.context_sources.memory import select_memory
    result = select_memory([_memory("prep", "结构化回答训练", "preparation")], query,
                           confirmed_interview_context=True)
    assert result.items == []
    assert result.diagnostics["memory_scenario_interview"] is False


@pytest.mark.parametrize("query", ["继续", "请继续。", "帮我准备一下！", "接着来", "Please continue."])
def test_readiness_hint_accepts_only_bounded_continuation_phrases(query):
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("prep", "结构化回答训练", "preparation")]
    assert select_memory(pool, query).items == []
    assert select_memory(pool, query, confirmed_interview_context=True).items == pool


@pytest.mark.parametrize("term", ["Kafka", "C++", "C#", "Node.js", ".NET", "ASP.NET", "scikit-learn"])
@pytest.mark.parametrize("suffix", [".", "-", "...", "。", "!"])
def test_memory_technical_tokens_ignore_trailing_punctuation(term, suffix):
    from offerpilot.context_sources.memory import select_memory
    pool = [_memory("match", term)]
    assert select_memory(pool, term + suffix).items == pool
    assert select_memory([_memory("match", term + suffix)], term).items


@pytest.mark.parametrize("left,right", [("C++", "C#"), ("C++", "C"), ("Node.js", "nodejs"), (".NET", "net"), ("scikit-learn", "scikitlearn")])
def test_memory_technical_token_identity_is_preserved(left, right):
    from offerpilot.context_sources.memory import select_memory
    assert select_memory([_memory("other", left)], right).items == []


def test_exploratory_memory_metrics_do_not_reward_duplicates_or_no_hit_recall():
    from scripts.evaluate_memory_retrieval import measure
    result = measure([{"group": "a"}, {"group": "a"}, {"group": "other"}], ["a", "b"])
    assert result["recall"] == 0.5
    assert result["irrelevant_count"] == 1
    assert result["duplicate_count"] == 1
    assert result["missing"] == ["b"]
    assert measure([], [])["recall"] is None
    assert measure([], [])["empty_correct"] is True
    assert measure([{"group": "a"}], [])["empty_correct"] is False


def test_exploratory_memory_comparison_is_repeatable_and_budget_bounded():
    import json
    from pathlib import Path
    from scripts.evaluate_memory_retrieval import evaluate
    fixture = json.loads((Path(__file__).resolve().parents[1] / "evals/fixtures/memory-retrieval-exploratory-v1.json").read_text())
    # Full scenario matrix, not a quality-pass threshold.
    results = evaluate(fixture)
    assert results == evaluate(fixture)
    assert len(results) == len(fixture["queries"]) * len(fixture["budgets"]) * len(fixture["id_order_seeds"]) * 2
    assert all(row["bytes"] <= row["budget"] for row in results)
    assert all("long" in row["oversize_relevant"] for row in results if row["case"] == "long_budget")


def test_candidate_experiment_admission_and_topic_switch_are_bounded():
    from scripts.memory_experiments.candidate_v2 import select_memory
    pool = [_memory("legacy", "过去参与过检索服务建设"),
            _memory("food", "喜欢甜食", "preference"),
            _memory("job", "希望进入金融行业", "goal"),
            _memory("art", "业余爱好陶艺")]
    assert "legacy" in {item["id"] for item in select_memory(pool, "准备面试").items}
    assert "food" not in {item["id"] for item in select_memory(pool, "推荐岗位").items}
    assert [item["id"] for item in select_memory(pool, "不要聊工作，改聊陶艺").items] == ["art"]
    assert select_memory(pool, "推荐一道甜点", confirmed_interview_context=True).items == []
    assert select_memory(pool, "", confirmed_interview_context=True).items == []


def test_candidate_experiment_backfill_cap_does_not_restore_all_unknowns():
    from scripts.memory_experiments.candidate_v2 import BACKFILL_LIMIT, select_memory
    pool = [_memory(str(i), "过去参与过服务建设") for i in range(20)]
    # Distinct contents prevent deduplication from masking admission limits.
    for i, item in enumerate(pool):
        item["content"] += str(i)
    result = select_memory(pool, "优化简历")
    assert len(result.items) == BACKFILL_LIMIT
    assert select_memory([_memory("u", "一条无法判断用途的记录")], "优化简历").items == []


def test_memory_evaluation_separates_required_helpful_and_prebudget():
    from scripts.evaluate_memory_retrieval import evaluate, summarize
    fixture = {"id_order_seeds": [0], "budgets": [4096], "memories": [
        {"key": "required", "content": "Zeta", "category": "unclassified"},
        {"key": "helpful", "content": "中文短句", "category": "preference"},
    ], "queries": [{"id": "q", "family": "metric", "query": "Zeta",
                    "required": ["required"], "helpful": ["helpful"],
                    "relevant": ["required", "helpful"]}]}
    rows = [r for r in evaluate(fixture) if r["strategy"] == "task_lexical"]
    summary = summarize(rows)
    assert summary["required_recall"] == summary["pre_required_recall"] == 1
    assert summary["helpful_recall"] == summary["pre_helpful_recall"] == 0
    assert summary["macro_recall"] == summary["pre_recall"] == 0.5
    assert summary["irrelevant_fraction"] == summary["pre_irrelevant_fraction"] == 0
