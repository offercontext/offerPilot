"""固定 Knowledge tokenizer 的失败语义。"""

from __future__ import annotations

import pytest

import offerpilot.knowledge.tokenizer as tokenizer


def test_count_tokens_fails_fast_when_cl100k_base_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokenizer, "_load_encoding", lambda: None)

    with pytest.raises(tokenizer.TokenizerUnavailableError, match="tokenizer_unavailable"):
        tokenizer.count_tokens("需要精确 token 计数的正文")


def test_tokenizer_status_does_not_report_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokenizer, "_load_encoding", lambda: None)

    with pytest.raises(tokenizer.TokenizerUnavailableError, match="tokenizer_unavailable"):
        tokenizer.tokenizer_status()
