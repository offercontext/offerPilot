"""Fresh-process offline probe; no SDK import or Provider request is permitted."""
from __future__ import annotations

import json
import importlib.abc
import os
from pathlib import Path
import sys
import traceback

from fixed_evaluation_source_launcher import policy_proof

attempts: list[str] = []


def reject_network(event: str, _args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto", "socket.sendmsg", "subprocess.Popen", "os.system"}:
        attempts.append(event)
        raise RuntimeError("network forbidden in offline tokenizer regression")


sys.addaudithook(reject_network)
config = json.loads(os.environ.pop("KNOWLEDGE_TOKENIZER_PROBE"))
mode, data_dir = config["mode"], Path(config["data"])
source_before = policy_proof()
assert source_before['guard_active'] and sys.dont_write_bytecode and sys.flags.isolated


class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'litellm', 'openai', 'anthropic'}:
            raise AssertionError('Provider SDK import forbidden: ' + fullname)


sys.meta_path.insert(0, NoSDK())
samples = [
    "", "hello", "hello world", "Hello, world!", "We're testing contractions; I'd agree.",
    "# 岗位资料\n\n真实经历与缺失证据必须区分。\n", "你好，世界！", "معلومات الوظيفة والتجربة",
    "👩🏽‍💻 café e\u0301\r\n\tSELECT * FROM jobs WHERE id = 42;", "   \n\n\t", "x" * 8192,
]


def ingest_before_tokenizer_use() -> None:
    from sqlalchemy import text
    from offerpilot.db import init_database
    from offerpilot.knowledge.repository import KnowledgeRepository
    from offerpilot.knowledge.service import IngestError, IngestRequest, KnowledgeIngestService
    from offerpilot.knowledge.worker import ExtractionWorker, KnowledgeJobRunner

    data_dir.mkdir()
    sessions = init_database(data_dir / "data.db")
    try:
        repo = KnowledgeRepository(sessions)
        service = KnowledgeIngestService(repo, data_dir, sessions)
        # 真实 ingest 是本子进程首次 tokenizer 使用，不在前面调用私有 loader 或 count。
        uploaded = service.ingest(IngestRequest(filename="offline.md", content_bytes=b"# Offline\n\nEvidence stays exact.\n"))
        completed = KnowledgeJobRunner(repo, ExtractionWorker(repo, data_dir, sessions)).tick_extraction()
        source = repo.get_source(uploaded.source.id)
        assert completed and all(row.accepted for row in completed)
        assert source is not None and source.extraction_status == "extracted"
        assert source.active_snapshot_id is not None
        accepted = service.ingest(IngestRequest(filename="limit.txt", content_bytes=(" x" * 64000).encode()))
        assert accepted.source.id != source.id
        with sessions() as session:
            before = session.execute(text("SELECT COUNT(*) FROM knowledge_sources")).scalar_one()
        try:
            service.ingest(IngestRequest(filename="too-many.txt", content_bytes=(" x" * 64001).encode()))
        except IngestError as error:
            assert error.code == "source_too_large"
        else:
            raise AssertionError("64,001 tokens must be rejected")
        with sessions() as session:
            assert session.execute(text("SELECT COUNT(*) FROM knowledge_sources")).scalar_one() == before
    finally:
        sessions.kw["bind"].dispose()


def run() -> dict[str, object]:
    assert "litellm" not in sys.modules
    cache_keys = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR", "CUSTOM_TIKTOKEN_CACHE_DIR")
    environment_before = {key: os.environ.get(key) for key in cache_keys}
    if mode == "product":
        ingest_before_tokenizer_use()
    if mode == "official":
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
    else:
        from offerpilot.knowledge import tokenizer

        encoding = tokenizer._load_encoding()
        assert encoding is not None, "fixed offline encoding unavailable"
        assert tokenizer.tokenizer_status() == ("cl100k_base-1", False)
        assert tokenizer.count_tokens("hello world").count == 2
        assert tokenizer.count_tokens("hello world").fallback is False
    result: dict[str, object] = {"ids": [encoding.encode(text) for text in samples]}
    rejections = []
    special_ids = []
    for token in ("<|endoftext|>", "<|fim_prefix|>", "<|fim_middle|>", "<|fim_suffix|>", "<|endofprompt|>"):
        try:
            encoding.encode(token)
        except ValueError:
            rejections.append(token)
        if mode != "official":
            try:
                tokenizer.count_tokens(token)
            except ValueError:
                pass
            else:
                raise AssertionError("public count_tokens must reject reserved special tokens")
        special_ids.append(encoding.encode(token, allowed_special={token}))
    assert len(rejections) == 5
    result.update(rejections=rejections, special_ids=special_ids,
                  boundary_counts=[len(encoding.encode(" x" * n)) for n in (64000, 64001)])
    assert result["boundary_counts"] == [64000, 64001]
    if mode != "official":
        assert [tokenizer.count_tokens(" x" * n).count for n in (64000, 64001)] == [64000, 64001]
    if mode == "product":
        result["ingest_extraction_and_limit_passed"] = True
    assert "litellm" not in sys.modules
    assert not attempts
    assert {key: os.environ.get(key) for key in cache_keys} == environment_before
    assert policy_proof() == source_before
    result.update(network_attempts=attempts, sdk_imported=False, environment_unchanged=True)
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(run(), ensure_ascii=False))
    except BaseException as error:
        print(json.dumps(dict(error_type=type(error).__name__, network_attempts=attempts,
                              sdk_imported="litellm" in sys.modules,
                              frames=[dict(file=frame.filename, line=frame.lineno, function=frame.name)
                                      for frame in traceback.extract_tb(error.__traceback__)])))
        raise
