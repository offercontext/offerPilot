"""Fresh-process offline probe; no SDK import or Provider request is permitted."""
from __future__ import annotations

import json
import importlib.abc
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

from fixed_evaluation_source_launcher import policy_proof

attempts: list[dict[str, object]] = []
sdk_attempts: list[str] = []


def reject_network(event: str, _args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto", "socket.sendmsg", "subprocess.Popen", "os.system"}:
        attempts.append(dict(event=event, arguments=repr(_args),
            frames=[dict(file=frame.filename, line=frame.lineno, function=frame.name)
                    for frame in traceback.extract_stack()[:-1]]))
        raise RuntimeError("network forbidden in offline tokenizer regression")


sys.addaudithook(reject_network)
config = json.loads(os.environ.pop("KNOWLEDGE_TOKENIZER_PROBE"))
mode, data_dir = config["mode"], Path(config["data"])
source_before = policy_proof()
assert source_before['guard_active'] and sys.dont_write_bytecode and sys.flags.isolated


class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'litellm', 'openai', 'anthropic'}:
            sdk_attempts.append(fullname)
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


def cache_contract() -> None:
    """Strict offline scope belongs to this fresh process, not prior SDK threads."""
    from offerpilot.knowledge import tokenizer

    keys = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR", "CUSTOM_TIKTOKEN_CACHE_DIR")
    key = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    state, variable = config["state"], config["cache_variable"]
    cache = Path(config["cache"]) if config["cache"] else None
    data_dir.mkdir()
    selected = data_dir / "selected"
    selected.mkdir()
    entry = selected / key
    if mode == "cache-unavailable":
        if state == "corrupt":
            entry.write_bytes(b"corrupt")
        os.environ[variable] = "" if state == "disabled" else str(selected)
        if variable != "CUSTOM_TIKTOKEN_CACHE_DIR":
            os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = str(cache)
    elif mode == "default-unavailable":
        if state == "corrupt":
            entry.write_bytes(b"corrupt")
        tokenizer.distribution = lambda _name: SimpleNamespace(locate_file=lambda _path: entry)
    before = {name: os.environ.get(name) for name in keys}
    if mode in {"cache-unavailable", "default-unavailable"}:
        try:
            if mode == "default-unavailable":
                tokenizer.tokenizer_status()
            else:
                tokenizer.count_tokens("hello world")
        except tokenizer.TokenizerUnavailableError as error:
            assert "tokenizer_unavailable" in str(error)
        else:
            raise AssertionError("unavailable resource must fail closed")
        if state == "corrupt":
            assert entry.read_bytes() == b"corrupt"
        else:
            assert not entry.exists()
    else:
        assert cache is not None
        entry = cache / key
        original = entry.read_bytes()
        if mode == "cache-mutated":
            read_bytes = Path.read_bytes
            reads = []

            def change_after_read(path):
                content = read_bytes(path)
                if path == entry:
                    reads.append(path)
                    path.write_bytes(b"changed after the verified read")
                return content

            Path.read_bytes = change_after_read
            try:
                assert tokenizer.count_tokens("hello world").count == 2
            finally:
                Path.read_bytes = read_bytes
            assert reads == [entry]
            assert read_bytes(entry) == b"changed after the verified read"
        else:
            assert tokenizer.count_tokens("hello world").count == 2
            assert entry.read_bytes() == original
            assert {path.name for path in cache.iterdir()} == {key}
    assert {name: os.environ.get(name) for name in keys} == before


def run() -> dict[str, object]:
    assert "litellm" not in sys.modules
    cache_keys = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR", "CUSTOM_TIKTOKEN_CACHE_DIR")
    environment_before = {key: os.environ.get(key) for key in cache_keys}
    if mode == "deny-sdk":
        try:
            __import__("litellm")
        except AssertionError:
            pass
        assert not sdk_attempts, "swallowed SDK import attempt must still fail"
    if mode in {"deny-connect", "deny-dns"}:
        import socket

        # Negative controls deliberately swallow the immediate exception. The
        # retained audit record must still fail the probe; loopback is not exempt.
        try:
            if mode == "deny-connect":
                with socket.socket() as connection:
                    connection.connect(("127.0.0.1", 9))
            else:
                socket.getaddrinfo("example.invalid", 443)
        except RuntimeError:
            pass
        assert not attempts, "swallowed network attempt must still fail"
    if mode in {"cache-valid", "cache-unavailable", "cache-mutated", "default-unavailable"}:
        cache_contract()
        assert "litellm" not in sys.modules and not attempts and not sdk_attempts
        assert policy_proof() == source_before
        return dict(cache_contract_passed=True, network_attempts=attempts, sdk_imported=False)
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
    assert not attempts and not sdk_attempts
    assert {key: os.environ.get(key) for key in cache_keys} == environment_before
    assert policy_proof() == source_before
    result.update(network_attempts=attempts, sdk_imported=False, environment_unchanged=True)
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(run(), ensure_ascii=False))
    except BaseException as error:
        print(json.dumps(dict(error_type=type(error).__name__, network_attempts=attempts,
                              sdk_imported="litellm" in sys.modules, sdk_attempts=sdk_attempts,
                              frames=[dict(file=frame.filename, line=frame.lineno, function=frame.name)
                                      for frame in traceback.extract_tb(error.__traceback__)])))
        raise
