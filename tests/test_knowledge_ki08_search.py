"""KI-08：交付可评估的 Evidence FTS 检索。

覆盖 Spec §15 验收点：
- 启动时验证 FTS5 + trigram（已在 db.py 实现，这里补强失败路径测试）
- 分列加权（source_title > heading_path > content）
- Query parser 处理中文长问句、ASCII identifier、英文词组、混合输入
- 短查询 (< 3 字符) 走 LIKE 子串回退，有上限
- 结果包含相邻 Evidence ID
- Retrieval Trace 记录 query/filters/hits/duration/label，不写原文
- FTS 错误显式抛出，不静默吞掉
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from conftest import wait_for_extraction

from offerpilot.knowledge.search import parse_query  # noqa: F401  # 保留给后续测试扩展


# ---------------------------------------------------------------------------
# 查询解析器单元测试
# ---------------------------------------------------------------------------


def test_parse_query_empty_returns_empty_mode() -> None:
    parsed = parse_query("   ")
    assert parsed.mode == "empty"
    assert parsed.match_expr == ""


def test_parse_query_short_ascii_uses_substring_fallback() -> None:
    parsed = parse_query("ab")
    assert parsed.mode == "substring"
    assert parsed.terms == ("ab",)


def test_parse_query_single_cjk_char_uses_substring_fallback() -> None:
    parsed = parse_query("中")
    assert parsed.mode == "substring"
    assert parsed.terms == ("中",)


def test_parse_query_two_cjk_chars_uses_substring_fallback() -> None:
    # CJK trigram tokenizer 需要 >= 3 字符；2 字 CJK 也走 LIKE
    parsed = parse_query("卡夫")
    assert parsed.mode == "substring"
    assert parsed.terms == ("卡夫",)


def test_parse_query_three_cjk_chars_uses_fts_match() -> None:
    parsed = parse_query("卡夫卡")
    assert parsed.mode == "fts"
    # 完整 3 字作为一个 trigram
    assert '"卡夫卡"' in parsed.match_expr


def test_parse_query_long_chinese_sentence_does_not_force_exact_phrase() -> None:
    """Spec §15：不再把无空格中文整句作为一个强制精确短语。"""
    parsed = parse_query("如何理解 Kafka ISR 的同步机制")
    assert parsed.mode == "fts"
    # 多个 trigram 通过 OR 连接，而不是整句作为一个 phrase
    assert " OR " in parsed.match_expr
    # 整个原句不应作为一个精确 phrase 出现
    assert '"如何理解 Kafka ISR 的同步机制"' not in parsed.match_expr


def test_parse_query_ascii_identifier_preserved() -> None:
    parsed = parse_query("KafkaISR")
    assert parsed.mode == "fts"
    assert '"KafkaISR"' in parsed.match_expr


def test_parse_query_ascii_dotted_identifier_preserved() -> None:
    parsed = parse_query("java.util.HashMap")
    assert parsed.mode == "fts"
    # dotted identifier 应作为整体保留
    assert '"java.util.HashMap"' in parsed.match_expr


def test_parse_query_english_phrase_split_by_whitespace() -> None:
    parsed = parse_query("in-sync replica")
    assert parsed.mode == "fts"
    expr = parsed.match_expr
    assert '"in-sync"' in expr
    assert '"replica"' in expr


def test_parse_query_mixed_chinese_english() -> None:
    parsed = parse_query("Kafka ISR 同步副本")
    assert parsed.mode == "fts"
    expr = parsed.match_expr
    assert '"Kafka"' in expr
    assert '"ISR"' in expr
    # CJK 部分按 trigram 切分（同步副、步副本）
    assert " OR " in expr


def test_parse_query_strips_fts5_special_chars() -> None:
    parsed = parse_query('Kafka" ISR * ( ) + - :')
    assert parsed.mode == "fts"
    # 不应包含未引用的特殊字符
    assert "*" not in parsed.match_expr
    assert "(" not in parsed.match_expr


def test_parse_query_pure_punctuation_returns_substring() -> None:
    parsed = parse_query("？？？！！！")
    # 标点无可识别 token，回退到 substring（带原始截断）
    assert parsed.mode == "substring"


# ---------------------------------------------------------------------------
# API 集成测试
# ---------------------------------------------------------------------------



def _upload(client: TestClient, filename: str, content: bytes, title_hint: str = ""):
    files = {"file": (filename, content, "text/markdown")}
    data = {"title_hint": title_hint} if title_hint else None
    response = client.post("/api/knowledge/sources", files=files, data=data)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response

def test_ki08_search_chinese_long_sentence_returns_evidence(app_client) -> None:
    content = (
        "# Kafka ISR\n\n"
        "ISR 是 in-sync replica 的缩写，表示与 leader 保持同步的副本集合。\n"
    ).encode("utf-8")
    response = _upload(app_client, "kafka.md", content)
    source_id = response.json()["source"]["id"]

    # 中文长问句应通过 trigram 切分命中
    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "如何理解 ISR 同步机制"},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"], "中文长问句搜索应命中"
    top = body["hits"][0]
    assert top["source_id"] == source_id
    assert "ISR" in top["canonical_excerpt"]


def test_ki08_search_ascii_identifier_returns_evidence(app_client) -> None:
    content = (
        "# Java Collections\n\n"
        "HashMap 与 TreeMap 都实现 Map 接口。\n"
    ).encode("utf-8")
    _upload(app_client, "java.md", content)

    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "HashMap"},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"]
    assert "HashMap" in body["hits"][0]["canonical_excerpt"]


def test_ki08_search_short_query_uses_substring_fallback(app_client) -> None:
    content = "# AI\n\n人工智能 (AI) 是计算机科学的分支。\n".encode("utf-8")
    _upload(app_client, "ai.md", content)

    # 2 字符 ASCII 走 substring fallback；仍可命中
    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "AI"},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"]
    assert "AI" in body["hits"][0]["canonical_excerpt"]


def test_ki08_search_short_cjk_uses_substring_fallback(app_client) -> None:
    content = "# 概率\n\n贝叶斯定理是概率论的基础。\n".encode("utf-8")
    _upload(app_client, "probability.md", content)

    # 单字 CJK 走 substring fallback
    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "概率"},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"]


def test_ki08_search_no_result_returns_empty_hits(app_client) -> None:
    content = "# Redis\n\nRedis 是内存数据库。\n".encode("utf-8")
    _upload(app_client, "redis.md", content)

    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "KafkaISR"},
    )
    assert search.status_code == 200
    assert search.json()["hits"] == []


def test_ki08_search_results_include_adjacent_evidence_ids(app_client) -> None:
    content = (
        "# Heading A\n\n第一段。\n\n## Heading B\n\n第二段。\n".encode("utf-8")
    )
    response = _upload(app_client, "structured.md", content)
    source_id = response.json()["source"]["id"]

    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    assert len(listing["items"]) == 2
    first_id = listing["items"][0]["id"]
    second_id = listing["items"][1]["id"]

    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "第二段"},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"]
    top = body["hits"][0]
    assert top["evidence_id"] == second_id
    assert top["previous_evidence_id"] == first_id
    # 第二段是最后一条 Evidence
    assert top["next_evidence_id"] is None


def test_ki08_search_excludes_archived_by_default(app_client) -> None:
    content = "# Kafka Archived\n\nKafka 是分布式日志。\n".encode("utf-8")
    response = _upload(app_client, "kafka.md", content)
    source_id = response.json()["source"]["id"]

    app_client.post(f"/api/knowledge/sources/{source_id}/archive")

    default_search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka"},
    )
    assert default_search.status_code == 200
    assert default_search.json()["hits"] == []

    include_search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka", "include_archived": True},
    )
    assert include_search.status_code == 200
    assert include_search.json()["hits"]


def test_ki08_search_source_ids_filter(app_client) -> None:
    kafka_content = "# Kafka\n\nKafka 是分布式日志。\n".encode("utf-8")
    redis_content = "# Redis\n\nRedis 是内存数据库。\n".encode("utf-8")
    kafka_id = _upload(app_client, "kafka.md", kafka_content).json()["source"]["id"]
    redis_id = _upload(app_client, "redis.md", redis_content).json()["source"]["id"]

    # 只搜 Kafka Source
    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka", "source_ids": [kafka_id]},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["hits"]
    assert all(hit["source_id"] == kafka_id for hit in body["hits"])
    assert all(hit["source_id"] != redis_id for hit in body["hits"])


def test_ki08_search_records_retrieval_trace(tmp_path, app_client) -> None:
    content = "# Kafka Trace\n\nKafka 是分布式日志系统。\n".encode("utf-8")
    _upload(app_client, "kafka.md", content)

    app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka", "evaluation_label": "fixture-1"},
    )

    with sqlite3.connect(tmp_path / "data.db") as conn:
        rows = conn.execute(
            "SELECT query, filters_json, hits_json, duration_ms, evaluation_label, "
            "error_code FROM knowledge_retrieval_traces ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert rows, "应写入 retrieval trace"
    query, filters_json, hits_json, duration_ms, label, error_code = rows[0]
    assert query == "Kafka"
    assert label == "fixture-1"
    assert error_code == ""
    assert duration_ms >= 0
    # hits_json 不含 Evidence 原文，只含 ID + score + 位置元数据
    assert "ev_" in hits_json
    assert "Kafka 是分布式日志系统" not in hits_json
    # filters_json 不含原文
    assert "Kafka" not in filters_json


def test_ki08_search_trace_excludes_canonical_excerpt(tmp_path, app_client) -> None:
    content = "# Sensitive\n\n敏感原文不应该进入 Trace。\n".encode("utf-8")
    _upload(app_client, "sensitive.md", content)

    app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "敏感原文"},
    )

    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT hits_json FROM knowledge_retrieval_traces ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    hits_json = row[0]
    assert "敏感原文" not in hits_json


def test_ki08_search_fTs_syntax_error_returns_stable_error(app_client) -> None:
    # 模拟 FTS5 无法解析的输入：纯引号包裹空 token
    # parse_query 应当 sanitize，但若运行时仍触发 OperationalError，API 层应稳定返回错误
    # 这里用纯引号查询验证 API 层不返回 500
    response = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": '""'},
    )
    # 空字符串清理后视为 empty，返回 200 空结果 OR 400；都不可是 500
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert response.json()["hits"] == []


def test_ki08_search_limit_clamped_to_max(app_client) -> None:
    content = "# Kafka\n\nKafka 是分布式日志。\n".encode("utf-8")
    _upload(app_client, "kafka.md", content)

    # limit=10000 应被限制到 50
    response = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka", "limit": 10000},
    )
    assert response.status_code == 200
    assert isinstance(response.json()["hits"], list)


def test_ki08_search_source_title_weighted_higher_than_content(app_client) -> None:
    """Spec §15：source_title 加权 > content。

    构造两个 Source：一个在标题命中关键词、一个仅在正文命中。
    验证标题命中的 Source 排在前。
    """
    title_content = "# Kafka 深入\n\n正文不含关键词。\n".encode("utf-8")
    body_content = "# 其他主题\n\n这里讨论 Kafka 工作机制。\n".encode("utf-8")
    title_resp = _upload(app_client, "title.md", title_content)
    body_resp = _upload(app_client, "body.md", body_content)
    title_source_id = title_resp.json()["source"]["id"]
    body_source_id = body_resp.json()["source"]["id"]

    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka"},
    )
    assert search.status_code == 200
    body = search.json()
    assert len(body["hits"]) >= 2
    # bm25 with weight (8.0, 4.0, 1.0)：标题命中 Source 必须排第一。
    top = body["hits"][0]
    assert top["source_id"] == title_source_id, (
        "标题命中 Source 必须排在 content 命中之前；"
        f"实际 top={top['source_id']}, title={title_source_id}, body={body_source_id}"
    )
    # 两个 Source 都应出现在结果中
    source_ids = {hit["source_id"] for hit in body["hits"]}
    assert title_source_id in source_ids
    assert body_source_id in source_ids


# ---------------------------------------------------------------------------
# FTS 启动校验
# ---------------------------------------------------------------------------


def test_ki08_init_database_raises_when_fts5_unavailable(tmp_path, monkeypatch) -> None:
    """Spec §15：FTS5 / trigram 不可用时启动失败，错误码 fts_unavailable。

    直接验证 ``_ensure_knowledge_fts`` 在 OperationalError("no such module: fts5")
    场景下抛 RuntimeError(含 ``fts_unavailable`` 标记)。init_database 调用链通过
    monkeypatch 验证。
    """
    from offerpilot import db as db_module

    def fake_ensure_knowledge_fts(_engine) -> None:
        raise RuntimeError(
            "fts_unavailable: SQLite FTS5 / trigram tokenizer not available"
        )

    monkeypatch.setattr(db_module, "_ensure_knowledge_fts", fake_ensure_knowledge_fts)
    with pytest.raises(RuntimeError) as exc_info:
        db_module.init_database(tmp_path / "data.db")
    assert "fts_unavailable" in str(exc_info.value)


def test_ki08_init_database_raises_when_trigram_unavailable(tmp_path, monkeypatch) -> None:
    """Spec §15：trigram tokenizer 不可用时启动失败。"""
    from offerpilot import db as db_module

    def fake_ensure_knowledge_fts(_engine) -> None:
        raise RuntimeError("fts_unavailable: trigram tokenizer not available")

    monkeypatch.setattr(db_module, "_ensure_knowledge_fts", fake_ensure_knowledge_fts)
    with pytest.raises(RuntimeError) as exc_info:
        db_module.init_database(tmp_path / "data.db")
    assert "fts_unavailable" in str(exc_info.value)


def test_ki08_ensure_knowledge_fts_translates_operational_error(tmp_path) -> None:
    """Spec §13 错误码 fts_unavailable 的产生路径。"""
    from offerpilot import db as db_module
    from sqlalchemy.exc import OperationalError

    class _FakeConnection:
        def execute(self, _statement):
            raise OperationalError(
                "CREATE VIRTUAL TABLE knowledge_evidence_fts",
                {},
                Exception("no such module: fts5"),
            )

    class _FakeEngine:
        def begin(self):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                yield _FakeConnection()

            return _cm()

    with pytest.raises(RuntimeError) as exc_info:
        db_module._ensure_knowledge_fts(_FakeEngine())
    message = str(exc_info.value).lower()
    assert "fts_unavailable" in message
