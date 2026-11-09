"""Fixed tokenizer resources must not depend on eager SDK import or a warm TEMP."""
from __future__ import annotations

import hashlib
from importlib.metadata import distribution
import json
import os
from pathlib import Path
import shutil
import socket
import threading
import subprocess
import sys

import pytest

from offerpilot.knowledge import tokenizer

KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CACHE_VARIABLES = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR", "CUSTOM_TIKTOKEN_CACHE_DIR")


@pytest.fixture(autouse=True)
def isolated_encoding(monkeypatch: pytest.MonkeyPatch):
    loader = tokenizer._load_encoding
    loader.cache_clear()
    for variable in CACHE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    yield
    loader.cache_clear()


@pytest.fixture
def valid_cache(tmp_path: Path) -> Path:
    resource = Path(distribution("litellm").locate_file("litellm/litellm_core_utils/tokenizers/" + KEY))
    assert hashlib.sha256(resource.read_bytes()).hexdigest() == SHA
    destination = tmp_path / "valid-cache"
    destination.mkdir()
    shutil.copyfile(resource, destination / KEY)
    return destination


@pytest.mark.parametrize("variable", CACHE_VARIABLES)
def test_explicit_cache_is_read_only_and_not_replaced(variable, valid_cache, tmp_path):
    result = _probe(tmp_path, "cache-valid", cache=valid_cache, cache_variable=variable)
    assert result["cache_contract_passed"] and result["network_attempts"] == []


@pytest.mark.parametrize(("variable", "state"), [
    (variable, state) for variable in CACHE_VARIABLES for state in ("missing", "corrupt", "disabled")
    if variable != "CUSTOM_TIKTOKEN_CACHE_DIR" or state != "disabled"
])
def test_explicit_unavailable_cache_never_downloads_or_falls_back(state, variable, valid_cache, tmp_path):
    result = _probe(tmp_path, "cache-unavailable", cache=valid_cache,
                    cache_variable=variable, state=state)
    assert result["cache_contract_passed"] and result["network_attempts"] == []


def test_native_cache_priority_does_not_depend_on_sdk_import(valid_cache, tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(valid_cache))
    monkeypatch.setenv("DATA_GYM_CACHE_DIR", str(tmp_path / "missing-data"))
    monkeypatch.setenv("CUSTOM_TIKTOKEN_CACHE_DIR", str(tmp_path / "missing-custom"))
    assert tokenizer.count_tokens("hello world").count == 2


def test_data_cache_precedes_custom_cache(valid_cache, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GYM_CACHE_DIR", str(valid_cache))
    monkeypatch.setenv("CUSTOM_TIKTOKEN_CACHE_DIR", str(tmp_path / "missing-custom"))
    assert tokenizer.count_tokens("hello world").count == 2


def test_empty_custom_cache_keeps_default_offline_resource(monkeypatch):
    monkeypatch.setenv("CUSTOM_TIKTOKEN_CACHE_DIR", "")
    assert tokenizer.count_tokens("hello world").count == 2
    assert os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] == ""
    assert "TIKTOKEN_CACHE_DIR" not in os.environ


def test_encoding_uses_verified_bytes_when_resource_changes_after_read(valid_cache, tmp_path):
    result = _probe(tmp_path, "cache-mutated", cache=valid_cache)
    assert result["cache_contract_passed"] and result["network_attempts"] == []


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_default_resource_missing_or_corrupt_fails_closed(state, tmp_path):
    result = _probe(tmp_path, "default-unavailable", state=state)
    assert result["cache_contract_passed"] and result["network_attempts"] == []


def _probe(tmp_path: Path, mode: str, *, cache: Path | None = None,
           cache_variable: str = "TIKTOKEN_CACHE_DIR", state: str | None = None,
           expect_failure: bool = False) -> dict:
    root = Path(__file__).resolve().parents[1]
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
    temporary = tmp_path / (mode + "-temp")
    temporary.mkdir()
    env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
               PYTHONDONTWRITEBYTECODE="1", LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_TELEMETRY="False")
    if cache is not None:
        env[cache_variable] = str(cache)
    env["KNOWLEDGE_TOKENIZER_PROBE"] = json.dumps(dict(mode=mode, data=str(tmp_path / (mode + "-data")),
        cache=str(cache) if cache is not None else None, cache_variable=cache_variable, state=state))
    prefix = tmp_path / (mode + "-source-prefix")
    result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-X", "pycache_prefix="+str(prefix),
                             str(root / "scripts/evaluation_subject/source_launcher.py"),
                             json.dumps(dict(checkout=str(root), evaluator=str(root),
                                             bridge=str(Path(__file__).with_name("_knowledge_tokenizer_offline.py"))))],
                            env=env, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    if expect_failure:
        assert result.returncode != 0, (result.stdout, result.stderr)
    else:
        assert result.returncode == 0, (result.stdout, result.stderr)
    assert not prefix.exists()
    assert not (temporary / "data-gym-cache").exists()
    return json.loads(result.stdout)


def test_knowledge_ingest_without_sdk_or_network(tmp_path):
    result = _probe(tmp_path, "product")
    assert result["ingest_extraction_and_limit_passed"] is True
    assert result["sdk_imported"] is False and result["network_attempts"] == []
    assert result["environment_unchanged"] is True


def test_fixed_encoding_matches_official_token_ids_and_special_token_rules(tmp_path, valid_cache):
    official = _probe(tmp_path, "official", cache=valid_cache)
    product = _probe(tmp_path, "encoding")
    for field in ("ids", "rejections", "special_ids", "boundary_counts"):
        assert product[field] == official[field]


@pytest.mark.parametrize(("mode", "event"), [("deny-connect", "socket.connect"),
                                             ("deny-dns", "socket.getaddrinfo")])
def test_fresh_probe_rejects_even_swallowed_network_attempts(tmp_path, mode, event):
    result = _probe(tmp_path, mode, expect_failure=True)
    assert result["error_type"] == "AssertionError"
    assert result["sdk_imported"] is False
    assert [attempt["event"] for attempt in result["network_attempts"]] == [event]
    assert result["network_attempts"][0]["frames"]


def test_cache_probe_isolated_from_unrelated_parent_socket_activity(tmp_path, valid_cache):
    stop = threading.Event()
    active = threading.Event()
    errors = []

    def background():
        try:
            while not stop.is_set():
                # On Windows socketpair uses a real loopback connect for the
                # event-loop wakeup pipe. This activity is not tokenizer I/O.
                first, second = socket.socketpair()
                first.close()
                second.close()
                socket.getaddrinfo("localhost", 0)
                active.set()
                stop.wait(0.01)
        except BaseException as error:
            errors.append(error)
            active.set()

    worker = threading.Thread(target=background)
    worker.start()
    try:
        assert active.wait(5) and not errors
        result = _probe(tmp_path, "cache-valid", cache=valid_cache)
        assert result["cache_contract_passed"] and result["network_attempts"] == []
    finally:
        stop.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and not errors


def test_fresh_probe_rejects_even_swallowed_sdk_import_attempt(tmp_path):
    result = _probe(tmp_path, "deny-sdk", expect_failure=True)
    assert result["error_type"] == "AssertionError"
    assert result["sdk_imported"] is False and result["sdk_attempts"] == ["litellm"]
    assert result["network_attempts"] == []
