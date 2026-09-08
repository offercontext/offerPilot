"""KI-11：建立真实 Source 与检索质量门禁。

覆盖 tickets.md KI-11 全部验收点：
- 仓库内 5 份公开真实结构 Source + fixture hash 标识（缺失/修改明确失败）。
- 查询集 ≥ 20 条、每份 Source ≥ 4 条，覆盖中文/英文/代码/自然语言。
- 验收报告列出 Snapshot digest、Evidence 数量、回读结果、Brief 状态。
- 相同 extractor 重跑 digest/Evidence ID/位置/内容完全一致。
- Evidence 回读成功率 100%。
- Brief Schema/citation/support/coverage 通过率 100%（stub provider）。
- Lexical Recall@5=100%、MRR≥0.9；自然语言 Recall@5≥80%。
- Provider/Brief 失败场景 Evidence 仍可搜索（7 类故障）。
- 门禁失败非零退出 + bad case 含 Evidence ID。
- 报告不含原文/API Key/Prompt/Provider 原始响应。
- 编码/空/超限/Markdown 结构/Bundle 边界 fixtures 可提交且被正确处理。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.acceptance import (
    AcceptanceGateConfig,
    AcceptanceReport,
    QuerySpec,
    _evaluate_queries,
    run_acceptance,
    validate_acceptance_contract,
)
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW

REPO_FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def test_ki11_contract_requires_five_sources_and_twenty_expected_queries() -> None:
    errors = validate_acceptance_contract([], [])
    assert any("正好 5 份 Source" in error for error in errors)
    assert any("至少 20 条" in error for error in errors)


def test_ki11_recall_requires_expected_evidence_not_only_source() -> None:
    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "hits": [
                    {
                        "source_id": 1,
                        "evidence_id": "ev_unrelated",
                        "canonical_excerpt": "同一 Source 的无关章节",
                    }
                ]
            }

    class _HttpClient:
        def post(self, path: str, *, json: dict[str, object]) -> _Response:
            assert path == "/api/knowledge/evidence/search"
            assert json == {"query": "人工预期主题", "limit": 5}
            return _Response()

    query = QuerySpec(
        query="人工预期主题",
        query_type="lexical_chinese",
        source_key="source-a",
        expect_hit=True,
        content_keywords=("预期主题",),
    )
    result = _evaluate_queries({"source-a": 1}, [query], _HttpClient())[0]  # type: ignore[arg-type]
    assert result.recall_hit is False
    assert result.first_rank is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _provider_config(context_window: int = BRIEF_MIN_CONTEXT_WINDOW) -> Config:
    """合格 Brief Provider 配置（测试用 stub key，不访问网络）。"""
    provider = AIProviderProfile(
        id="default",
        label="Default",
        provider="openai",
        api_key="sk-test-acceptance",
        base_url="https://example.com",
        model="gpt-acceptance",
        enabled=True,
        context_window=context_window,
        max_output_tokens=4096,
    )
    return Config(
        api_key="sk-test-acceptance",
        providers=[provider],
        active_provider_id="default",
    )


def _run(tmp_path: Path, **overrides: Any) -> AcceptanceReport:
    return run_acceptance(
        fixtures_dir=REPO_FIXTURES,
        data_dir=tmp_path,
        config=_provider_config(),
        **overrides,
    )


def _summary(report: AcceptanceReport) -> str:
    return json.dumps(report.to_safe_json(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# manifest / queries 契约
# ---------------------------------------------------------------------------


def test_ki11_manifest_declares_five_sources_with_content_hash() -> None:
    manifest = json.loads((REPO_FIXTURES / "manifest.json").read_text("utf-8"))
    sources = manifest["sources"]
    assert len(sources) == 5
    for s in sources:
        assert s["source_key"], "source_key 不能为空"
        assert s["fixture_path"], "fixture_path 不能为空"
        assert s["expected_source_hash"].startswith("sha256:"), (
            "必须用 source_hash 标识真实 Source"
        )
        assert (REPO_FIXTURES / s["fixture_path"]).exists(), (
            f"fixture 缺失：{s['fixture_path']}"
        )


def test_ki11_queries_cover_four_types_and_meet_minimum_count() -> None:
    data = json.loads((REPO_FIXTURES / "queries.json").read_text("utf-8"))
    queries = data["queries"]
    hit_queries = [q for q in queries if q["expect_hit"]]
    assert len(hit_queries) >= 20, f"expect_hit 查询至少 20 条，实际 {len(hit_queries)}"
    types = {q["query_type"] for q in hit_queries}
    assert {
        "lexical_chinese",
        "lexical_english",
        "lexical_code",
        "natural_language",
    } <= types, f"查询类型覆盖不全：{types}"
    manifest = json.loads((REPO_FIXTURES / "manifest.json").read_text("utf-8"))
    for s in manifest["sources"]:
        per = [q for q in hit_queries if q["source_key"] == s["source_key"]]
        assert len(per) >= 4, f"{s['source_key']} 只有 {len(per)} 条查询"


# ---------------------------------------------------------------------------
# 核心 happy path：一次运行所有硬门禁
# ---------------------------------------------------------------------------


def test_ki11_acceptance_passes_all_hard_gates(tmp_path: Path) -> None:
    report = _run(tmp_path)
    assert report.passed, f"硬门禁未通过：\n{_summary(report)}"
    assert report.metrics["lexical_recall_at_5"] == 1.0
    assert report.metrics["lexical_mrr"] >= 0.9
    assert report.metrics["natural_language_recall_at_5"] >= 0.8
    assert report.metrics["evidence_readback_rate"] == 1.0
    assert report.metrics["brief_pass_rate"] == 1.0
    assert len(report.source_results) == 5


def test_ki11_report_records_snapshot_digest_evidence_count_brief_status(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path)
    for r in report.source_results:
        assert r.snapshot_digest, f"{r.source_key} 缺 Snapshot digest"
        assert r.evidence_count > 0, f"{r.source_key} Evidence 数量应 > 0"
        assert r.brief_status == "ready", f"{r.source_key} Brief 应 ready"
        assert r.readback_fail == 0, f"{r.source_key} 回读失败应为 0"
        # hash 只暴露前缀，不暴露完整原文路径
        assert r.source_hash_prefix.startswith("sha256:")
        assert len(r.source_hash_prefix) < 24


# ---------------------------------------------------------------------------
# 回读 / 幂等
# ---------------------------------------------------------------------------


def test_ki11_evidence_readback_rate_is_100_percent(tmp_path: Path) -> None:
    report = _run(tmp_path)
    assert report.metrics["evidence_readback_rate"] == 1.0
    total_evidence = sum(r.evidence_count for r in report.source_results)
    total_pass = sum(r.readback_pass for r in report.source_results)
    assert total_pass == total_evidence


def test_ki11_idempotent_rerun_keeps_digest_evidence_ids_and_positions(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path)
    for r in report.source_results:
        assert r.rerun_snapshot_digest == r.snapshot_digest, (
            f"{r.source_key} 重跑 Snapshot digest 不一致"
        )
        assert r.rerun_consistent is True, (
            f"{r.source_key} 重跑 Evidence ID/位置/内容不一致"
        )


# ---------------------------------------------------------------------------
# 检索指标
# ---------------------------------------------------------------------------


def test_ki11_lexical_recall_and_mrr_meet_gate(tmp_path: Path) -> None:
    report = _run(tmp_path)
    lexical = [
        q
        for q in report.query_results
        if q.query_type.startswith("lexical_") and q.expect_hit
    ]
    assert lexical, "应有 lexical 查询结果"
    assert all(q.recall_hit for q in lexical), (
        f"lexical Recall@5 未达 100%：{_summary(report)}"
    )
    assert report.metrics["lexical_mrr"] >= 0.9


def test_ki11_natural_language_recall_meets_gate(tmp_path: Path) -> None:
    report = _run(tmp_path)
    assert report.metrics["natural_language_recall_at_5"] >= 0.8


def test_ki11_negative_queries_do_not_inflate_recall(tmp_path: Path) -> None:
    """Spec 防评估漏洞：expect_hit=false 查询不参与 Recall，且不应命中已知 Source。"""
    report = _run(tmp_path)
    negatives = [q for q in report.query_results if not q.expect_hit]
    assert negatives, "应有 negative 诊断查询"
    for q in negatives:
        assert q.recall_hit is False, f"negative 查询误判为命中：{q.query}"


# ---------------------------------------------------------------------------
# Brief 验收
# ---------------------------------------------------------------------------


def test_ki11_brief_passes_schema_citation_support_coverage(tmp_path: Path) -> None:
    report = _run(tmp_path)
    assert report.metrics["brief_pass_rate"] == 1.0
    for r in report.source_results:
        assert r.brief_status == "ready"


def test_ki11_brief_failure_scenarios_keep_evidence_searchable(tmp_path: Path) -> None:
    report = _run(tmp_path)
    scenarios = {r.scenario: r for r in report.brief_failure_results}
    expected = {
        "invalid_json",
        "forged_citation",
        "unsupported_support",
        "coverage_missing",
        "timeout",
        "rate_limit",
        "fallback_success",
    }
    assert expected <= set(scenarios), (
        f"故障场景缺失：{expected - set(scenarios)}"
    )
    for name, r in scenarios.items():
        if name == "fallback_success":
            assert r.brief_status == "ready", "fallback 成功后 Brief 应 ready"
        else:
            assert r.brief_status == "failed", (
                f"{name} 应导致 Brief failed，实际 {r.brief_status}"
            )
        assert r.evidence_searchable, f"{name} 故障下 Evidence 仍须可搜索"


# ---------------------------------------------------------------------------
# 报告安全
# ---------------------------------------------------------------------------


def test_ki11_report_excludes_raw_content_api_key_prompt_and_response(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path)
    blob = json.dumps(report.to_safe_json(), ensure_ascii=False)
    # API Key 不写入报告
    assert "sk-test-acceptance" not in blob
    # 真实 Source 原文片段不写入报告（取每份 Source 的独特正文短语）
    raw_phrases = [
        "in-sync replica 的缩写",
        "基于哈希表，查找平均",
        "asyncio.create_task",
        "返回 [state, setState]",
        "tokenize = 'trigram'",
    ]
    for phrase in raw_phrases:
        assert phrase not in blob, f"报告泄露原文：{phrase}"
    # 完整 Prompt / Provider 原始响应关键词不写入
    assert "Knowledge Brief Generator" not in blob
    assert "你是 OfferPilot" not in blob


# ---------------------------------------------------------------------------
# 门禁失败 → bad case + 非零退出语义
# ---------------------------------------------------------------------------


def test_ki11_gate_failure_outputs_bad_case_with_evidence_id(tmp_path: Path) -> None:
    """人为抬高 Recall 门禁到不可达值，验证 passed=False 且 failures 含定位信息。"""
    report = run_acceptance(
        fixtures_dir=REPO_FIXTURES,
        data_dir=tmp_path,
        config=_provider_config(),
        gates=AcceptanceGateConfig(lexical_recall_at_5=1.0001),
    )
    assert report.passed is False
    assert report.failures, "门禁失败必须输出 bad case"
    case = report.failures[0]
    assert case.gate
    assert case.reason


def test_ki11_readback_failure_appears_as_bad_case(tmp_path: Path) -> None:
    """构造一个回读失败的 Source（手动篡改 canonical_excerpt），验证 bad case 含 evidence_id。"""
    report = run_acceptance(
        fixtures_dir=REPO_FIXTURES,
        data_dir=tmp_path,
        config=_provider_config(),
        inject_readback_failure=True,
    )
    assert report.passed is False
    readback_cases = [f for f in report.failures if f.gate == "evidence_readback"]
    assert readback_cases, "回读失败必须产生 bad case"
    assert any(c.evidence_id.startswith("ev_") for c in readback_cases), (
        "bad case 必须含可定位的 Evidence ID"
    )


# ---------------------------------------------------------------------------
# fixture hash 校验
# ---------------------------------------------------------------------------


def test_ki11_fixture_hash_mismatch_fails_acceptance(tmp_path: Path) -> None:
    fake_fixtures = tmp_path / "fixtures"
    shutil.copytree(REPO_FIXTURES, fake_fixtures)
    target = fake_fixtures / "structures" / "kafka-isr.md"
    target.write_bytes(target.read_bytes() + b"\n\n# tampered\n")
    report = run_acceptance(
        fixtures_dir=fake_fixtures,
        data_dir=tmp_path / "data",
        config=_provider_config(),
    )
    assert report.passed is False
    assert any("kafka-isr" in e for e in report.fixture_errors), (
        f"hash 不匹配应明确报错：{report.fixture_errors}"
    )


def test_ki11_fixture_missing_fails_acceptance(tmp_path: Path) -> None:
    fake_fixtures = tmp_path / "fixtures"
    shutil.copytree(REPO_FIXTURES, fake_fixtures)
    (fake_fixtures / "structures" / "java-collections.md").unlink()
    report = run_acceptance(
        fixtures_dir=fake_fixtures,
        data_dir=tmp_path / "data",
        config=_provider_config(),
    )
    assert report.passed is False
    assert any("java-collections" in e for e in report.fixture_errors)


# ---------------------------------------------------------------------------
# 边界 fixtures
# ---------------------------------------------------------------------------


def test_ki11_edge_fixtures_rejected_or_handled(tmp_path: Path) -> None:
    """编码/空/超限/Markdown 结构/Bundle 边界 fixtures 被 acceptance 边界校验正确分类。"""
    report = _run(tmp_path)
    edge = {e.name: e for e in report.edge_fixture_results}
    # 编码矩阵全部应被识别为合法编码并解码成功
    for enc in ("utf8", "utf8bom", "utf16le", "utf16be", "gbk"):
        assert enc in edge, f"编码 fixture {enc} 缺失"
        assert edge[enc].accepted, f"{enc} 应被接受"
    # 空文件与超限被拒
    assert edge["empty"].rejected
    assert "unsupported_type" in edge["empty"].error_code or edge["empty"].error_code == "unsupported_type"
    assert edge["oversized"].rejected
    assert edge["oversized"].error_code == "source_too_large"
    # Markdown 综合结构被接受且生成多种 Evidence
    assert edge["markdown_structure"].accepted
    assert edge["markdown_structure"].evidence_kind_count >= 4, (
        "Markdown 结构 fixture 应触发 ≥4 种 Evidence 类型"
    )
    # 纯文本被接受
    assert edge["text_plain"].accepted


def test_ki11_bundle_boundary_fixtures_classified(tmp_path: Path) -> None:
    report = _run(tmp_path)
    bundle = {b.name: b for b in report.bundle_fixture_results}
    assert bundle["valid"].accepted
    for name in (
        "invalid_missing_image",
        "invalid_duplicate_image",
        "invalid_unused_asset",
        "invalid_path_traversal",
        "invalid_media_disguise",
    ):
        assert name in bundle, f"Bundle fixture {name} 缺失"
        assert bundle[name].rejected, f"{name} 应被拒绝"
        assert bundle[name].error_code == "bundle_invalid"


# ---------------------------------------------------------------------------
# 检索只走 FTS（不引入向量/rerank）
# ---------------------------------------------------------------------------


def test_ki11_retrieval_uses_fts_only_no_embedding(tmp_path: Path) -> None:
    report = _run(tmp_path)
    for q in report.query_results:
        assert q.retrieval_method == "fts", (
            f"KI-11 禁止偷加向量/rerank/LLM rewrite：{q.query} 用了 {q.retrieval_method}"
        )


# ---------------------------------------------------------------------------
# 故障静态 fixtures 契约：被 acceptance 代码读取，须存在且格式合法
# ---------------------------------------------------------------------------


def test_ki11_failure_fixtures_static_samples_are_valid() -> None:
    """failure/ 静态样本被 acceptance._make_failure_stub 读取，须存在且占位格式正确。"""
    failure_dir = REPO_FIXTURES / "failure"
    invalid_json = (failure_dir / "invalid-json.txt").read_text("utf-8").strip()
    assert invalid_json, "invalid-json.txt 不能为空"
    # 非法 JSON 样本确实不是 JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(invalid_json)

    forged = json.loads((failure_dir / "forged-citation.json").read_text("utf-8"))
    assert forged["payload"]["overview"][0]["evidence_ids"] == ["ev_:forged:1"]

    coverage = json.loads((failure_dir / "coverage-missing.json").read_text("utf-8"))
    assert "ev_:real:" in json.dumps(coverage["payload"], ensure_ascii=False)
    # KBR-04：fixture 只引用第一条 Evidence，其余文本章节未被实际引用 → 派生 coverage missing
    all_evidence_ids: list[str] = []
    for block_name in ("overview", "key_points", "section_guides", "limitations"):
        for item in coverage["payload"][block_name]:
            all_evidence_ids.extend(item["evidence_ids"])
    assert set(all_evidence_ids) == {"ev_:real:0"}

    unsupported = json.loads(
        (failure_dir / "unsupported-validation.json").read_text("utf-8")
    )
    assert unsupported["decision"] == "unsupported"
